from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QPlainTextEdit


class _Emitter(QObject):
    message = Signal(str)


class GuiLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.emitter = _Emitter()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s",
                                            "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.emitter.message.emit(self.format(record))


class LogConsole(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.handler = GuiLogHandler()
        self.handler.emitter.message.connect(self.appendPlainText)
        logging.getLogger().addHandler(self.handler)

    def worker_event(self, event: dict) -> None:
        kind = event.get("evt")
        position = event.get("position", "?")
        stamp = time.strftime("%H:%M:%S")
        if kind == "log":
            self.appendPlainText(f"{stamp} cam{position}: {event.get('message')}")
        elif kind in ("error", "fault"):
            self.appendPlainText(f"{stamp} cam{position} {kind.upper()}: {event.get('error')}")
        elif kind in ("configured", "armed", "done", "recording_complete",
                      "worker_lost", "exited", "preview_mode"):
            detail = {k: v for k, v in event.items()
                      if k not in ("evt", "position", "serial", "dump")}
            self.appendPlainText(f"{stamp} cam{position} {kind} {detail if detail else ''}")
