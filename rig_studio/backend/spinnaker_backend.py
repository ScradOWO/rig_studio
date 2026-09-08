"""Native Spinnaker C++ backend loaded through a stable C ABI."""
from __future__ import annotations

import ctypes as ct
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np

from .base import CamBackend, CamHandle, Frame, FrameGapError, Frames

_VALUE_INTEGER = 1
_VALUE_FLOAT = 2
_VALUE_BOOLEAN = 3
_VALUE_STRING = 4

_NODE_HAS_VALUE = 1
_NODE_HAS_MIN = 2
_NODE_HAS_MAX = 4
_NODE_HAS_INC = 8


class _Value(ct.Structure):
    _fields_ = [
        ("type", ct.c_int32),
        ("reserved", ct.c_int32),
        ("integer", ct.c_int64),
        ("real", ct.c_double),
        ("text", ct.c_char * 512),
    ]


class _NodeInfo(ct.Structure):
    _fields_ = [
        ("name", ct.c_char * 256),
        ("access", ct.c_char * 16),
        ("type", ct.c_char * 32),
        ("value", ct.c_char * 512),
        ("minimum", ct.c_double),
        ("maximum", ct.c_double),
        ("increment", ct.c_double),
        ("flags", ct.c_uint32),
    ]


class _FrameInfo(ct.Structure):
    _fields_ = [
        ("frame_id", ct.c_uint64),
        ("timestamp_ns", ct.c_uint64),
        ("exposure_us", ct.c_double),
        ("width", ct.c_uint32),
        ("height", ct.c_uint32),
        ("image_bytes", ct.c_uint64),
        ("image_status", ct.c_int32),
        ("reserved", ct.c_int32),
    ]


