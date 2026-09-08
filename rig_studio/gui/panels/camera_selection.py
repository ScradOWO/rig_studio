from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from rig_studio.config import RigConfig


class CameraSelectionPanel(QWidget):
    """Select worker, visible, and recording camera subsets."""

    use_changed = Signal(object)
    view_changed = Signal(object)
    record_changed = Signal(object)

    def __init__(self, config: RigConfig) -> None:
        super().__init__()
        self.config = config
        self.use_boxes: dict[int, QCheckBox] = {}
        self.view_boxes: dict[int, QCheckBox] = {}
        self.record_boxes: dict[int, QCheckBox] = {}
        self._connected = False
        self._running = False

        table = QGridLayout()
        table.addWidget(QLabel("Camera"), 0, 0)
        table.addWidget(QLabel("Use"), 0, 1)
        table.addWidget(QLabel("View"), 0, 2)
        table.addWidget(QLabel("Record"), 0, 3)
        use_default = set(config.gui.default_use_positions)
        view_default = set(config.gui.default_view_positions)
        record_default = set(config.recording.default_positions)
        for row, cam in enumerate(sorted(config.cameras, key=lambda item: item.position), 1):
            table.addWidget(QLabel(f"cam{cam.position}  {cam.label}  [{cam.serial}]"), row, 0)
            use = QCheckBox()
            view = QCheckBox()
            record = QCheckBox()
            use.setChecked(cam.position in use_default)
            view.setChecked(cam.position in view_default)
            record.setChecked(cam.position in record_default)
            use.toggled.connect(
                lambda checked, position=cam.position: self._use_toggled(position, checked)
            )
            view.toggled.connect(lambda _checked: self.view_changed.emit(self.view_positions()))
            record.toggled.connect(
                lambda _checked: self.record_changed.emit(self.record_positions())
            )
            self.use_boxes[cam.position] = use
            self.view_boxes[cam.position] = view
            self.record_boxes[cam.position] = record
            table.addWidget(use, row, 1)
            table.addWidget(view, row, 2)
            table.addWidget(record, row, 3)

        self.use_all = QPushButton("Use all")
        self.use_none = QPushButton("Use none")
        self.view_all = QPushButton("View all used")
        self.view_none = QPushButton("View none")
        self.record_all = QPushButton("Record all used")
        self.record_none = QPushButton("Record none")
        self.use_all.clicked.connect(lambda: self._set_checks(self.use_boxes, set(self.use_boxes)))
        self.use_none.clicked.connect(lambda: self._set_checks(self.use_boxes, set()))
        self.view_all.clicked.connect(
            lambda: self._set_checks(self.view_boxes, self.use_positions())
        )
        self.view_none.clicked.connect(lambda: self._set_checks(self.view_boxes, set()))
        self.record_all.clicked.connect(
            lambda: self._set_checks(self.record_boxes, self.use_positions())
        )
        self.record_none.clicked.connect(lambda: self._set_checks(self.record_boxes, set()))

        use_buttons = QHBoxLayout()
        use_buttons.addWidget(self.use_all)
        use_buttons.addWidget(self.use_none)
        view_buttons = QHBoxLayout()
        view_buttons.addWidget(self.view_all)
        view_buttons.addWidget(self.view_none)
        record_buttons = QHBoxLayout()
        record_buttons.addWidget(self.record_all)
        record_buttons.addWidget(self.record_none)

        hint = QLabel(
            "Use controls which workers open on Connect. View only changes live tiles. "
            "Record controls which files are created."
        )
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(table)
        layout.addLayout(use_buttons)
        layout.addLayout(view_buttons)
        layout.addLayout(record_buttons)
        layout.addStretch(1)
        self._refresh_enabled()

    @staticmethod
    def _checked(boxes: dict[int, QCheckBox]) -> set[int]:
        return {position for position, box in boxes.items() if box.isChecked()}

    def use_positions(self) -> set[int]:
        return self._checked(self.use_boxes)

    def view_positions(self) -> set[int]:
        return self._checked(self.view_boxes)

    def record_positions(self) -> set[int]:
        return self._checked(self.record_boxes)

    def _set_checks(self, boxes: dict[int, QCheckBox], selected: set[int]) -> None:
        for position, box in boxes.items():
            box.setChecked(position in selected)

    def _use_toggled(self, position: int, checked: bool) -> None:
        if not checked:
            self.view_boxes[position].setChecked(False)
            self.record_boxes[position].setChecked(False)
        self._refresh_enabled()
        self.use_changed.emit(self.use_positions())

    def _refresh_enabled(self) -> None:
        used = self.use_positions()
        for position, box in self.use_boxes.items():
            box.setEnabled(not self._connected)
            self.view_boxes[position].setEnabled(position in used)
            self.record_boxes[position].setEnabled(position in used and not self._running)
        self.use_all.setEnabled(not self._connected)
        self.use_none.setEnabled(not self._connected)
        self.record_all.setEnabled(not self._running)
        self.record_none.setEnabled(not self._running)

    def set_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        self._refresh_enabled()

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._refresh_enabled()

