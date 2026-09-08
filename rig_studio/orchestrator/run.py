"""Combined run state machine, replicating the MATLAB record_cameras_show_stimulus
timeline: arm -> trigger -> start gate (all cams streaming) -> preroll -> absolute
POSIX write gate -> baseline (black) -> grating -> timed stop -> reports.

Plain synchronous class; the GUI runs it on a QThread and receives state via the
on_state callback. abort() is safe from any thread.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from rig_studio.config import RigConfig
from rig_studio.recorder.supervisor import RecorderSupervisor
from rig_studio.stimulus.client import StimulusClient
from rig_studio.trigger.digilent import DigilentClock

log = logging.getLogger(__name__)

MODES = ("record", "stimulus", "both")


class RunAborted(RuntimeError):
    pass


class RunController:
    def __init__(
        self,
        config: RigConfig,
        supervisor: RecorderSupervisor | None,
        stimulus: StimulusClient | None,
        trigger: DigilentClock | None,
        on_state: Callable[[str, dict], None] | None = None,
        stimulus_starter: Callable[[], None] | None = None,
        record_positions: set[int] | None = None,
    ) -> None:
        self.config = config
        self.supervisor = supervisor
        self.stimulus = stimulus
        self.trigger = trigger  # None => externally driven (WaveForms GUI)
        self._on_state = on_state or (lambda state, info: None)
        self._starter = stimulus_starter  # lazily boots the host on the run thread
        self._abort = threading.Event()
        self.record_positions = None if record_positions is None else frozenset(record_positions)
        self._armed_positions: set[int] = set()

    def abort(self) -> None:
        self._abort.set()

    def _state(self, state: str, **info) -> None:
        log.info("run state: %s %s", state, info or "")
        self._on_state(state, info)

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise RunAborted("aborted by user")

    def _sleep_until(self, deadline_posix: float) -> None:
        while time.time() < deadline_posix:
            self._check_abort()
            if self.supervisor is not None:
                self.supervisor.poll_events()
            time.sleep(min(0.05, max(0.001, deadline_posix - time.time())))

    def run(self, base: str, duration_s: float, mode: str = "both",
            frames_only_count: int | None = None) -> dict:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode in ("both", "stimulus"):
            if self.stimulus is not None and not self.stimulus.alive and self._starter:
                self._state("STIM_HOST", detail="starting stimulus host")
                self._starter()  # blocking is fine: we are on the run thread
            if self.stimulus is None or not self.stimulus.alive:
                raise RuntimeError("stimulus host is not running — start it in the "
                                   "Stimulus panel (or check the projector), then retry")
        self._abort.clear()
        rec = self.config.recording
        stim_cfg = self.config.stimulus
        report: dict = {"base": base, "mode": mode, "duration_s": duration_s}
        try:
            if mode == "stimulus":
                return self._run_stimulus_only(base, duration_s, report)
            if self.supervisor is None:
                raise RuntimeError("camera supervisor is not connected")
            targets = (set(self.supervisor.workers) if self.record_positions is None
                       else set(self.record_positions))
            if not targets:
                raise RuntimeError("select at least one camera in the Record column")
            unavailable = targets - set(self.supervisor.workers)
            if unavailable:
                raise RuntimeError(f"recording cameras are not active: {sorted(unavailable)}")
            self._armed_positions = targets
            report["camera_positions"] = sorted(targets)
            # PREPARING: files open, acquisition running, gate closed
            self._state("PREPARING", base=base)
            paths = self.supervisor.arm_all(
                base, duration_s, wait=False, positions=targets,
                fps_nominal=rec.fps_nominal,
                frames_only_count=frames_only_count,
            )
            report["paths"] = paths
            self._check_abort()
            # TRIGGER
            if self.trigger is not None and self.trigger.owned:
                self._state("TRIGGER", ownership="app")
                self.trigger.configure(rec.fps_nominal, self.config.trigger.duty)
                self.trigger.start()
            else:
                self._state("TRIGGER", ownership="external")
                if self.supervisor.backend_kind == "sim":
                    self.supervisor.set_sim_trigger(True, rec.fps_nominal)
            # START GATE: every camera streaming
            try:
                self.supervisor.wait_armed(positions=targets)
            except TimeoutError as error:
                raise RuntimeError(
                    "no trigger detected: cameras produced no frames — is the "
                    f"{rec.fps_nominal:g} Hz clock running (Digilent/WaveForms)?") from error
            self._state("PREROLL", seconds=rec.preroll_s)
            self._sleep_until(time.time() + rec.preroll_s)
            # WRITE GATE (absolute POSIX broadcast, same instant on all workers)
            gate_ns = self.supervisor.open_gate(positions=targets)
            gate_s = gate_ns / 1e9
            report["gate_posix"] = gate_s
            self._state("GATE", gate_posix=gate_s)
            if mode == "both" and self.stimulus is not None:
                stim_onset = gate_s + stim_cfg.baseline_s
                log_path = self._stim_log_path(base)
                report["stim_log"] = str(log_path)
                self.stimulus.arm(stim_cfg.grating, circles=stim_cfg.circles,
                                  start_posix=stim_onset,
                                  grating_s=stim_cfg.grating_s,
                                  log_path=log_path, base=base)
                self._sleep_until(gate_s + stim_cfg.baseline_s)
                self._state("STIMULUS", onset_posix=stim_onset,
                            grating_s=stim_cfg.grating_s)
            else:
                self._state("BASELINE")
            # record until the requested duration past the gate
            self._sleep_until(gate_s + duration_s)
            self._state("DRAIN")
            report["camera_reports"] = self._teardown_recording()
            if mode == "both" and self.stimulus is not None:
                report["stim_status"] = self.stimulus.status()
            self._state("DONE", **{})
            return report
        except RunAborted:
            self._state("ABORTED")
            report["aborted"] = True
            report["camera_reports"] = self._teardown_recording()
            return report
        except Exception as error:
            self._state("ERROR", error=str(error))
            report["error"] = str(error)
            self._teardown_recording()
            raise

    def _stim_log_path(self, base: str) -> Path:
        position = min(self.record_positions) if self.record_positions else 0
        root = self.config.by_position(position).output_root
        return Path(root) / f"stim_log_{base}.mat"

    def _teardown_recording(self) -> dict | None:
        reports = None
        positions, self._armed_positions = self._armed_positions, set()
        if self.supervisor is not None and positions:
            try:
                reports = self.supervisor.stop_recording_all(positions=positions)
            except Exception as error:  # noqa: BLE001
                log.error("stop_recording failed: %s", error)
            if self.supervisor.backend_kind == "sim":
                self.supervisor.set_sim_trigger(False)
        if self.stimulus is not None and self.stimulus.alive:
            try:
                self.stimulus.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.trigger is not None and self.trigger.owned:
            try:
                self.trigger.stop()
            except Exception:  # noqa: BLE001
                pass
        return reports

    def _run_stimulus_only(self, base: str, duration_s: float, report: dict) -> dict:
        if self.stimulus is None:
            raise RuntimeError("stimulus host is not running")
        start = time.time() + 1.0
        log_path = self._stim_log_path(base)
        report["stim_log"] = str(log_path)
        self.stimulus.arm(self.config.stimulus.grating,
                          circles=self.config.stimulus.circles, start_posix=start,
                          grating_s=duration_s, log_path=log_path, base=base)
        self._state("STIMULUS", onset_posix=start, grating_s=duration_s)
        self._sleep_until(start + duration_s + 1.0)
        report["stim_status"] = self.stimulus.wait_state({"done", "error"}, 10.0)
        self._state("DONE")
        return report
