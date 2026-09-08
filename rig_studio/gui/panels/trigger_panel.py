from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import QProcess, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from rig_studio.config import RigConfig
from rig_studio.trigger.digilent import DigilentClock

log = logging.getLogger(__name__)


class _RateTestThread(QThread):
    finished_rates = Signal(dict)
    failed = Signal(str)

    def __init__(self, supervisor, clock: DigilentClock | None,
                 requested_hz: float, duty: float, external: bool) -> None:
        super().__init__()
        self.supervisor = supervisor
        self.clock = clock
        self.requested_hz = float(requested_hz)
        self.duty = float(duty)
        self.external = bool(external)

    def run(self) -> None:
        states = {}
        clock_was_running = False
        clock_plan = None
        result = None
        failure = None
        try:
            targets = set(self.supervisor.workers)
            self.supervisor.broadcast({"cmd": "capture_state"}, targets)
            states = self.supervisor.wait_for("capture_state", targets, 5.0)
            recording = sorted(position for position, state in states.items()
                               if state.get("recording"))
            if recording:
                raise RuntimeError(
                    f"rate test is unavailable while cam{recording} are recording")
            if not self.external and self.clock is not None:
                clock_was_running = bool(self.clock.running)
                clock_plan = self.clock.plan
            self.supervisor.broadcast({"cmd": "stop_acquisition"}, targets)
            self.supervisor.wait_for("acquisition", targets, 10.0)
            self.supervisor.broadcast({"cmd": "preview_mode", "free_run": False}, targets)
            self.supervisor.wait_for("preview_mode", targets, 10.0)
            if not self.external:
                if self.clock is None or not self.clock.owned:
                    raise RuntimeError("open the Digilent device first")
                self.clock.configure(self.requested_hz, self.duty)
                self.clock.start()
            self.supervisor.broadcast({"cmd": "start_acquisition"}, targets)
            self.supervisor.wait_for("acquisition", targets, 10.0)
            result = self.supervisor.measure_rates(3.0, targets)
        except Exception as error:  # noqa: BLE001
            failure = str(error)
        finally:
            if states:
                try:
                    targets = set(states)
                    self.supervisor.broadcast({"cmd": "stop_acquisition"}, targets)
                    self.supervisor.wait_for("acquisition", targets, 10.0)
                    free_run = {position for position, state in states.items()
                                if state.get("free_run")}
                    triggered = targets - free_run
                    for positions, mode in ((free_run, True), (triggered, False)):
                        if positions:
                            self.supervisor.broadcast(
                                {"cmd": "preview_mode", "free_run": mode}, positions)
                            self.supervisor.wait_for("preview_mode", positions, 10.0)
                    acquiring = {position for position, state in states.items()
                                 if state.get("acquiring")}
                    if acquiring:
                        self.supervisor.broadcast({"cmd": "start_acquisition"}, acquiring)
                        self.supervisor.wait_for("acquisition", acquiring, 10.0)
                    if not self.external and self.clock is not None:
                        if clock_was_running:
                            if clock_plan is not None:
                                self.clock.configure(
                                    clock_plan.achieved_fps,
                                    clock_plan.high_ticks / clock_plan.period_ticks)
                            self.clock.start()
                        else:
                            self.clock.stop()
                except Exception as restore_error:  # noqa: BLE001
                    detail = f"state restore failed: {restore_error}"
                    failure = f"{failure}; {detail}" if failure else detail
        if failure:
            self.failed.emit(failure)
        elif result is not None:
            self.finished_rates.emit(result)


