from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from rig_studio.config import RigConfig

SendFn = Callable[[int, dict], None]
FreezeFn = Callable[[int, bool], bool]
OverlayFn = Callable[[int, "tuple[float, float, float, float] | None"], None]
ViewFn = Callable[[int], None]
DisplayFn = Callable[[int, bool, float], None]

PANEL_NODES = ("ExposureAuto", "ExposureTime", "GainAuto", "Gain",
               "GammaEnabled", "Gamma", "PixelFormat", "VideoMode",
               "BinningHorizontal", "BinningVertical",
               "Width", "Height", "OffsetX", "OffsetY")
GEOMETRY_NODES = ("Width", "Height", "OffsetX", "OffsetY")

# per-axis native-sensor pixels per mode-coordinate unit
MODE_SENSOR_SCALE = {"Mode0": (1, 1), "Mode1": (2, 2), "Mode2": (1, 2)}


def roi_overlay_rect(freeze_mode: str, freeze_roi: tuple[int, int, int, int],
                     mode: str, ox: int, oy: int, w: int, h: int,
                     ) -> tuple[float, float, float, float]:
    """Pending ROI as FRACTIONS of the frozen reference frame, so drawing is
    independent of preview decimation and display scaling (the reference may
    also have been captured in a different VideoMode)."""
    fsx, fsy = MODE_SENSOR_SCALE.get(freeze_mode, (1, 1))
    sx, sy = MODE_SENSOR_SCALE.get(mode, (1, 1))
    fox, foy, fw, fh = freeze_roi
    return ((ox * sx - fox * fsx) / (fw * fsx), (oy * sy - foy * fsy) / (fh * fsy),
            w * sx / (fw * fsx), h * sy / (fh * fsy))


