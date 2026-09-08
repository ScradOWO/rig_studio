from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from rig_studio.safety.allowlist import FORBIDDEN_SUBSTRINGS, MUTABLE_ALLOWLIST

SendFn = Callable[[int, dict], None]


def _parse(text: str, type_name: str):
    if type_name == "IInteger":
        return int(text, 0)
    if type_name == "IFloat":
        return float(text)
    if type_name == "IBoolean":
        return text.strip().lower() in ("1", "true", "on", "yes")
    return text


class NodemapBrowser(QWidget):
    """SpinView-style full nodemap: read everything, write in tiers —
    forbidden substrings hard-blocked, allowlisted nodes guarded-written,
    everything else requires the Advanced toggle + confirmation."""

    def __init__(self, send: SendFn) -> None:
        super().__init__()
        self.send = send
        self.position: int | None = None
        self.filter = QLineEdit(placeholderText="filter nodes…")
        self.refresh = QPushButton("Refresh")
        self.advanced = QCheckBox("Advanced writes")
        self.status = QLabel("")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["node", "value", "type", "access", "min", "max", "inc"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        top = QHBoxLayout()
        top.addWidget(self.filter, 1)
        top.addWidget(self.refresh)
        top.addWidget(self.advanced)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)
        self._dump: dict = {}
        self.filter.textChanged.connect(self._rebuild)
        self.refresh.clicked.connect(self.request_dump)
        self.tree.itemDoubleClicked.connect(self._edit_item)

    def select_camera(self, position: int) -> None:
        self.position = position
        self.request_dump()

    def request_dump(self) -> None:
        if self.position is not None:
            self.send(self.position, {"cmd": "nodemap_dump"})
            self.status.setText("refreshing…")

    def load_dump(self, position: int, dump: dict) -> None:
        if position != self.position:
            return
        self._dump = dump
        self._rebuild()
        self.status.setText(f"cam{position}: {len(dump)} nodes")

    def _rebuild(self) -> None:
        self.tree.clear()
        needle = self.filter.text().casefold()
        for name in sorted(self._dump):
            if needle and needle not in name.casefold():
                continue
            entry = self._dump[name]
            item = QTreeWidgetItem([
                name, str(entry.get("value", "")), str(entry.get("type", "")),
                str(entry.get("access", "")),
                str(entry.get("min", "")), str(entry.get("max", "")),
                str(entry.get("inc", "")),
            ])
            if any(sub in name for sub in FORBIDDEN_SUBSTRINGS):
                item.setForeground(0, Qt.red)
                item.setToolTip(0, "forbidden node — never written by rig-studio")
            elif name not in MUTABLE_ALLOWLIST:
                item.setForeground(0, Qt.gray)
            self.tree.addTopLevelItem(item)

    def _edit_item(self, item: QTreeWidgetItem, column: int) -> None:
        if self.position is None:
            return
        name = item.text(0)
        entry = self._dump.get(name, {})
        if any(sub in name for sub in FORBIDDEN_SUBSTRINGS):
            QMessageBox.warning(self, "Blocked", f"{name} is a forbidden node.")
            return
        if entry.get("access") not in ("RW", "WO"):
            return
        if name not in MUTABLE_ALLOWLIST:
            if not self.advanced.isChecked():
                QMessageBox.information(
                    self, "Not allowlisted",
                    f"{name} is outside the allowlist. Enable 'Advanced writes' "
                    "to write it (the change is still snapshotted and restored).")
                return
            if QMessageBox.question(self, "Advanced write",
                                    f"Write non-allowlisted node {name}?"
                                    ) != QMessageBox.Yes:
                return
        text, ok = QInputDialog.getText(self, name,
                                        f"new value ({entry.get('type', '?')}):",
                                        text=str(entry.get("value", "")))
        if not ok:
            return
        try:
            value = _parse(text, str(entry.get("type", "")))
        except ValueError as error:
            QMessageBox.warning(self, "Bad value", str(error))
            return
        command = {"cmd": "node_write", "name": name, "value": value}
        if name not in MUTABLE_ALLOWLIST:
            command["advanced"] = True
        self.send(self.position, command)
