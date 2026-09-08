from __future__ import annotations

import json
import math
import shutil

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from rig_studio.config import RigConfig
from rig_studio.orchestrator.run import RunController
from rig_studio.recording.session import max_frames, session_tag


class _RunThread(QThread):
    state_changed = Signal(str, dict)
    finished_report = Signal(dict)
    failed = Signal(str)

    def __init__(self, controller: RunController, base: str, duration: float,
                 mode: str, frames_only: bool = False,
                 export_count: int = 0) -> None:
        super().__init__()
        self.controller = controller
        self.controller._on_state = lambda s, i: self.state_changed.emit(s, i)
        self.base = base
        self.duration = duration
        self.mode = mode
        self.frames_only = bool(frames_only)
        self.export_count = int(export_count)

    def run(self) -> None:
        try:
            report = self.controller.run(
                self.base, self.duration, self.mode,
                frames_only_count=(self.export_count if self.frames_only else None))
            self.finished_report.emit(report)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class RunPanel(QWidget):
    """Record / Stimulus / Record+Stimulus orchestration (the MATLAB timeline)."""

    def __init__(self, config: RigConfig, main_window) -> None:
        super().__init__()
        self.config = config
        self.main_window = main_window
        self.thread: _RunThread | None = None

        self.mode = QComboBox()
        self.mode.addItems(["both", "record", "stimulus"])
        self.base = QLineEdit("run")
        self.duration = QDoubleSpinBox(minimum=0.5, maximum=36000.0, decimals=1)
        self.duration.setValue(config.stimulus.grating_s + config.stimulus.baseline_s)
        self.frames_label = QLabel("")
        self.disk_label = QLabel("")
        self.disk_label.setWordWrap(True)
        self.frames_only = QCheckBox(
            "Save lossless raw PNG frames only (do not keep HDF5)")
        self.export_count = QSpinBox(minimum=0, maximum=1_000_000, value=60)
        self.export_count.setSpecialValueText("all frames")
        self.export_count.setToolTip(
            "Frames are sampled evenly across the run; 0 saves every frame.")
        self.start = QPushButton("START RUN")
        self.abort = QPushButton("ABORT")
        self.abort.setEnabled(False)
        self.state = QLabel("idle")
        self.state.setStyleSheet("font-weight: bold;")
        self.progress = QProgressBar()
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)

        form = QFormLayout()
        form.addRow("mode", self.mode)
        form.addRow("base name", self.base)
        form.addRow("record duration (s)", self.duration)
        form.addRow("max frames", self.frames_label)
        form.addRow(self.frames_only)
        form.addRow("PNG frames per camera", self.export_count)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.disk_label)
        layout.addWidget(self.start)
        layout.addWidget(self.abort)
        layout.addWidget(self.state)
        layout.addWidget(self.progress)
        layout.addWidget(self.report, 1)

        self.duration.valueChanged.connect(self._update_estimates)
        self.mode.currentTextChanged.connect(self._update_estimates)
        self.frames_only.toggled.connect(self._update_estimates)
        self.export_count.valueChanged.connect(self._update_estimates)
        self.start.clicked.connect(self._start)
        self.abort.clicked.connect(self._abort)
        self._update_estimates()

    def _update_estimates(self) -> None:
        rec = self.config.recording
        fps = self.main_window.desired_fps()
        frames = max_frames(self.duration.value(), fps,
                            rec.max_frames_overhead)
        if self.mode.currentText() == "stimulus":
            self.frames_label.setText("—")
            self.disk_label.setText("No camera files in stimulus-only mode")
            return
        frames_only = self.frames_only.isChecked()
        output_frames = (math.ceil(self.duration.value() * fps)
                         if self.export_count.value() == 0
                         else min(self.export_count.value(),
                                  math.ceil(self.duration.value() * fps)))
        self.frames_label.setText(
            f"{output_frames} PNGs/camera" if frames_only else str(frames))
        positions = self.main_window.selection_panel.record_positions()
        if not positions:
            self.disk_label.setText("No recording cameras selected")
            return
        lines = []
        for position in sorted(positions):
            cam = self.config.by_position(position)
            need_gb = ((output_frames if frames_only else frames)
                       * self.main_window.frame_bytes(position) / 1e9)
            try:
                free_gb = shutil.disk_usage(cam.output_root).free / 1e9
                warn = "  ⚠ LOW" if free_gb < 1.2 * need_gb else ""
                kind = "PNG estimate" if frames_only else "HDF5"
                lines.append(f"cam{cam.position} {cam.output_root} ({kind}): "
                             f"need {need_gb:.1f} GB, free {free_gb:.0f} GB{warn}")
            except OSError:
                lines.append(f"cam{cam.position} {cam.output_root}: NOT FOUND")
        self.disk_label.setText("\n".join(lines))

    def _start(self) -> None:
        if self.thread is not None and self.thread.isRunning():
            return
        mode = self.mode.currentText()
        if mode != "stimulus" and not self.main_window.selection_panel.record_positions():
            self.state.setText("select at least one camera in Record")
            return
        base = f"{self.base.text().strip() or 'run'}_{session_tag()}"
        controller = self.main_window.build_run_controller()
        if controller is None:
            self.state.setText("not connected (open cameras first)")
            return
        self.thread = _RunThread(
            controller, base, self.duration.value(), mode,
            frames_only=self.frames_only.isChecked(),
            export_count=self.export_count.value())
        self.thread.state_changed.connect(self._on_state)
        self.thread.finished_report.connect(self._on_finished)
        self.thread.failed.connect(self._on_failed)
        self.report.clear()
        self.start.setEnabled(False)
        self.frames_only.setEnabled(False)
        self.export_count.setEnabled(False)
        self.abort.setEnabled(True)
        if mode != "stimulus":
            self.main_window.selection_panel.set_running(True)
            self.main_window.trigger_panel.set_running(True)
        self.progress.setRange(0, 0)  # busy
        self.thread.start()

    def _abort(self) -> None:
        if self.thread is not None:
            self.thread.controller.abort()

    def _on_state(self, state: str, info: dict) -> None:
        self.state.setText(f"{state} {info if info else ''}")

    def _on_finished(self, report: dict) -> None:
        self.report.setPlainText(json.dumps(report, indent=2, default=str))
        self._done()

    def _on_failed(self, error: str) -> None:
        self.report.setPlainText(f"RUN FAILED:\n{error}")
        self._done()

    def _done(self) -> None:
        self.start.setEnabled(True)
        self.frames_only.setEnabled(True)
        self.export_count.setEnabled(True)
        self.abort.setEnabled(False)
        self.main_window.selection_panel.set_running(False)
        self.main_window.trigger_panel.set_running(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._update_estimates()