class TriggerPanel(QWidget):
    """Digilent clock control. 'External' mode = you drive it from WaveForms
    (or MATLAB); the app then only verifies frames are arriving."""

    fps_changed = Signal(float)

    def __init__(self, config: RigConfig, main_window) -> None:
        super().__init__()
        self.config = config
        self.main_window = main_window
        self.clock: DigilentClock | None = None

        self.ownership = QComboBox()
        self.ownership.addItems(["app", "external"])
        self.ownership.setCurrentText(config.trigger.ownership)
        self.fps = QDoubleSpinBox(minimum=1.0, maximum=1000.0, decimals=2)
        self.fps.setValue(config.trigger.fps)
        self.duty = QDoubleSpinBox(minimum=0.05, maximum=0.95, decimals=2, singleStep=0.05)
        self.duty.setValue(config.trigger.duty)
        self.open_button = QPushButton("Open device")
        self.waveforms_button = QPushButton("Launch WaveForms")
        self.start_button = QPushButton("Start clock")
        self.stop_button = QPushButton("Stop clock")
        self.rate_button = QPushButton("Measure simultaneous rate (3 s)")
        self.sim_button = QPushButton("Toggle SIM clock")
        self.status = QLabel("closed")
        self.banner = QLabel("")
        self.banner.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Ownership", self.ownership)
        form.addRow("camera / recording Hz", self.fps)
        form.addRow("duty", self.duty)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.open_button)
        layout.addWidget(self.waveforms_button)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.rate_button)
        layout.addWidget(self.sim_button)
        layout.addWidget(self.status)
        layout.addWidget(self.banner)
        layout.addStretch(1)

        self.ownership.currentTextChanged.connect(self._mode_changed)
        self.open_button.clicked.connect(self._open)
        self.waveforms_button.clicked.connect(self._launch_waveforms)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        self.rate_button.clicked.connect(self._rate_test)
        self.sim_button.clicked.connect(self._toggle_sim)
        self.fps.valueChanged.connect(self.fps_changed.emit)
        self.fps.valueChanged.connect(lambda _value: self._mode_changed(
            self.ownership.currentText()))
        # a RUNNING app-owned clock retunes live when Hz/duty change (debounced
        # so typing digits doesn't glitch the trigger lines mid-edit)
        self._retune_timer = QTimer(self)
        self._retune_timer.setSingleShot(True)
        self._retune_timer.setInterval(400)
        self._retune_timer.timeout.connect(self._retune_running)
        self.fps.valueChanged.connect(lambda _value: self._retune_timer.start())
        self.duty.valueChanged.connect(lambda _value: self._retune_timer.start())
        self._sim_running = False
        self._rate_thread: _RateTestThread | None = None
        self._run_locked = False
        self._mode_changed(self.ownership.currentText())

    @property
    def external(self) -> bool:
        return self.ownership.currentText() == "external"

    def _mode_changed(self, mode: str) -> None:
        app_mode = mode == "app"
        self.ownership.setEnabled(not self._run_locked)
        self.fps.setEnabled(not self._run_locked)
        self.duty.setEnabled(not self._run_locked)
        self.waveforms_button.setEnabled(not self._run_locked)
        self.rate_button.setEnabled(not self._run_locked and not (
            self._rate_thread is not None and self._rate_thread.isRunning()))
        self.sim_button.setEnabled(not self._run_locked)
        for widget in (self.open_button, self.start_button, self.stop_button):
            widget.setEnabled(app_mode and not self._run_locked)
        self.banner.setText(
            "" if app_mode else
            f"Trigger owned EXTERNALLY — set WaveForms to {self.fps.value():g} Hz. "
            "Rig Studio still uses this Hz for file sizing and reports.")

    def _launch_waveforms(self) -> None:
        executable = Path(self.config.trigger.waveforms_exe)
        if not executable.is_file():
            self.status.setText(f"WaveForms not found: {executable}")
            return
        started = QProcess.startDetached(str(executable), [])
        ok = started[0] if isinstance(started, tuple) else bool(started)
        if ok:
            self.ownership.setCurrentText("external")
            self.status.setText("WaveForms launched — configure/start its clock")
        else:
            self.status.setText("WaveForms launch failed")

    def _open(self) -> None:
        if self.clock is not None:  # don't leak a half-open device per click
            self.clock.close()
        self.clock = DigilentClock(self.config.trigger)
        try:
            self.clock.open()
            self.status.setText("open (owned by app)")
        except RuntimeError as error:
            self.status.setText(str(error))
            self.ownership.setCurrentText("external")
            self.clock = None

    def _start(self) -> None:
        if self.clock is None or not self.clock.owned:
            self.status.setText("open the device first")
            return
        try:
            plan = self.clock.configure(self.fps.value(), self.duty.value())
            self.clock.start()
            self.status.setText(f"RUNNING {plan.achieved_fps:.3f} Hz "
                                f"(divider {plan.divider}, period {plan.period_ticks})")
        except Exception as error:  # noqa: BLE001
            self.status.setText(f"start failed: {error}")

    def _retune_running(self) -> None:
        """Push a changed Hz/duty into the app-owned clock while it runs."""
        if (self.external or self.clock is None or not self.clock.owned
                or not self.clock.running):
            return
        try:
            plan = self.clock.configure(self.fps.value(), self.duty.value())
            self.clock.start()  # re-Configure(True) applies the new settings
            self.status.setText(f"RUNNING {plan.achieved_fps:.3f} Hz (retuned live)")
        except Exception as error:  # noqa: BLE001
            self.status.setText(f"live retune failed: {error}")

    def _stop(self) -> None:
        if self.clock is not None:
            self.clock.stop()
            self.status.setText("stopped (DIOs held low)")

    def _toggle_sim(self) -> None:
        self._sim_running = not self._sim_running
        self.main_window.set_sim_trigger(self._sim_running)
        self.status.setText(f"SIM clock {'RUNNING' if self._sim_running else 'stopped'}")

    def _rate_test(self) -> None:
        if self._rate_thread is not None and self._rate_thread.isRunning():
            return
        supervisor = self.main_window.supervisor
        if supervisor is None:
            self.status.setText("connect cameras before measuring")
            return
        if self.external:
            self.status.setText("measuring external clock — make sure WaveForms is RUNNING")
        elif self.clock is None or not self.clock.owned:
            self.status.setText("open the Digilent device before measuring")
            return
        self.rate_button.setEnabled(False)
        self._rate_thread = _RateTestThread(
            supervisor, self.clock, self.fps.value(), self.duty.value(), self.external)
        self._rate_thread.finished_rates.connect(self._rate_done)
        self._rate_thread.failed.connect(self._rate_failed)
        self._rate_thread.finished.connect(self._rate_finished)
        self._rate_thread.start()

    def _rate_done(self, rates: dict) -> None:
        self._mode_changed(self.ownership.currentText())
        requested = self.fps.value()
        common = min(rates.values()) if rates else 0.0
        recommended = requested if common >= requested * 0.98 else max(1.0, math.floor(common * 0.95))
        detail = ", ".join(f"cam{position} {rate:.1f}" for position, rate in sorted(rates.items()))
        self.status.setText(
            f"measured: {detail} Hz | safe common setting: {recommended:g} Hz")

    def _rate_failed(self, error: str) -> None:
        self._mode_changed(self.ownership.currentText())
        self.status.setText(f"rate test failed: {error}")

    def _rate_finished(self) -> None:
        self._rate_thread = None
        self._mode_changed(self.ownership.currentText())

    def set_running(self, running: bool) -> None:
        self._run_locked = bool(running)
        self._retune_timer.stop()
        self._mode_changed(self.ownership.currentText())

    def close_clock(self) -> None:
        if self.clock is not None:
            self.clock.close()
            self.clock = None
