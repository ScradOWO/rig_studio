from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QDockWidget, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QScrollArea, QToolBar, QVBoxLayout, QWidget,
)

from rig_studio.config import RigConfig
from rig_studio.gui.panels.camera_grid import CameraGrid
from rig_studio.gui.panels.camera_selection import CameraSelectionPanel
from rig_studio.gui.panels.camera_settings import CameraSettingsPanel
from rig_studio.gui.panels.log_console import LogConsole
from rig_studio.gui.panels.nodemap_browser import NodemapBrowser
from rig_studio.gui.panels.run_panel import RunPanel
from rig_studio.gui.panels.stimulus_panel import StimulusPanel
from rig_studio.gui.panels.trigger_panel import TriggerPanel
from rig_studio.orchestrator.run import RunController
from rig_studio.recorder.previewbus import PreviewReader, shm_name
from rig_studio.recorder.supervisor import RecorderSupervisor

log = logging.getLogger(__name__)


class _ConnectThread(QThread):
    done = Signal(object, str)
    progress = Signal(str)

    def __init__(self, config: RigConfig, state_dir: Path, positions: set[int]) -> None:
        super().__init__()
        self.config = config
        self.state_dir = state_dir
        self.positions = set(positions)

    def run(self) -> None:
        supervisor = None
        try:
            supervisor = RecorderSupervisor(self.config, state_dir=self.state_dir)
            self.progress.emit(f"spawning {len(self.positions)} camera workers…")
            supervisor.start(positions=self.positions)
            self.progress.emit("configuring cameras (snapshot + trigger/stream setup)…")
            supervisor.configure_all()
            self.done.emit(supervisor, "")
        except Exception as error:  # noqa: BLE001
            # a half-connected rig must not linger: workers restore + exit
            if supervisor is not None:
                try:
                    self.progress.emit("connect failed — restoring cameras…")
                    supervisor.shutdown()
                except Exception:  # noqa: BLE001
                    pass
            self.done.emit(None, str(error))


class _DisconnectThread(QThread):
    done = Signal()

    def __init__(self, supervisor: RecorderSupervisor) -> None:
        super().__init__()
        self.supervisor = supervisor

    def run(self) -> None:
        try:
            self.supervisor.shutdown()
        except Exception as error:  # noqa: BLE001
            log.error("disconnect: %s", error)
        self.done.emit()


class _CamWindow(QWidget):
    """Standalone camera-view window; closing it re-docks the grid."""

    def __init__(self, on_close) -> None:
        super().__init__()
        self._on_close = on_close
        self.setWindowTitle("Cameras — Rig Studio")
        self.resize(1280, 820)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._on_close()
        super().closeEvent(event)


