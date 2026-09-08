from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from rig_studio.config import GratingCfg, RigConfig
from rig_studio.stimulus import profiles
from rig_studio.stimulus.client import StimulusClient


class ColorButton(QPushButton):
    """Swatch button that opens a color dialog (Aegisub-style picker).

    The border must be styled explicitly: the native Windows button style
    silently ignores background-color otherwise (swatches looked 'broken')."""

    def __init__(self, hex_color: str) -> None:
        super().__init__()
        self.setFixedWidth(72)
        self.set_hex(hex_color)
        self.clicked.connect(self._pick)

    def set_hex(self, hex_color: str) -> None:
        self._hex = hex_color.lower()
        color = QColor(self._hex)
        text = "#000000" if color.lightness() > 127 else "#ffffff"
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._hex}; color: {text};"
            f" border: 1px solid #777777; padding: 3px; }}")
        self.setText(self._hex)

    def hex(self) -> str:
        return self._hex

    def _pick(self) -> None:
        color = QColorDialog.getColor(QColor(self._hex), self)
        if color.isValid():
            self.set_hex(color.name())


class _HostStartThread(QThread):
    done = Signal(str)

    def __init__(self, client: StimulusClient, windowed: bool) -> None:
        super().__init__()
        self.client = client
        self.windowed = windowed

    def run(self) -> None:
        try:
            state = self.client.start(windowed=self.windowed)
            self.done.emit(f"host: {state}")
        except Exception as error:  # noqa: BLE001
            self.done.emit(f"host start FAILED: {error}")


def _row(*widgets) -> QHBoxLayout:
    box = QHBoxLayout()
    for widget in widgets:
        box.addWidget(QLabel(widget) if isinstance(widget, str) else widget)
    return box