def _decode(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _default_dll() -> Path:
    configured = os.environ.get("RIG_STUDIO_SPINNAKER_DLL")
    if configured:
        return Path(configured)
    return Path(__file__).parents[1] / "native" / "bin" / "rig_studio_spinnaker.dll"


class _NativeApi:
    def __init__(self, dll_path: str | Path | None = None) -> None:
        path = Path(dll_path) if dll_path else _default_dll()
        if not path.is_file():
            raise FileNotFoundError(
                f"Native Spinnaker backend not built: {path}. "
                r"Run powershell -File native\build.ps1 from the rig_studio repository."
            )
        self._dll_dirs = []
        if hasattr(os, "add_dll_directory"):
            for directory in (
                path.parent,
                Path(os.environ.get("SPINNAKER_ROOT", r"C:\Program Files\Teledyne\Spinnaker"))
                / "bin64" / "vs2015",
            ):
                if directory.is_dir():
                    self._dll_dirs.append(os.add_dll_directory(str(directory)))
        self.lib = ct.CDLL(str(path))
        self._declare()

    def _declare(self) -> None:
        lib = self.lib
        lib.rs_last_error.argtypes = []
        lib.rs_last_error.restype = ct.c_char_p
        lib.rs_system_create.argtypes = [ct.POINTER(ct.c_void_p)]
        lib.rs_system_create.restype = ct.c_int
        lib.rs_system_destroy.argtypes = [ct.c_void_p]
        lib.rs_system_destroy.restype = ct.c_int
        lib.rs_camera_count.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint32)]
        lib.rs_camera_count.restype = ct.c_int
        lib.rs_camera_serial.argtypes = [ct.c_void_p, ct.c_uint32, ct.c_char_p, ct.c_size_t]
        lib.rs_camera_serial.restype = ct.c_int
        lib.rs_camera_open.argtypes = [ct.c_void_p, ct.c_char_p, ct.POINTER(ct.c_void_p)]
        lib.rs_camera_open.restype = ct.c_int
        lib.rs_camera_close.argtypes = [ct.c_void_p]
        lib.rs_camera_close.restype = ct.c_int
        lib.rs_node_read.argtypes = [
            ct.c_void_p, ct.c_char_p, ct.c_int, ct.POINTER(_Value)
        ]
        lib.rs_node_read.restype = ct.c_int
        lib.rs_node_write.argtypes = [ct.c_void_p, ct.c_char_p, ct.c_char_p]
        lib.rs_node_write.restype = ct.c_int
        lib.rs_node_count.argtypes = [
            ct.c_void_p, ct.c_int, ct.POINTER(ct.c_uint32)
        ]
        lib.rs_node_count.restype = ct.c_int
        lib.rs_node_info.argtypes = [
            ct.c_void_p, ct.c_int, ct.c_uint32, ct.POINTER(_NodeInfo)
        ]
        lib.rs_node_info.restype = ct.c_int
        lib.rs_begin_acquisition.argtypes = [ct.c_void_p]
        lib.rs_begin_acquisition.restype = ct.c_int
        lib.rs_grab_into.argtypes = [
            ct.c_void_p,
            ct.c_void_p,
            ct.c_size_t,
            ct.c_uint32,
            ct.POINTER(_FrameInfo),
        ]
        lib.rs_grab_into.restype = ct.c_int
        lib.rs_discard_one.argtypes = [ct.c_void_p, ct.c_uint32]
        lib.rs_discard_one.restype = ct.c_int
        lib.rs_end_acquisition.argtypes = [ct.c_void_p]
        lib.rs_end_acquisition.restype = ct.c_int

    def _check(self, result: int) -> None:
        if result == 0:
            return
        message = _decode(self.lib.rs_last_error() or b"native Spinnaker error")
        if result == 1:
            raise TimeoutError(message)
        raise RuntimeError(message)

    def system_create(self) -> ct.c_void_p:
        handle = ct.c_void_p()
        self._check(self.lib.rs_system_create(ct.byref(handle)))
        return handle

    def system_destroy(self, system: ct.c_void_p) -> None:
        self._check(self.lib.rs_system_destroy(system))

    def enumerate(self, system: ct.c_void_p) -> list[str]:
        count = ct.c_uint32()
        self._check(self.lib.rs_camera_count(system, ct.byref(count)))
        serials: list[str] = []
        for index in range(count.value):
            value = ct.create_string_buffer(256)
            self._check(self.lib.rs_camera_serial(system, index, value, len(value)))
            serials.append(_decode(value.raw))
        return serials

    def camera_open(self, system: ct.c_void_p, serial: str) -> ct.c_void_p:
        handle = ct.c_void_p()
        self._check(self.lib.rs_camera_open(system, serial.encode(), ct.byref(handle)))
        return handle

    def camera_close(self, camera: ct.c_void_p) -> None:
        self._check(self.lib.rs_camera_close(camera))

    def node_read(self, camera: ct.c_void_p, name: str, ignore_cache: bool) -> Any:
        value = _Value()
        self._check(self.lib.rs_node_read(
            camera, name.encode(), int(ignore_cache), ct.byref(value)
        ))
        if value.type == _VALUE_INTEGER:
            return int(value.integer)
        if value.type == _VALUE_FLOAT:
            return float(value.real)
        if value.type == _VALUE_BOOLEAN:
            return bool(value.integer)
        if value.type == _VALUE_STRING:
            return _decode(value.text)
        raise RuntimeError(f"Native node {name} returned unsupported type {value.type}")

    def node_write(self, camera: ct.c_void_p, name: str, value: Any) -> None:
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        self._check(self.lib.rs_node_write(camera, name.encode(), text.encode()))

    def nodemap_dump(self, camera: ct.c_void_p) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for scope, prefix in ((0, ""), (1, "TLStream:")):
            count = ct.c_uint32()
            self._check(self.lib.rs_node_count(camera, scope, ct.byref(count)))
            for index in range(count.value):
                info = _NodeInfo()
                self._check(self.lib.rs_node_info(camera, scope, index, ct.byref(info)))
                name = prefix + _decode(info.name)
                entry: dict[str, Any] = {
                    "access": _decode(info.access),
                    "type": _decode(info.type),
                }
                if info.flags & _NODE_HAS_VALUE:
                    raw = _decode(info.value)
                    if entry["type"] == "IInteger":
                        entry["value"] = int(raw)
                    elif entry["type"] == "IFloat":
                        entry["value"] = float(raw)
                    elif entry["type"] == "IBoolean":
                        entry["value"] = raw.lower() == "true"
                    else:
                        entry["value"] = raw
                if info.flags & _NODE_HAS_MIN:
                    entry["min"] = info.minimum
                if info.flags & _NODE_HAS_MAX:
                    entry["max"] = info.maximum
                if info.flags & _NODE_HAS_INC:
                    entry["inc"] = info.increment
                result[name] = entry
        return result

    def begin(self, camera: ct.c_void_p) -> None:
        self._check(self.lib.rs_begin_acquisition(camera))

    def grab_into(
        self, camera: ct.c_void_p, destination: np.ndarray, timeout_s: float
    ) -> _FrameInfo:
        info = _FrameInfo()
        timeout_ms = max(1, int(round(timeout_s * 1000.0)))
        self._check(self.lib.rs_grab_into(
            camera,
            ct.c_void_p(destination.ctypes.data),
            destination.nbytes,
            timeout_ms,
            ct.byref(info),
        ))
        return info

    def discard_one(self, camera: ct.c_void_p, timeout_s: float) -> None:
        timeout_ms = max(1, int(round(timeout_s * 1000.0)))
        self._check(self.lib.rs_discard_one(camera, timeout_ms))

    def end(self, camera: ct.c_void_p) -> None:
        self._check(self.lib.rs_end_acquisition(camera))


