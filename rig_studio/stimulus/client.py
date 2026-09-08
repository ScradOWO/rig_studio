"""GUI-side control of the stimulus host process (JSON-lines over stdin +
status-file polling), the projector_host.pyw pattern."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from rig_studio.config import CirclesCfg, GratingCfg, ProjectorCfg
from rig_studio.stimulus.circles import assign_modes, parse_ass, prepare_rows


def grating_spec(grating: GratingCfg,
                 circles: CirclesCfg | None = None) -> dict:
    spec = {
        "period_px": grating.period_px,
        "speed_px_s": grating.speed_px_s,
        "direction": grating.direction,
        "contrast": grating.contrast,
        "rotation_deg": grating.rotation_deg,
        "smoothstep_edges": list(grating.smoothstep_edges),
        "region_preset": grating.region_preset,
        "custom_rect": list(grating.custom_rect) if grating.custom_rect else None,
        "color_bright": grating.color_bright,
        "color_dark": grating.color_dark,
        "bg_color": grating.bg_color,
    }
    if circles is not None and circles.enabled:
        elements = prepare_rows(parse_ass(circles.ass_path),
                                circles.size_top, circles.size_bottom,
                                circles.nth_top, circles.nth_bottom)
        spec["circles"] = {
            "elements": assign_modes(elements, circles.expand_row),
            "cycle_s": circles.cycle_s,
            "soft_px": circles.soft_px,
            "sizes": [circles.size_top, circles.size_bottom],
            "nths": [circles.nth_top, circles.nth_bottom],
            "pulse": circles.pulse,
            "style": circles.style,
            "color_top": None if circles.colors_from_ass else circles.color_top,
            "color_bottom": (None if circles.colors_from_ass
                             else circles.color_bottom),
            "window_bg": circles.window_bg,
        }
    return spec


class StimulusClient:
    def __init__(self, projector: ProjectorCfg, status_path: str | Path) -> None:
        self.projector = projector
        self.status_path = Path(status_path)
        self.process: subprocess.Popen | None = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, *, windowed: bool = False, timeout_s: float = 20.0) -> dict:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.unlink(missing_ok=True)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "rig_studio.stimulus.host",
             "--status", str(self.status_path)],
            stdin=subprocess.PIPE, text=True, bufsize=1,
        )
        self.send({"cmd": "open", "windowed": windowed, "projector": {
            "width": self.projector.width, "height": self.projector.height,
            "refresh_hz": self.projector.refresh_hz,
            "name_hint": self.projector.name_hint,
        }})
        return self.wait_state({"ready", "error"}, timeout_s)

    def send(self, message: dict) -> None:
        if not self.alive:
            raise RuntimeError("stimulus host is not running")
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def status(self) -> dict:
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # incl. replace-in-progress races
            return {"state": "unknown"}

    def wait_state(self, states: set[str], timeout_s: float) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            current = self.status()
            if current.get("state") in states:
                return current
            if not self.alive:
                raise RuntimeError("stimulus host exited unexpectedly")
            time.sleep(0.05)
        raise TimeoutError(f"stimulus host never reached {states}; last: {self.status()}")

    def preview(self, grating: GratingCfg,
                circles: CirclesCfg | None = None) -> None:
        self.send({"cmd": "preview", "grating": grating_spec(grating, circles)})

    def arm(self, grating: GratingCfg, *, start_posix: float, grating_s: float,
            log_path: str | Path, base: str,
            circles: CirclesCfg | None = None) -> None:
        self.send({"cmd": "arm", "grating": grating_spec(grating, circles),
                   "schedule": {"start_posix": start_posix, "grating_s": grating_s},
                   "log_path": str(log_path), "base": base})

    def stop(self) -> None:
        if self.alive:
            self.send({"cmd": "stop"})

    def fliptest(self, seconds: float = 2.0) -> dict:
        self.send({"cmd": "fliptest", "seconds": seconds})
        return self.wait_state({"ready", "error"}, seconds + 10.0)

    def quit(self) -> None:
        if self.alive:
            try:
                self.send({"cmd": "quit"})
                self.process.wait(timeout=5.0)
            except Exception:  # noqa: BLE001
                self.process.kill()
        self.process = None