class StimulusPanel(QWidget):
    """Stimulus control: play/profiles up top, details in Grating /
    Shapes / Run tabs."""

    def __init__(self, config: RigConfig, status_path) -> None:
        super().__init__()
        self.config = config
        self.client = StimulusClient(config.stimulus.projector, status_path)
        grating = config.stimulus.grating
        circles = config.stimulus.circles

        # ---- grating tab widgets ----
        self.period = QDoubleSpinBox(minimum=2.0, maximum=4000.0, decimals=2)
        self.period.setValue(grating.period_px)
        self.speed = QDoubleSpinBox(minimum=0.0, maximum=5000.0, decimals=1)
        self.speed.setValue(grating.speed_px_s)
        self.direction = QComboBox(); self.direction.addItems(["+1", "-1"])
        self.direction.setCurrentText("+1" if grating.direction == 1 else "-1")
        self.contrast = QDoubleSpinBox(minimum=0.0, maximum=1.0, decimals=2,
                                       singleStep=0.1)
        self.contrast.setValue(grating.contrast)
        self.rotation = QDoubleSpinBox(minimum=-180.0, maximum=180.0, decimals=1)
        self.rotation.setValue(grating.rotation_deg)
        self.region = QComboBox()
        self.region.addItems(["bottomhalf", "tophalf", "full", "custom"])
        self.region.setCurrentText(grating.region_preset)
        self.custom = [QSpinBox(minimum=0, maximum=8192) for _ in range(4)]
        if grating.custom_rect:
            for widget, value in zip(self.custom, grating.custom_rect):
                widget.setValue(value)
        self.col_bright = ColorButton(grating.color_bright)
        self.col_dark = ColorButton(grating.color_dark)
        self.col_bg = ColorButton(grating.bg_color)

        # ---- shapes tab widgets ----
        self.circles_on = QCheckBox("draw shapes from the .ass layout")
        self.ass_box = QComboBox()
        self.ass_box.setToolTip(
            "Shape layouts are .ass files in configs/ (edit them in Aegisub\n"
            "over Desktop/stimulus_current_frame.png; active Dialogue lines\n"
            "become shapes, Comment lines are ignored).")
        self.shape_style = QComboBox()
        self.shape_style.addItems(["fill", "window"])
        self.shape_style.setToolTip("fill: opaque shapes over the grating\n"
                                    "window: grating shows only inside shapes")
        self.shape_pulse = QCheckBox(
            "pulse circles (one side grows, the other shrinks — "
            "rectangles never move)")
        self.circle_cycle = QDoubleSpinBox(minimum=0.1, maximum=600.0, decimals=2)
        self.circle_cycle.setToolTip("seconds per grow/shrink cycle — lower = faster")
        self.expand_row = QComboBox(); self.expand_row.addItems(["top", "bottom"])
        self.circle_soft = QDoubleSpinBox(minimum=0.0, maximum=30.0, decimals=1,
                                          singleStep=0.5)
        # per-wall grid: top / bottom columns (circles only — rects never resize)
        self.size_top = QDoubleSpinBox(minimum=0.1, maximum=5.0, decimals=2,
                                       singleStep=0.1)
        self.size_bottom = QDoubleSpinBox(minimum=0.1, maximum=5.0, decimals=2,
                                          singleStep=0.1)
        self.nth_top = QSpinBox(minimum=1, maximum=6)
        self.nth_bottom = QSpinBox(minimum=1, maximum=6)
        for widget in (self.nth_top, self.nth_bottom):
            widget.setToolTip("keep every Nth circle (2 = alternate ones)")
        self.col_top = ColorButton("#000000")
        self.col_bottom = ColorButton("#000000")
        self.swap_rows = QPushButton("⇄ swap walls")
        self.swap_rows.setToolTip("exchange size / every-Nth / color "
                                  "between the two walls")
        self.ass_colors = QCheckBox("circle colors from the .ass file "
                                    "(untick to use the pickers above)")
        self.ass_colors.toggled.connect(self._sync_color_pickers)
        self.col_window_bg = ColorButton("#636363")
        self._circle_widgets = (
            self.circles_on, self.ass_box, self.shape_style, self.shape_pulse,
            self.circle_cycle, self.expand_row, self.circle_soft,
            self.size_top, self.size_bottom, self.nth_top, self.nth_bottom,
            self.col_top, self.col_bottom, self.swap_rows, self.ass_colors,
            self.col_window_bg)
        if circles:
            self._reload_ass_files(select=Path(circles.ass_path).name)
            self.circles_on.setChecked(circles.enabled)
            self.shape_style.setCurrentText(circles.style)
            self.shape_pulse.setChecked(circles.pulse)
            self.circle_cycle.setValue(circles.cycle_s)
            self.expand_row.setCurrentText(circles.expand_row)
            self.circle_soft.setValue(circles.soft_px)
            self.size_top.setValue(circles.size_top)
            self.size_bottom.setValue(circles.size_bottom)
            self.nth_top.setValue(circles.nth_top)
            self.nth_bottom.setValue(circles.nth_bottom)
            self.ass_colors.setChecked(circles.colors_from_ass)
            self.col_top.set_hex(circles.color_top)
            self.col_bottom.set_hex(circles.color_bottom)
            self.col_window_bg.set_hex(circles.window_bg)
            self._sync_color_pickers()
        else:
            for widget in self._circle_widgets:
                widget.setEnabled(False)
            self.circles_on.setToolTip("no stimulus.circles section in rig.yaml")

        # ---- run tab widgets ----
        self.baseline = QDoubleSpinBox(minimum=0.0, maximum=600.0, decimals=1)
        self.baseline.setValue(config.stimulus.baseline_s)
        self.grating_s = QDoubleSpinBox(minimum=0.1, maximum=36000.0, decimals=1)
        self.grating_s.setValue(config.stimulus.grating_s)
        self.windowed = QCheckBox("windowed (dev — no projector)")
        self.start_host = QPushButton("Start host only")
        self.fliptest = QPushButton("Flip-timing test")
        self.quit_host = QPushButton("Quit host")

        # ---- top-level controls ----
        self.play = QPushButton("▶  PLAY ON PROJECTOR")
        self.play.setMinimumHeight(44)
        self.play.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.stop = QPushButton("■  Stop (black)")
        self.stop.setMinimumHeight(32)
        self.profile_box = QComboBox()
        self.prof_load = QPushButton("Load")
        self.prof_save = QPushButton("Save as…")
        self.prof_delete = QPushButton("Delete")
        self._reload_profiles()
        self.status = QLabel("host not running")
        self.status.setWordWrap(True)

        grating_form = QFormLayout()
        grating_form.addRow("period (px)", self.period)
        grating_form.addRow("speed (px/s)", self.speed)
        grating_form.addRow("direction", self.direction)
        grating_form.addRow("contrast", self.contrast)
        grating_form.addRow("rotation (deg)", self.rotation)
        grating_form.addRow("region", self.region)
        grating_form.addRow("custom l/t/r/b", _row(*self.custom))
        grating_form.addRow("bar colors", _row("bright", self.col_bright,
                                               "dark", self.col_dark))
        grating_form.addRow("screen bg", _row(self.col_bg))

        shapes_form = QFormLayout()
        shapes_form.addRow(self.circles_on)
        shapes_form.addRow("layout (.ass)", self.ass_box)
        shapes_form.addRow("style", self.shape_style)
        shapes_form.addRow("edge (px)", self.circle_soft)
        walls = QGridLayout()
        walls.addWidget(QLabel("<b>top wall</b>"), 0, 1)
        walls.addWidget(QLabel("<b>bottom wall</b>"), 0, 2)
        walls.addWidget(self.swap_rows, 0, 3)
        for grid_row, (label, top_w, bottom_w) in enumerate((
                ("size ×", self.size_top, self.size_bottom),
                ("every Nth", self.nth_top, self.nth_bottom),
                ("color", self.col_top, self.col_bottom)), start=1):
            walls.addWidget(QLabel(label), grid_row, 0)
            walls.addWidget(top_w, grid_row, 1)
            walls.addWidget(bottom_w, grid_row, 2)
        shapes_form.addRow(walls)
        shapes_form.addRow(self.ass_colors)
        shapes_form.addRow("window bg", _row(self.col_window_bg))
        shapes_form.addRow(self.shape_pulse)
        shapes_form.addRow("pulse cycle (s)", self.circle_cycle)
        shapes_form.addRow("growing wall", self.expand_row)

        run_form = QFormLayout()
        run_form.addRow("baseline (s)", self.baseline)
        run_form.addRow("grating (s)", self.grating_s)
        run_form.addRow(self.windowed)
        for button in (self.start_host, self.fliptest, self.quit_host):
            run_form.addRow(button)

        tabs = QTabWidget()
        for title, form in (("Grating", grating_form), ("Shapes", shapes_form),
                            ("Run", run_form)):
            page = QWidget()
            page.setLayout(form)
            tabs.addTab(page, title)

        layout = QVBoxLayout(self)
        layout.addWidget(self.play)
        layout.addWidget(self.stop)
        layout.addLayout(_row("profile", self.profile_box))
        layout.addLayout(_row(self.prof_load, self.prof_save, self.prof_delete))
        layout.addWidget(tabs)
        layout.addWidget(self.status)
        layout.addStretch(1)

        self.play.clicked.connect(self._play)
        self.stop.clicked.connect(lambda: self.client.alive and self.client.stop())
        self.prof_load.clicked.connect(self._profile_load)
        self.prof_save.clicked.connect(self._profile_save)
        self.prof_delete.clicked.connect(self._profile_delete)
        self.swap_rows.clicked.connect(self._swap_walls)
        self.start_host.clicked.connect(lambda: self._start_host(play_after=False))
        self.fliptest.clicked.connect(self._fliptest)
        self.quit_host.clicked.connect(self.client.quit)

    # ---- current settings as configs ----
    def grating_cfg(self) -> GratingCfg:
        return dataclasses.replace(
            self.config.stimulus.grating,
            period_px=self.period.value(),
            speed_px_s=self.speed.value(),
            direction=1 if self.direction.currentText() == "+1" else -1,
            contrast=self.contrast.value(),
            rotation_deg=self.rotation.value(),
            region_preset=self.region.currentText(),
            custom_rect=tuple(w.value() for w in self.custom)
            if self.region.currentText() == "custom" else
            self.config.stimulus.grating.custom_rect,
            color_bright=self.col_bright.hex(),
            color_dark=self.col_dark.hex(),
            bg_color=self.col_bg.hex(),
        )

    def circles_cfg(self):
        base = self.config.stimulus.circles
        if base is None:
            return None
        return dataclasses.replace(
            base,
            enabled=self.circles_on.isChecked(),
            ass_path=str(self._configs_dir() / self.ass_box.currentText())
            if self.ass_box.currentText() else base.ass_path,
            cycle_s=self.circle_cycle.value(),
            expand_row=self.expand_row.currentText(),
            soft_px=self.circle_soft.value(),
            pulse=self.shape_pulse.isChecked(),
            style=self.shape_style.currentText(),
            size_top=self.size_top.value(),
            size_bottom=self.size_bottom.value(),
            nth_top=self.nth_top.value(),
            nth_bottom=self.nth_bottom.value(),
            colors_from_ass=self.ass_colors.isChecked(),
            color_top=self.col_top.hex(),
            color_bottom=self.col_bottom.hex(),
            window_bg=self.col_window_bg.hex(),
        )

    def _sync_color_pickers(self, *_args) -> None:
        """Gray the per-wall pickers out while .ass colors are in charge."""
        picked = not self.ass_colors.isChecked()
        self.col_top.setEnabled(picked)
        self.col_bottom.setEnabled(picked)

    def _swap_walls(self) -> None:
        """Flip which wall gets which circles: exchange the per-wall column."""
        for top_w, bottom_w in ((self.size_top, self.size_bottom),
                                (self.nth_top, self.nth_bottom)):
            top_value = top_w.value()
            top_w.setValue(bottom_w.value())
            bottom_w.setValue(top_value)
        top_hex = self.col_top.hex()
        self.col_top.set_hex(self.col_bottom.hex())
        self.col_bottom.set_hex(top_hex)

    def _configs_dir(self) -> Path:
        return Path(self.config.stimulus.profiles_path).parent

    def _reload_ass_files(self, select: str | None = None) -> None:
        names = sorted(p.name for p in self._configs_dir().glob("*.ass"))
        self.ass_box.clear()
        self.ass_box.addItems(names)
        if select in names:
            self.ass_box.setCurrentText(select)

    # ---- named profiles ----
    def _settings(self) -> dict:
        return {
            "period_px": self.period.value(),
            "speed_px_s": self.speed.value(),
            "direction": 1 if self.direction.currentText() == "+1" else -1,
            "contrast": self.contrast.value(),
            "rotation_deg": self.rotation.value(),
            "region_preset": self.region.currentText(),
            "custom_rect": [w.value() for w in self.custom],
            "color_bright": self.col_bright.hex(),
            "color_dark": self.col_dark.hex(),
            "bg_color": self.col_bg.hex(),
            "baseline_s": self.baseline.value(),
            "grating_s": self.grating_s.value(),
            "circles": {
                "enabled": self.circles_on.isChecked(),
                "ass_file": self.ass_box.currentText(),
                "style": self.shape_style.currentText(),
                "pulse": self.shape_pulse.isChecked(),
                "cycle_s": self.circle_cycle.value(),
                "expand_row": self.expand_row.currentText(),
                "soft_px": self.circle_soft.value(),
                "size_top": self.size_top.value(),
                "size_bottom": self.size_bottom.value(),
                "nth_top": self.nth_top.value(),
                "nth_bottom": self.nth_bottom.value(),
                "colors_from_ass": self.ass_colors.isChecked(),
                "color_top": self.col_top.hex(),
                "color_bottom": self.col_bottom.hex(),
                "window_bg": self.col_window_bg.hex(),
            },
        }

    def _apply_settings(self, s: dict) -> None:
        self.period.setValue(float(s["period_px"]))
        self.speed.setValue(float(s["speed_px_s"]))
        self.direction.setCurrentText("+1" if int(s["direction"]) == 1 else "-1")
        self.contrast.setValue(float(s["contrast"]))
        self.rotation.setValue(float(s["rotation_deg"]))
        self.region.setCurrentText(str(s["region_preset"]))
        for widget, value in zip(self.custom, s["custom_rect"]):
            widget.setValue(int(value))
        self.col_bright.set_hex(str(s.get("color_bright", "#ffffff")))
        self.col_dark.set_hex(str(s.get("color_dark", "#000000")))
        self.col_bg.set_hex(str(s.get("bg_color", "#000000")))
        self.baseline.setValue(float(s["baseline_s"]))
        self.grating_s.setValue(float(s["grating_s"]))
        circ = s.get("circles", {})
        if circ and self.circles_on.isEnabled():
            self.circles_on.setChecked(bool(circ["enabled"]))
            self._reload_ass_files(select=circ.get("ass_file"))
            self.shape_style.setCurrentText(str(circ.get("style", "fill")))
            self.shape_pulse.setChecked(bool(circ.get("pulse", True)))
            self.circle_cycle.setValue(float(circ["cycle_s"]))
            self.expand_row.setCurrentText(str(circ["expand_row"]))
            self.circle_soft.setValue(float(circ["soft_px"]))
            legacy_size = float(circ.get("size_factor", 1.0))
            self.size_top.setValue(float(circ.get("size_top", legacy_size)))
            self.size_bottom.setValue(float(circ.get("size_bottom", legacy_size)))
            self.nth_top.setValue(int(circ.get("nth_top", 1)))
            self.nth_bottom.setValue(int(circ.get("nth_bottom", 1)))
            self.ass_colors.setChecked(bool(circ.get("colors_from_ass", True)))
            self.col_top.set_hex(str(circ.get("color_top", "#000000")))
            self.col_bottom.set_hex(str(circ.get("color_bottom", "#000000")))
            self.col_window_bg.set_hex(str(circ.get("window_bg", "#636363")))

    def _reload_profiles(self, select: str | None = None) -> None:
        names = sorted(profiles.load_all(self.config.stimulus.profiles_path))
        self.profile_box.clear()
        self.profile_box.addItems(names)
        if select in names:
            self.profile_box.setCurrentText(select)

    def _profile_load(self) -> None:
        name = self.profile_box.currentText()
        settings = profiles.load_all(self.config.stimulus.profiles_path).get(name)
        if settings is None:
            self.status.setText("no profile selected")
            return
        self._apply_settings(settings)
        self.status.setText(f"loaded profile '{name}'")

    def _profile_save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save stimulus profile",
                                        "profile name:",
                                        text=self.profile_box.currentText())
        name = name.strip()
        if not ok or not name:
            return
        profiles.save(self.config.stimulus.profiles_path, name, self._settings())
        self._reload_profiles(select=name)
        self.status.setText(f"saved profile '{name}'")

    def _profile_delete(self) -> None:
        name = self.profile_box.currentText()
        if not name:
            return
        profiles.delete(self.config.stimulus.profiles_path, name)
        self._reload_profiles()
        self.status.setText(f"deleted profile '{name}'")

    # ---- host control ----
    def _play(self) -> None:
        """One click: boot the host on the projector if needed, then run the grating."""
        if self.client.alive:
            self.client.preview(self.grating_cfg(), self.circles_cfg())
            self.status.setText("playing grating")
            return
        self._start_host(play_after=True)

    def _start_host(self, play_after: bool = False) -> None:
        if getattr(self, "_start_thread", None) and self._start_thread.isRunning():
            return
        self.status.setText("starting host on the projector…")
        self.play.setEnabled(False)
        self.start_host.setEnabled(False)
        self._play_after = play_after
        self._start_thread = _HostStartThread(self.client, self.windowed.isChecked())
        self._start_thread.done.connect(self._host_started)
        self._start_thread.start()

    def _host_started(self, message: str) -> None:
        self.status.setText(message)
        self.play.setEnabled(True)
        self.start_host.setEnabled(True)
        if getattr(self, "_play_after", False) and self.client.alive \
                and "FAILED" not in message:
            self.client.preview(self.grating_cfg(), self.circles_cfg())
            self.status.setText("playing grating")

    def _fliptest(self) -> None:
        if not self.client.alive:
            self.status.setText("start the host first")
            return
        state = self.client.fliptest(2.0)
        self.status.setText(f"measured: {state.get('refresh_hz', 0):.2f} Hz "
                            f"(ifi {state.get('ifi', 0) * 1000:.3f} ms)")

    def tick(self) -> None:
        # poll the status file gently (~2 Hz) — hammering it races the host's
        # atomic replace on Windows
        import time as _time

        now = _time.monotonic()
        if now - getattr(self, "_last_poll", 0.0) < 0.5 or not self.client.alive:
            return
        self._last_poll = now
        state = str(self.client.status().get("state", "?"))
        if state != getattr(self, "_last_state", None):
            self._last_state = state
            self.status.setText(f"host: {state}")
