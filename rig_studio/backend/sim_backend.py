"""Hardware-free CamBackend: fake nodemaps, ROI-honoring frames, simulated trigger clock.

Frames flow when the camera is free-running (TriggerMode Off on the FrameStart
selector) or when the backend's simulated trigger clock is running — mirroring
the real rig, where hardware-triggered cameras are dark without the Digilent.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from .base import CamBackend, CamHandle, Frame, Frames

_SENSOR = 2048  # CMV4000 full frame


def _int_node(value: int, lo: int, hi: int, inc: int = 1) -> dict[str, Any]:
    return {"access": "RW", "type": "IInteger", "value": value, "min": lo, "max": hi, "inc": inc}


def _default_nodes(roi: tuple[int, int, int, int]) -> dict[str, dict[str, Any]]:
    width, height, offset_x, offset_y = roi
    nodes: dict[str, dict[str, Any]] = {
        "VideoMode": {"access": "RW", "type": "IEnumeration", "value": "Mode0"},
        "PixelFormat": {"access": "RW", "type": "IEnumeration", "value": "Mono8"},
        "Width": _int_node(width, 8, _SENSOR, 8),
        "Height": _int_node(height, 2, _SENSOR, 2),
        "OffsetX": _int_node(offset_x, 0, _SENSOR - 8, 4),
        "OffsetY": _int_node(offset_y, 0, _SENSOR - 2, 2),
        "BinningHorizontal": _int_node(1, 1, 4),
        "BinningVertical": _int_node(1, 1, 4),
        "AcquisitionMode": {"access": "RW", "type": "IEnumeration", "value": "Continuous"},
        "ExposureAuto": {"access": "RW", "type": "IEnumeration", "value": "Off"},
        "ExposureMode": {"access": "RW", "type": "IEnumeration", "value": "Timed"},
        "ExposureTime": {"access": "RW", "type": "IFloat", "value": 5000.0,
                         "min": 6.0, "max": 32000.0},
        "GainAuto": {"access": "RW", "type": "IEnumeration", "value": "Off"},
        "Gain": {"access": "RW", "type": "IFloat", "value": 9.8, "min": 0.0, "max": 47.99},
        "GammaEnabled": {"access": "RW", "type": "IBoolean", "value": False},
        "Gamma": {"access": "RW", "type": "IFloat", "value": 1.0, "min": 0.5, "max": 4.0},
        "TriggerSelector": {"access": "RW", "type": "IEnumeration", "value": "FrameStart"},
        "TriggerSource": {"access": "RW", "type": "IEnumeration", "value": "Line0"},
        "TriggerActivation": {"access": "RW", "type": "IEnumeration", "value": "RisingEdge"},
        "TriggerOverlap": {"access": "RW", "type": "IEnumeration", "value": "ReadOut"},
        "TriggerDelay": {"access": "RW", "type": "IFloat", "value": 0.0, "min": 0.0, "max": 1e6},
        "LineSelector": {"access": "RW", "type": "IEnumeration", "value": "Line0"},
        "LineMode": {"access": "RW", "type": "IEnumeration", "value": "Input"},
        "LineInverter": {"access": "RW", "type": "IBoolean", "value": False},
        "LineDebouncerTimeRaw": _int_node(0, 0, 10000),
        "DeviceLinkThroughputLimit": _int_node(400 * 1024 * 1024, 1_000_000, 500_000_000),
        "ChunkModeActive": {"access": "RW", "type": "IBoolean", "value": False},
        "ChunkSelector": {"access": "RW", "type": "IEnumeration", "value": "FrameCounter"},
        "ChunkEnable": {"access": "RW", "type": "IBoolean", "value": False},
        "AcquisitionFrameRateEnabled": {"access": "RW", "type": "IBoolean", "value": True},
        "AcquisitionFrameRate": {"access": "RW", "type": "IFloat", "value": 178.0,
                                 "min": 1.0, "max": 200.0},
        "TLStream:StreamBufferCountMode": {"access": "RW", "type": "IEnumeration",
                                           "value": "Manual"},
        "TLStream:StreamBufferCountManual": _int_node(64, 1, 512),
        "TLStream:StreamBufferHandlingMode": {"access": "RW", "type": "IEnumeration",
                                              "value": "OldestFirst"},
    }
    return nodes


class SimBackend(CamBackend):
    def __init__(
        self,
        serials: list[str],
        rois: dict[str, tuple[int, int, int, int]] | None = None,
        fps: float = 178.0,
    ) -> None:
        self._serials = [str(s) for s in serials]
        self._rois = rois or {}
        self.fps = float(fps)
        self.trigger_running = False  # the simulated Digilent clock
        self._handles: list[SimHandle] = []

    def enumerate(self) -> list[str]:
        return sorted(self._serials)

    def open(self, serial: str) -> CamHandle:
        serial = str(serial)
        if serial not in self._serials:
            raise RuntimeError(f"Configured rig serial is missing: {serial}")
        handle = SimHandle(serial, self, self._rois.get(serial, (1024, 750, 0, 160)))
        self._handles.append(handle)
        return handle

    def close(self) -> None:
        for handle in reversed(self._handles):
            handle.close()
        self._handles.clear()


class SimHandle(CamHandle):
    def __init__(self, serial: str, backend: SimBackend, roi: tuple[int, int, int, int]) -> None:
        self.serial = serial
        self._backend = backend
        self._nodes = _default_nodes(roi)
        # GS3 hardware truth (verified 2026-08-20): TriggerMode is ONE shared
        # node — TriggerSelector does NOT bank it.
        self._trigger_mode = "On"
        self._acquiring = False
        self._closed = False
        self._frame_id = 0
        self._t0 = time.perf_counter()
        self._last_frame_t = 0.0

    def _flowing(self) -> bool:
        return self._trigger_mode == "Off" or self._backend.trigger_running

    def node_read(self, name: str) -> Any:
        if name == "TriggerMode":
            return self._trigger_mode
        entry = self._nodes[name]
        if str(entry.get("access")) in ("1", "NA", "0", "NI"):
            raise RuntimeError(f"Node is not readable: {name}")
        return entry["value"]

    def node_write(self, name: str, value: Any) -> None:
        if name == "TriggerMode":
            self._trigger_mode = value  # shared node — selector does not bank it
            return
        entry = self._nodes[name]
        if entry["access"] not in ("RW", "WO"):
            raise RuntimeError(f"Node is not writable: {name}")
        lo, hi = entry.get("min"), entry.get("max")
        if lo is not None and (value < lo or value > hi):
            raise RuntimeError(f"{name}={value!r} outside [{lo}, {hi}]")
        inc = entry.get("inc")
        if inc and isinstance(value, int) and (value - lo) % inc:
            raise RuntimeError(f"{name}={value!r} violates increment {inc}")
        entry["value"] = value

    def nodemap_dump(self) -> dict[str, dict[str, Any]]:
        dump = {name: dict(entry) for name, entry in self._nodes.items()}
        dump["TriggerMode"] = {"access": "RW", "type": "IEnumeration",
                               "value": self.node_read("TriggerMode")}
        return dump

    def begin_acquisition(self, buffers: int) -> None:
        if self._acquiring:
            raise RuntimeError("Acquisition already active")
        if self.node_read("TLStream:StreamBufferHandlingMode") != "OldestFirst":
            raise RuntimeError("Recording requires OldestFirst")
        self._acquiring = True
        self._last_frame_t = time.perf_counter()

    def _render(self) -> np.ndarray:
        width = self._nodes["Width"]["value"]
        height = self._nodes["Height"]["value"]
        image = np.full((height, width), 30, np.uint8)
        bar = (self._frame_id * 7) % max(width - 32, 1)
        image[:, bar:bar + 32] = 200
        image[:24, :] = int(self.serial) % 251  # per-camera identity band
        return image

    def _fetch_one(self, timeout_s: float) -> Frame:
        deadline = time.perf_counter() + timeout_s
        interval = 1.0 / self._backend.fps
        while True:
            now = time.perf_counter()
            if self._flowing() and now - self._last_frame_t >= interval:
                self._last_frame_t += interval
                if now - self._last_frame_t > 1.0:  # stale epoch after a stall
                    self._last_frame_t = now
                self._frame_id += 1
                return Frame(
                    image=self._render(),
                    frame_id=self._frame_id,
                    timestamp_ns=int((now - self._t0) * 1e9),
                    exposure_us=float(self._nodes["ExposureTime"]["value"]),
                )
            if now >= deadline:
                raise TimeoutError(f"sim fetch timeout on {self.serial}")
            time.sleep(min(0.001, interval / 4))

    def grab(self, n: int, timeout_s: float) -> Frames:
        if not self._acquiring:
            raise RuntimeError("Acquisition is not active")
        return tuple(self._fetch_one(timeout_s) for _ in range(n))

    def discard_pending(self, timeout_s: float) -> int:
        return 0

    def end_acquisition(self) -> None:
        self._acquiring = False

    def close(self) -> None:
        self.end_acquisition()
        self._closed = True