class _SingleCamWindow(QWidget):
    """Selected-camera view with explicit fit and preview-pixel 1:1 modes."""

    def __init__(self, position: int, on_close) -> None:
        super().__init__()
        self.position = position
        self._on_close = on_close
        self._image = None
        self._fit = True
        self.setWindowTitle(f"cam{position} — Rig Studio")
        self.resize(1000, 850)
        self.image_label = QLabel("no captured frame yet")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.image_label)
        self.scroll.setWidgetResizable(True)
        fit = QPushButton("Fit")
        one = QPushButton("1:1 preview pixels")
        fit.clicked.connect(lambda: self.set_fit(True))
        one.clicked.connect(lambda: self.set_fit(False))
        buttons = QHBoxLayout()
        buttons.addWidget(fit)
        buttons.addWidget(one)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.scroll, 1)

    def set_fit(self, fit: bool) -> None:
        self._fit = bool(fit)
        self._render()

    def update_image(self, image) -> None:
        self._image = image.copy()
        self._render()

    def _render(self) -> None:
        if self._image is None:
            return
        from PySide6.QtGui import QImage, QPixmap

        height, width = self._image.shape
        qimage = QImage(self._image.data, width, height, width, QImage.Format_Grayscale8)
        pixmap = QPixmap.fromImage(qimage)
        if self._fit:
            pixmap = pixmap.scaled(self.scroll.viewport().size(), Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
        self.image_label.setPixmap(pixmap)
        self.image_label.resize(pixmap.size())

    def closeEvent(self, event) -> None:  # noqa: N802
        self._on_close(self.position)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, config: RigConfig, state_dir: Path) -> None:
        super().__init__()
        self.config = config
        self.state_dir = state_dir
        self.supervisor: RecorderSupervisor | None = None
        self.readers: dict[int, PreviewReader] = {}
        self.camera_sizes = {
            cam.position: (cam.roi.width, cam.roi.height) for cam in config.cameras
        }
        self.setWindowTitle(f"Rig Studio — {config.name} [{config.backend}]")
        self.resize(1500, 950)

        self.grid = CameraGrid(config)
        self.setCentralWidget(self.grid)
        self.cam_window: _CamWindow | None = None
        self.single_cam_windows: dict[int, _SingleCamWindow] = {}
        self.selection_panel = CameraSelectionPanel(config)
        self.settings_panel = CameraSettingsPanel(
            config, self.send_to_worker,
            freeze_preview=self.grid.set_reference_frozen,
            crop_overlay=self.grid.set_crop_overlay,
            open_full_view=self.open_full_camera_view,
            display_options=self.grid.set_display_options,
        )
        self.nodemap_panel = NodemapBrowser(self.send_to_worker)
        self.trigger_panel = TriggerPanel(config, self)
        self.stimulus_panel = StimulusPanel(config, state_dir / "stimulus_status.json")
        self.run_panel = RunPanel(config, self)
        self.log_console = LogConsole()
        self.docks: dict[str, QDockWidget] = {}
        for title, widget, area in (
            ("Cameras", self.selection_panel, Qt.LeftDockWidgetArea),
            ("Stimulus", self.stimulus_panel, Qt.LeftDockWidgetArea),
            ("Run", self.run_panel, Qt.LeftDockWidgetArea),
            ("Trigger", self.trigger_panel, Qt.LeftDockWidgetArea),
            ("Camera Settings", self.settings_panel, Qt.RightDockWidgetArea),
            ("Nodemap", self.nodemap_panel, Qt.RightDockWidgetArea),
            ("Log", self.log_console, Qt.BottomDockWidgetArea),
        ):
            dock = QDockWidget(title, self)
            dock.setObjectName(title)
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            self.docks[title] = dock
        # tabbed groups keep the window tidy; drag any tab out to float it
        self.tabifyDockWidget(self.docks["Cameras"], self.docks["Run"])
        self.tabifyDockWidget(self.docks["Stimulus"], self.docks["Run"])
        self.tabifyDockWidget(self.docks["Run"], self.docks["Trigger"])
        self.tabifyDockWidget(self.docks["Camera Settings"], self.docks["Nodemap"])
        self.docks["Stimulus"].raise_()
        self.docks["Camera Settings"].raise_()
        toolbar = QToolBar("main")
        toolbar.setObjectName("main")
        self.addToolBar(toolbar)
        self.connect_action = QAction("Connect cameras", self)
        self.disconnect_action = QAction("Disconnect", self)
        self.preview_start = QAction("Start preview", self)
        self.preview_stop = QAction("Stop preview", self)
        self.clock_mode = QComboBox()
        self.clock_mode.addItems([f"Triggered ({config.trigger.fps:g} Hz)", "Free-run"])
        self.strip_action = QAction("Strip view", self)
        self.strip_action.setCheckable(True)
        self.popout_action = QAction("Pop out cameras", self)
        self.popout_action.setCheckable(True)
        self.status_label = QLabel(" disconnected ")
        toolbar.addAction(self.connect_action)
        toolbar.addAction(self.disconnect_action)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" preview clock: "))
        toolbar.addWidget(self.clock_mode)
        toolbar.addAction(self.preview_start)
        toolbar.addAction(self.preview_stop)
        toolbar.addSeparator()
        toolbar.addAction(self.strip_action)
        toolbar.addAction(self.popout_action)
        toolbar.addSeparator()
        toolbar.addWidget(self.status_label)

        self.connect_action.triggered.connect(self.connect_cameras)
        self.disconnect_action.triggered.connect(lambda: self.disconnect_cameras(False))
        self.preview_start.triggered.connect(self.start_preview)
        self.preview_stop.triggered.connect(self.stop_preview)
        self.strip_action.toggled.connect(self.grid.set_strip)
        self.popout_action.toggled.connect(self._toggle_cam_window)
        self.grid.tile_selected.connect(self.settings_panel.select_camera)
        self.grid.tile_selected.connect(self.nodemap_panel.select_camera)
        self.grid.crop_dragged.connect(self.settings_panel.apply_overlay_drag)
        self.selection_panel.view_changed.connect(self.grid.set_visible_positions)
        self.selection_panel.record_changed.connect(self.run_panel._update_estimates)
        self.trigger_panel.fps_changed.connect(self._fps_changed)

        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / config.gui.preview_hz))
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._connect_thread: _ConnectThread | None = None
        self._disconnect_thread: _DisconnectThread | None = None

    # ---- camera window pop-out ----
    def _toggle_cam_window(self, popped: bool) -> None:
        if popped and self.cam_window is None:
            self.takeCentralWidget()  # release the grid without deleting it
            self.cam_window = _CamWindow(lambda: self.popout_action.setChecked(False))
            layout = QVBoxLayout(self.cam_window)
            layout.setContentsMargins(2, 2, 2, 2)
            layout.addWidget(self.grid)
            placeholder = QLabel("cameras popped out — toolbar: 'Pop out cameras' to re-dock")
            placeholder.setAlignment(Qt.AlignCenter)
            self.setCentralWidget(placeholder)
            self.cam_window.show()
        elif not popped and self.cam_window is not None:
            window, self.cam_window = self.cam_window, None
            self.grid.setParent(None)
            placeholder = self.takeCentralWidget()
            if placeholder is not None:
                placeholder.deleteLater()
            self.setCentralWidget(self.grid)
            window.deleteLater()

    def open_full_camera_view(self, position: int) -> None:
        window = self.single_cam_windows.get(position)
        if window is None:
            window = _SingleCamWindow(position, self._single_cam_closed)
            self.single_cam_windows[position] = window
        image = self.grid.latest_display_image(position)
        if image is not None:
            window.update_image(image)
        window.show()
        window.raise_()
        window.activateWindow()

    def _single_cam_closed(self, position: int) -> None:
        self.single_cam_windows.pop(position, None)

    def _fps_changed(self, fps: float) -> None:
        self.clock_mode.setItemText(0, f"Triggered ({fps:g} Hz)")
        self.run_panel._update_estimates()

    def desired_fps(self) -> float:
        return float(self.trigger_panel.fps.value())

    def update_camera_size(self, position: int, values: dict) -> None:
        width = values.get("Width")
        height = values.get("Height")
        if isinstance(width, dict):
            width = width.get("value")
        if isinstance(height, dict):
            height = height.get("value")
        if width is not None and height is not None:
            self.camera_sizes[int(position)] = (int(width), int(height))
            self.run_panel._update_estimates()

    def frame_bytes(self, position: int) -> int:
        width, height = self.camera_sizes[position]
        return width * height

    # ---- connection ----
    def connect_cameras(self) -> None:
        if self.supervisor is not None or self._connect_thread is not None:
            return
        positions = self.selection_panel.use_positions()
        if not positions:
            self.status_label.setText(" select at least one camera in Use ")
            return
        self.connect_action.setEnabled(False)
        self.selection_panel.set_connected(True)
        self.status_label.setText(" connecting… ")
        self._connect_thread = _ConnectThread(self.config, self.state_dir, positions)
        self._connect_thread.progress.connect(
            lambda text: self.status_label.setText(f" {text} "))
        self._connect_thread.done.connect(self._on_connected)
        self._connect_thread.start()

    def _on_connected(self, supervisor, error: str) -> None:
        self._connect_thread = None
        self.connect_action.setEnabled(True)
        if supervisor is None:
            self.selection_panel.set_connected(False)
            self.status_label.setText(f" connect FAILED: {error} ")
            log.error("connect failed: %s", error)
            return
        self.supervisor = supervisor
        self.settings_panel.set_active_positions(set(supervisor.workers))
        for position in sorted(supervisor.workers):
            self.readers[position] = PreviewReader(
                shm_name(self.config.name, position),
                decimate=self.config.gui.preview_decimate)
        supervisor.broadcast({"cmd": "read_many", "names": ["Width", "Height"]})
        self.status_label.setText(f" connected: {len(supervisor.workers)} cameras ")

    def disconnect_cameras(self, blocking: bool = False) -> None:
        supervisor, self.supervisor = self.supervisor, None  # stop event polling now
        for reader in self.readers.values():
            reader.close()
        self.readers.clear()
        self.settings_panel.set_active_positions(set())
        if supervisor is None:
            return
        if blocking:  # app close: must finish restore before the process ends
            supervisor.shutdown()
            self.selection_panel.set_connected(False)
            self.status_label.setText(" disconnected ")
            return
        self.disconnect_action.setEnabled(False)
        self.status_label.setText(" disconnecting — restoring cameras… ")
        self._disconnect_thread = _DisconnectThread(supervisor)
        self._disconnect_thread.done.connect(self._on_disconnected)
        self._disconnect_thread.start()

    def _on_disconnected(self) -> None:
        self._disconnect_thread = None
        self.disconnect_action.setEnabled(True)
        self.selection_panel.set_connected(False)
        self.status_label.setText(" disconnected ")

    # ---- preview ----
    def start_preview(self) -> None:
        if self.supervisor is None:
            return
        free_run = self.clock_mode.currentText().startswith("Free")
        # pipe is FIFO per worker: stop -> mode -> start execute in order
        self.supervisor.broadcast({"cmd": "stop_acquisition"})
        self.supervisor.broadcast({"cmd": "preview_mode", "free_run": free_run})
        self.supervisor.broadcast({"cmd": "start_acquisition"})
        self.status_label.setText(
            " preview: free-run " if free_run else
            f" preview: triggered — needs the {self.desired_fps():g} Hz clock "
            "(Digilent/WaveForms) ")

    def stop_preview(self) -> None:
        if self.supervisor is not None:
            self.supervisor.broadcast({"cmd": "stop_acquisition"})

    # ---- plumbing used by panels ----
    def send_to_worker(self, position: int, message: dict) -> None:
        if self.supervisor is None:
            log.warning("not connected; dropped %s", message.get("cmd"))
            return
        if position not in self.supervisor.workers:
            log.warning("cam%d is not selected in Use; dropped %s",
                        position, message.get("cmd"))
            return
        self.supervisor.send(position, message)

    def set_sim_trigger(self, running: bool) -> None:
        if self.supervisor is not None:
            self.supervisor.set_sim_trigger(running, self.desired_fps())

    def build_run_controller(self) -> RunController | None:
        mode = self.run_panel.mode.currentText()
        if self.supervisor is None and mode != "stimulus":
            return None
        stimulus = self.stimulus_panel.client
        windowed = self.stimulus_panel.windowed.isChecked()
        starter = (lambda: stimulus.start(windowed=windowed))
        trigger = None
        if not self.trigger_panel.external and self.trigger_panel.clock is not None:
            trigger = self.trigger_panel.clock
        # push the panel's current grating values into the controller's config
        import dataclasses

        stim_cfg = dataclasses.replace(
            self.config.stimulus,
            grating=self.stimulus_panel.grating_cfg(),
            circles=self.stimulus_panel.circles_cfg(),
            baseline_s=self.stimulus_panel.baseline.value(),
            grating_s=self.stimulus_panel.grating_s.value(),
        )
        recording_cfg = dataclasses.replace(
            self.config.recording, fps_nominal=self.desired_fps())
        trigger_cfg = dataclasses.replace(
            self.config.trigger, fps=self.desired_fps(),
            duty=self.trigger_panel.duty.value())
        config = dataclasses.replace(
            self.config, stimulus=stim_cfg,
            recording=recording_cfg, trigger=trigger_cfg)
        record_positions = self.selection_panel.record_positions()
        return RunController(config, self.supervisor, stimulus, trigger,
                             stimulus_starter=starter,
                             record_positions=record_positions)

    # ---- periodic ----
    def _tick(self) -> None:
        for position, reader in self.readers.items():
            preview = reader.read_latest()
            if preview is not None:
                self.grid.tiles[position].update_frame(preview)
                window = self.single_cam_windows.get(position)
                if window is not None:
                    window.update_image(
                        self.grid.tiles[position].display_image(preview["image"]))
        self.grid.tick()
        self.stimulus_panel.tick()
        if self.supervisor is None:
            return
        for event in self.supervisor.take_events():
            kind = event.get("evt")
            if kind == "status":
                self.grid.update_status(event)
            elif kind == "nodemap":
                position = event.get("position")
                self.update_camera_size(position, event["dump"])
                self.settings_panel.load_dump(position, event["dump"])
                self.nodemap_panel.load_dump(position, event["dump"])
            elif kind == "write_result":
                self.settings_panel.write_result(event)
                self.log_console.worker_event(event | {"evt": "log", "level": "info",
                                                       "message": f"wrote {event.get('name')}"
                                                                  f" -> {event.get('value')}"})
            elif kind == "values":
                self.update_camera_size(event.get("position"), event["values"])
                self.settings_panel.load_values(event.get("position"), event["values"])
            elif kind == "mode_result":
                self.update_camera_size(event.get("position"), event.get("values", {}))
                self.settings_panel.mode_result(event)
            else:
                if kind == "error" and event.get("cmd") == "node_write":
                    self.settings_panel.write_result({"position": event.get("position"),
                                                      "ok": False})
                self.log_console.worker_event(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.popout_action.setChecked(False)  # re-dock so the grid closes with us
        for window in list(self.single_cam_windows.values()):
            window.close()
        self.stimulus_panel.client.quit()
        self.trigger_panel.close_clock()
        self.disconnect_cameras(blocking=True)  # restore must finish before exit
        super().closeEvent(event)