class CameraSettingsPanel(QWidget):
    """SpinView-style quick controls for the selected camera. Every edit routes
    through the worker's GuardedRig (allowlist + readback verify)."""

    def __init__(self, config: RigConfig, send: SendFn,
                 freeze_preview: FreezeFn | None = None,
                 crop_overlay: OverlayFn | None = None,
                 open_full_view: ViewFn | None = None,
                 display_options: DisplayFn | None = None) -> None:
        super().__init__()
        self.config = config
        self.send = send
        self.freeze_preview = freeze_preview or (lambda position, frozen: False)
        self.crop_overlay = crop_overlay or (lambda position, rect: None)
        self.open_full_view = open_full_view or (lambda position: None)
        self.display_options = display_options or (
            lambda position, auto_contrast, gamma: None)
        self.position: int | None = None
        self.active_positions = {cam.position for cam in config.cameras}
        self._loading = False
        self._freeze_basis: tuple[str, tuple[int, int, int, int]] | None = None
        self._display_by_position = {
            cam.position: (False, 1.0) for cam in config.cameras
        }

        self.title = QLabel("select a camera tile")
        self.apply_all = QCheckBox("apply to all cameras")

        self.exposure = QDoubleSpinBox(suffix=" µs", minimum=6.0, maximum=1_000_000.0,
                                       decimals=1, singleStep=100.0)
        self.gain = QDoubleSpinBox(suffix=" dB", minimum=0.0, maximum=48.0,
                                   decimals=2, singleStep=0.5)
        self.exposure_auto = QComboBox(); self.exposure_auto.addItems(["Off", "Once", "Continuous"])
        self.gain_auto = QComboBox(); self.gain_auto.addItems(["Off", "Once", "Continuous"])
        self.gamma_enabled = QCheckBox("GammaEnabled")
        self.gamma = QDoubleSpinBox(minimum=0.25, maximum=4.0, decimals=2, singleStep=0.05)
        self.pixel_format = QComboBox(); self.pixel_format.addItems(["Mono8", "Mono12p", "Mono16"])
        self.video_mode = QComboBox()
        self.video_mode.addItems(["Mode0", "Mode1", "Mode2"])
        self.video_mode.setToolTip("Mode0: full res / no binning\n"
                                   "Mode1: 2x2 binning (1024x1024, square px)\n"
                                   "Mode2: vertical binning 2 (1024x1024, NON-square px — rig convention)")
        self.binning_h = QSpinBox(minimum=1, maximum=4)
        self.binning_v = QSpinBox(minimum=1, maximum=4)
        self.width = QSpinBox(minimum=8, maximum=2048, singleStep=8)
        self.height = QSpinBox(minimum=2, maximum=2048, singleStep=2)
        self.offset_x = QSpinBox(minimum=0, maximum=2040, singleStep=4)
        self.offset_y = QSpinBox(minimum=0, maximum=2046, singleStep=2)
        self.load_preset = QPushButton("Load rig preset (ROI + defaults)")
        self.freeze_reference = QPushButton("Freeze previous frame for cropping")
        self.freeze_reference.setCheckable(True)
        self.full_view = QPushButton("Selected camera 1:1 view")
        self.preview_contrast = QCheckBox("Auto contrast (preview only)")
        self.preview_gamma = QDoubleSpinBox(
            minimum=0.25, maximum=4.0, decimals=2, singleStep=0.05, value=1.0)

        exposure_box = QGroupBox("Exposure / Gain / Gamma")
        form = QFormLayout(exposure_box)
        form.addRow("ExposureAuto", self.exposure_auto)
        form.addRow("ExposureTime", self.exposure)
        form.addRow("GainAuto", self.gain_auto)
        form.addRow("Gain", self.gain)
        form.addRow(self.gamma_enabled, self.gamma)

        format_box = QGroupBox("Format / Crop (stops preview to apply)")
        form2 = QFormLayout(format_box)
        form2.addRow("PixelFormat", self.pixel_format)
        form2.addRow("VideoMode", self.video_mode)
        form2.addRow("Binning H", self.binning_h)
        form2.addRow("Binning V", self.binning_v)
        form2.addRow("Width", self.width)
        form2.addRow("Height", self.height)
        form2.addRow("OffsetX", self.offset_x)
        form2.addRow("OffsetY", self.offset_y)

        display_box = QGroupBox("Display only (recording stays raw)")
        display_form = QFormLayout(display_box)
        display_form.addRow(self.preview_contrast)
        display_form.addRow("Display gamma", self.preview_gamma)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.apply_all)
        layout.addWidget(exposure_box)
        layout.addWidget(format_box)
        layout.addWidget(display_box)
        layout.addWidget(self.freeze_reference)
        layout.addWidget(self.full_view)
        layout.addWidget(self.load_preset)
        layout.addStretch(1)

        self._wire()

    # ---- wiring ----
    def _wire(self) -> None:
        self.exposure.editingFinished.connect(
            lambda: self._write("ExposureTime", float(self.exposure.value())))
        self.gain.editingFinished.connect(
            lambda: self._write("Gain", float(self.gain.value())))
        self.exposure_auto.activated.connect(
            lambda: self._write("ExposureAuto", self.exposure_auto.currentText()))
        self.gain_auto.activated.connect(
            lambda: self._write("GainAuto", self.gain_auto.currentText()))
        self.gamma_enabled.toggled.connect(
            lambda checked: self._write("GammaEnabled", bool(checked)))
        self.gamma.editingFinished.connect(
            lambda: self._write("Gamma", float(self.gamma.value())))
        self.pixel_format.activated.connect(
            lambda: self._write("PixelFormat", self.pixel_format.currentText()))
        self.video_mode.activated.connect(self._switch_video_mode)
        self.binning_h.editingFinished.connect(
            lambda: self._write("BinningHorizontal", int(self.binning_h.value())))
        self.binning_v.editingFinished.connect(
            lambda: self._write("BinningVertical", int(self.binning_v.value())))
        self.width.editingFinished.connect(
            lambda: self._write("Width", int(self.width.value())))
        self.height.editingFinished.connect(
            lambda: self._write("Height", int(self.height.value())))
        self.offset_x.editingFinished.connect(
            lambda: self._write("OffsetX", int(self.offset_x.value())))
        self.offset_y.editingFinished.connect(
            lambda: self._write("OffsetY", int(self.offset_y.value())))
        self.load_preset.clicked.connect(self._load_preset)
        self.freeze_reference.toggled.connect(self._freeze_reference)
        self.full_view.clicked.connect(self._open_full_view)
        self.preview_contrast.toggled.connect(self._apply_display_options)
        self.preview_gamma.valueChanged.connect(self._apply_display_options)

    def _apply_display_options(self) -> None:
        if self._loading or self.position is None:
            return
        targets = (sorted(self._display_by_position) if self.apply_all.isChecked()
                   else [self.position])
        options = (self.preview_contrast.isChecked(), self.preview_gamma.value())
        for position in targets:
            self._display_by_position[position] = options
            self.display_options(position, *options)

    def _switch_video_mode(self) -> None:
        if self._loading:
            return
        for position in self._targets():
            self.send(position, {"cmd": "video_mode_scaled",
                                 "mode": self.video_mode.currentText()})

    def _freeze_reference(self, frozen: bool) -> None:
        if frozen:
            shown = (self.position is not None
                     and bool(self.freeze_preview(self.position, True)))
            if not shown:
                self.freeze_reference.blockSignals(True)
                self.freeze_reference.setChecked(False)
                self.freeze_reference.blockSignals(False)
                self.freeze_reference.setText(
                    "No frame to freeze — select a tile + start preview")
                self._freeze_basis = None
                return
            self._freeze_basis = (self.video_mode.currentText(),
                                  (int(self.offset_x.value()),
                                   int(self.offset_y.value()),
                                   int(self.width.value()),
                                   int(self.height.value())))
            self._push_overlay()
        else:
            if self.position is not None:
                self.freeze_preview(self.position, False)
            self._freeze_basis = None
        self.freeze_reference.setText(
            "Use live preview" if frozen else "Freeze previous frame for cropping")

    def _push_overlay(self) -> None:
        if self.position is None or self._freeze_basis is None:
            return
        freeze_mode, freeze_roi = self._freeze_basis
        self.crop_overlay(self.position, roi_overlay_rect(
            freeze_mode, freeze_roi, self.video_mode.currentText(),
            int(self.offset_x.value()), int(self.offset_y.value()),
            int(self.width.value()), int(self.height.value())))

    def _open_full_view(self) -> None:
        if self.position is not None:
            self.open_full_view(self.position)

    def apply_overlay_drag(self, position: int,
                           rect: tuple[float, float, float, float]) -> None:
        """Tile crop-drag released: convert reference fractions back to camera
        ROI units, quantize to node increments, and write (size before offset
        when shrinking, offset first when growing, per axis)."""
        if position != self.position or self._freeze_basis is None:
            return
        freeze_mode, (fox, foy, fw, fh) = self._freeze_basis
        fsx, fsy = MODE_SENSOR_SCALE.get(freeze_mode, (1, 1))
        sx, sy = MODE_SENSOR_SCALE.get(self.video_mode.currentText(), (1, 1))
        rx, ry, rw, rh = rect

        def quantized(spin, value: float) -> int:
            step = max(1, int(spin.singleStep()))
            value = int(round(value / step)) * step
            return int(min(max(value, spin.minimum()), spin.maximum()))

        ox = quantized(self.offset_x, (rx * fw + fox) * fsx / sx)
        oy = quantized(self.offset_y, (ry * fh + foy) * fsy / sy)
        w = quantized(self.width, rw * fw * fsx / sx)
        h = quantized(self.height, rh * fh * fsy / sy)
        writes = ([("Width", w), ("OffsetX", ox)] if w <= int(self.width.value())
                  else [("OffsetX", ox), ("Width", w)])
        writes += ([("Height", h), ("OffsetY", oy)] if h <= int(self.height.value())
                   else [("OffsetY", oy), ("Height", h)])
        for spin, value in ((self.offset_x, ox), (self.offset_y, oy),
                            (self.width, w), (self.height, h)):
            spin.setValue(value)
        for name, value in writes:
            self._write(name, value)

    def _targets(self) -> list[int]:
        if self.position is None:
            return []
        if self.apply_all.isChecked():
            return sorted(self.active_positions)
        return [self.position] if self.position in self.active_positions else []

    def set_active_positions(self, positions: set[int]) -> None:
        self.active_positions = set(positions)
        if self.position is not None and self.position not in self.active_positions:
            self.position = None
            self.title.setText("select an active camera tile")

    def _write(self, name: str, value: Any) -> None:
        if self._loading:
            return
        for position in self._targets():
            self.send(position, {"cmd": "node_write", "name": name, "value": value})
        if name in GEOMETRY_NODES:
            self._push_overlay()

    def _load_preset(self) -> None:
        for position in self._targets():
            cam = self.config.by_position(position)
            d = self.config.defaults
            for name, value in (("VideoMode", cam.video_mode),
                                ("Width", cam.roi.width), ("Height", cam.roi.height),
                                ("OffsetX", cam.roi.offset_x), ("OffsetY", cam.roi.offset_y),
                                ("ExposureTime", d.exposure_time_us), ("Gain", d.gain_db),
                                ("GammaEnabled", d.gamma_enabled)):
                self.send(position, {"cmd": "node_write", "name": name, "value": value})

    # ---- state from workers ----
    def select_camera(self, position: int) -> None:
        if position == self.position:
            return  # re-click (e.g. starting a crop drag) must not unfreeze
        if self.position is not None:
            self.freeze_preview(self.position, False)
        self.freeze_reference.setChecked(False)
        self.position = position
        cam = self.config.by_position(position)
        self.title.setText(f"<b>cam{position} {cam.label}</b>  serial {cam.serial}")
        auto_contrast, display_gamma = self._display_by_position[position]
        self._loading = True
        self.preview_contrast.setChecked(auto_contrast)
        self.preview_gamma.setValue(display_gamma)
        self._loading = False
        self.send(position, {"cmd": "nodemap_dump"})

    def load_dump(self, position: int, dump: dict) -> None:
        if position != self.position:
            return
        self._loading = True
        try:
            def value(name, default=None):
                return dump.get(name, {}).get("value", default)

            if value("ExposureTime") is not None:
                self.exposure.setValue(float(value("ExposureTime")))
            if value("Gain") is not None:
                self.gain.setValue(float(value("Gain")))
            self.exposure_auto.setCurrentText(str(value("ExposureAuto", "Off")))
            self.gain_auto.setCurrentText(str(value("GainAuto", "Off")))
            self.gamma_enabled.setChecked(bool(value("GammaEnabled", False)))
            if value("Gamma") is not None:
                self.gamma.setValue(float(value("Gamma")))
            self.pixel_format.setCurrentText(str(value("PixelFormat", "Mono8")))
            self.video_mode.setCurrentText(str(value("VideoMode", "Mode0")))
            for widget, name in ((self.binning_h, "BinningHorizontal"),
                                 (self.binning_v, "BinningVertical"),
                                 (self.width, "Width"), (self.height, "Height"),
                                 (self.offset_x, "OffsetX"), (self.offset_y, "OffsetY")):
                v = value(name)
                if v is not None:
                    widget.setValue(int(v))
                entry = dump.get(name, {})
                if isinstance(entry.get("min"), (int, float)):
                    widget.setMinimum(int(entry["min"]))
                if isinstance(entry.get("max"), (int, float)):
                    widget.setMaximum(int(entry["max"]))
                if isinstance(entry.get("inc"), (int, float)) and entry["inc"]:
                    widget.setSingleStep(int(entry["inc"]))
        finally:
            self._loading = False
        self._push_overlay()

    def load_values(self, position: int, values: dict) -> None:
        """Targeted refresh (read_many result) — reflect the camera's actual state."""
        if position != self.position:
            return
        self._loading = True
        try:
            if values.get("ExposureTime") is not None:
                self.exposure.setValue(float(values["ExposureTime"]))
            if values.get("Gain") is not None:
                self.gain.setValue(float(values["Gain"]))
            if values.get("ExposureAuto") is not None:
                self.exposure_auto.setCurrentText(str(values["ExposureAuto"]))
            if values.get("GainAuto") is not None:
                self.gain_auto.setCurrentText(str(values["GainAuto"]))
            if values.get("GammaEnabled") is not None:
                self.gamma_enabled.setChecked(bool(values["GammaEnabled"]))
            if values.get("Gamma") is not None:
                self.gamma.setValue(float(values["Gamma"]))
            if values.get("PixelFormat") is not None:
                self.pixel_format.setCurrentText(str(values["PixelFormat"]))
            if values.get("VideoMode") is not None:
                self.video_mode.setCurrentText(str(values["VideoMode"]))
            for widget, name in ((self.binning_h, "BinningHorizontal"),
                                 (self.binning_v, "BinningVertical"),
                                 (self.width, "Width"), (self.height, "Height"),
                                 (self.offset_x, "OffsetX"), (self.offset_y, "OffsetY")):
                if values.get(name) is not None:
                    widget.setValue(int(values[name]))
        finally:
            self._loading = False
        self._push_overlay()

    def refresh(self) -> None:
        if self.position is not None:
            self.send(self.position, {"cmd": "read_many", "names": list(PANEL_NODES)})

    def write_result(self, event: dict) -> None:
        if event.get("position") != self.position:
            return
        ok = bool(event.get("ok"))
        self.title.setStyleSheet("color: #2a2;" if ok else "color: #a22;")
        # reflect reality after every write; a mode change moves the geometry
        # limits, so it re-pulls the full dump instead
        if event.get("name") == "VideoMode":
            self.send(self.position, {"cmd": "nodemap_dump"})
        else:
            self.refresh()

    def mode_result(self, event: dict) -> None:
        if event.get("position") != self.position:
            return
        self.title.setStyleSheet("color: #2a2;" if event.get("ok") else "color: #a22;")
        self.send(self.position, {"cmd": "nodemap_dump"})