class SpinnakerBackend(CamBackend):
    """Vendor-native backend; one instance should live in each sensor worker."""

    def __init__(
        self,
        allowed_serials: Iterable[str],
        *,
        dll_path: str | Path | None = None,
        api_factory: Callable[[str | Path | None], Any] = _NativeApi,
    ) -> None:
        self._allowed = frozenset(str(serial) for serial in allowed_serials)
        self._api = api_factory(dll_path)
        self._system = self._api.system_create()
        self._handles: list[SpinnakerHandle] = []
        self._closed = False

    def _present(self) -> set[str]:
        if self._closed:
            raise RuntimeError("backend is closed")
        return set(self._api.enumerate(self._system))

    def enumerate(self) -> list[str]:
        return sorted(self._present() & self._allowed)

    def open(self, serial: str) -> CamHandle:
        serial = str(serial)
        if serial not in self._allowed:
            raise PermissionError(f"Serial is outside the configured rig: {serial}")
        if serial not in self._present():
            raise RuntimeError(f"Configured rig serial is missing: {serial}")
        handle = SpinnakerHandle(serial, self._api, self._api.camera_open(self._system, serial))
        self._handles.append(handle)
        return handle

    def close(self) -> None:
        if self._closed:
            return
        for handle in reversed(self._handles):
            handle.close()
        self._handles.clear()
        self._api.system_destroy(self._system)
        self._closed = True


class SpinnakerHandle(CamHandle):
    def __init__(self, serial: str, api: Any, camera: Any) -> None:
        self.serial = serial
        self._api = api
        self._camera = camera
        self._acquiring = False
        self._closed = False
        self._last_frame_id: int | None = None

    def node_read(self, name: str) -> Any:
        return self._api.node_read(self._camera, name, False)

    def node_read_nocache(self, name: str) -> Any:
        return self._api.node_read(self._camera, name, True)

    def node_write(self, name: str, value: Any) -> None:
        self._api.node_write(self._camera, name, value)

    def nodemap_dump(self) -> dict[str, dict[str, Any]]:
        return self._api.nodemap_dump(self._camera)

    def begin_acquisition(self, buffers: int) -> None:
        if self._acquiring:
            raise RuntimeError("Acquisition already active")
        if buffers < 1:
            raise ValueError("buffers must be positive")
        self.node_write("TLStream:StreamBufferCountMode", "Manual")
        self.node_write("TLStream:StreamBufferCountManual", int(buffers))
        self.node_write("TLStream:StreamBufferHandlingMode", "OldestFirst")
        if self.node_read_nocache("TLStream:StreamBufferHandlingMode") != "OldestFirst":
            raise RuntimeError("Recording requires OldestFirst stream handling")
        self._api.begin(self._camera)
        self._last_frame_id = None
        self._acquiring = True

    @staticmethod
    def _destination(destination: np.ndarray) -> np.ndarray:
        target = np.asarray(destination)
        if target.dtype != np.uint8 or not target.flags.c_contiguous or not target.flags.writeable:
            raise ValueError("destination must be writable, C-contiguous uint8 storage")
        return target

    def _accept_frame_id(self, frame_id: int) -> None:
        previous = self._last_frame_id
        self._last_frame_id = frame_id
        if previous is not None and frame_id != previous + 1:
            raise FrameGapError(
                f"Non-contiguous frames for {self.serial}: {previous} -> {frame_id}"
            )

    def grab_into(self, destination: np.ndarray, timeout_s: float) -> Frame:
        if not self._acquiring:
            raise RuntimeError("Acquisition is not active")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        target = self._destination(destination)
        info = self._api.grab_into(self._camera, target, timeout_s)
        expected = int(info.width) * int(info.height)
        if expected != int(info.image_bytes) or target.size < expected:
            raise RuntimeError(
                f"Native frame layout mismatch: {info.width}x{info.height}, "
                f"{info.image_bytes} bytes into {target.size}"
            )
        frame_id = int(info.frame_id)
        self._accept_frame_id(frame_id)
        return Frame(
            image=target.reshape(-1)[:expected].reshape(int(info.height), int(info.width)),
            frame_id=frame_id,
            timestamp_ns=int(info.timestamp_ns),
            exposure_us=float(info.exposure_us),
        )

    def grab(self, n: int, timeout_s: float) -> Frames:
        if n < 1 or timeout_s <= 0:
            raise ValueError("n and timeout_s must be positive")
        width = int(self.node_read_nocache("Width"))
        height = int(self.node_read_nocache("Height"))
        frames = []
        for _ in range(n):
            destination = np.empty((height, width), dtype=np.uint8)
            frames.append(self.grab_into(destination, timeout_s))
        return tuple(frames)

    def discard_pending(self, timeout_s: float) -> int:
        if not self._acquiring:
            raise RuntimeError("Acquisition is not active")
        try:
            count = int(self.node_read_nocache("TLStream:StreamOutputBufferCount"))
        except Exception:  # noqa: BLE001 - producer-specific diagnostic node
            count = 1
        discarded = 0
        for _ in range(max(0, min(count, 4096))):
            try:
                self._api.discard_one(self._camera, timeout_s)
            except TimeoutError:
                break
            discarded += 1
        self._last_frame_id = None
        return discarded

    def end_acquisition(self) -> None:
        if self._acquiring:
            self._api.end(self._camera)
            self._acquiring = False
            self._last_frame_id = None

    def close(self) -> None:
        if self._closed:
            return
        self.end_acquisition()
        self._api.camera_close(self._camera)
        self._closed = True

