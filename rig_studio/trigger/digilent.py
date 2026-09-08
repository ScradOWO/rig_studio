"""Digilent Digital Discovery camera clock — port of DwfTriggerController
(flir_camera_helper_c.cpp). Divider math is transcribed verbatim:

    divider = 1
    while round(clk_hz / divider / fps) > counter_max and divider < 1e8: divider *= 10
    period_ticks = round(clk_hz / divider / fps)          # must be >= 2
    high_ticks   = clamp(round(period_ticks * duty), 1, period_ticks - 1)

Channels are DigitalOut Pulse, idle Low, run forever; stop() drives every used
DIO low so the cameras never see a floating trigger line. Ownership is
optional: open() reports owned=False when WaveForms (or MATLAB) holds the
device, and the app then treats the trigger as externally driven.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from rig_studio.config import TriggerCfg

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClockPlan:
    divider: int
    period_ticks: int
    high_ticks: int
    achieved_fps: float


def plan_clock(clk_hz: float, fps: float, duty: float, counter_max: int) -> ClockPlan:
    """Pure divider math (MEX configureClock178), unit-testable without hardware."""
    if fps <= 0 or not 0.0 < duty < 1.0:
        raise ValueError(f"bad fps/duty: {fps}, {duty}")
    divider = 1
    while round(clk_hz / divider / fps) > counter_max and divider < 100_000_000:
        divider *= 10
    period_ticks = round(clk_hz / divider / fps)
    if period_ticks < 2:
        raise ValueError(f"fps {fps} too high for clock {clk_hz} (period < 2 ticks)")
    high_ticks = min(max(round(period_ticks * duty), 1), period_ticks - 1)
    return ClockPlan(
        divider=divider,
        period_ticks=period_ticks,
        high_ticks=high_ticks,
        achieved_fps=clk_hz / divider / period_ticks,
    )


class DigilentClock:
    """App-owned trigger clock via pydwf. All methods raise RuntimeError with a
    readable message when the WaveForms SDK or device is unavailable."""

    def __init__(self, cfg: TriggerCfg) -> None:
        self._cfg = cfg
        self._dwf = None
        self._device = None
        self.owned = False
        self.running = False
        self.plan: ClockPlan | None = None

    def open(self) -> str:
        try:
            from pydwf import DwfLibrary
            from pydwf.utilities import openDwfDevice
        except ImportError as error:
            raise RuntimeError("pydwf is not installed") from error
        self._dwf = DwfLibrary()
        # release any device this process already half-claimed (a failed open
        # leaves the Discovery wedged for every later attempt in this process)
        try:
            self._dwf.deviceControl.closeAll()
        except Exception:  # noqa: BLE001 — API name varies across pydwf versions
            try:
                self._dwf.deviceCloseAll()
            except Exception:  # noqa: BLE001
                pass
        # Explicit-configure mode, like the MEX (FDwfDeviceAutoConfigureSet 0).
        try:
            self._device = openDwfDevice(self._dwf)
            self._device.autoConfigureSet(0)
        except Exception as error:  # noqa: BLE001 — "device already opened" etc.
            self._device = None
            self.owned = False
            message = str(error)
            log.info("Digilent open failed (%s); treating trigger as EXTERNAL", message)
            raise RuntimeError(f"trigger device unavailable: {message}") from error
        self.owned = True
        return "Digilent device opened"

    def configure(self, fps: float | None = None, duty: float | None = None) -> ClockPlan:
        if not self.owned or self._device is None:
            raise RuntimeError("trigger device is not owned by the app")
        fps = float(fps if fps is not None else self._cfg.fps)
        duty = float(duty if duty is not None else self._cfg.duty)
        out = self._device.digitalOut
        clk_hz = float(out.internalClockInfo())
        first = self._cfg.channels_by_position[0]
        try:
            _, counter_max = out.counterInfo(first)
            counter_max = int(counter_max)
        except Exception:  # noqa: BLE001 — MEX fallback
            counter_max = 32767
            log.warning("counterInfo failed; using fallback counter_max=32767")
        plan = plan_clock(clk_hz, fps, duty, counter_max)
        from pydwf import DwfDigitalOutIdle, DwfDigitalOutType

        low_ticks = plan.period_ticks - plan.high_ticks
        for channel in self._cfg.channels_by_position:
            if not 0 <= channel <= 31:
                raise ValueError(f"DIO channel out of range: {channel}")
            out.enableSet(channel, True)
            out.typeSet(channel, DwfDigitalOutType.Pulse)
            out.idleSet(channel, DwfDigitalOutIdle.Low)
            out.dividerSet(channel, plan.divider)
            out.counterSet(channel, low_ticks, plan.high_ticks)
        out.runSet(0.0)     # forever
        out.waitSet(0.0)
        out.repeatSet(0)
        self.plan = plan
        log.info("trigger configured: %.4f Hz (divider %d, period %d, high %d)",
                 plan.achieved_fps, plan.divider, plan.period_ticks, plan.high_ticks)
        return plan

    def start(self) -> None:
        if not self.owned or self._device is None:
            raise RuntimeError("trigger device is not owned by the app")
        # release the stop()-time DigitalIO low-override, else it keeps the
        # pins pinned low and the restarted clock produces no edges
        io = self._device.digitalIO
        io.outputEnableSet(0)
        io.configure()
        self._device.digitalOut.configure(True)
        self.running = True

    def stop(self) -> None:
        """Stop and force every used DIO low (MEX stop path)."""
        if not self.owned or self._device is None:
            return
        out = self._device.digitalOut
        io = self._device.digitalIO
        out.configure(False)
        io.reset()
        mask = 0
        for channel in self._cfg.channels_by_position:
            mask |= 1 << channel
        io.outputEnableSet(mask)
        io.outputSet(0)
        io.configure()
        self.running = False

    def close(self) -> None:
        if self._device is not None:
            try:
                self.stop()
                self._device.close()
            except Exception:  # noqa: BLE001
                pass
        self._device = None
        self.owned = False
        self.running = False
