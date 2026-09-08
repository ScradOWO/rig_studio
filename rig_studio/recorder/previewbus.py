"""Per-camera live-preview channel over multiprocessing.shared_memory.

Triple-buffered seqlock: the worker's fetch thread publishes a decimated frame
into the next slot (never blocking), the GUI reads the latest complete slot and
retries if a write raced it. Layout:

  header (64 bytes): u64 seq | u32 width | u32 height | u32 slot | u32 pad
                     f64 t_wall_s | u64 frame_id | f64 fps_estimate
  slots: 3 x slot_bytes (uint8 image, row-major, decimated)

seq odd = write in progress; seq even = slot/meta consistent.
"""
from __future__ import annotations

import struct
import time
from multiprocessing import shared_memory

import numpy as np

_HEADER = struct.Struct("<QIIII d Q d")
_HEADER_BYTES = 64
_SLOTS = 3


def _slot_bytes(max_width: int, max_height: int, decimate: int) -> int:
    return -(-max_width // decimate) * (-(-max_height // decimate))


def shm_name(rig_name: str, position: int) -> str:
    return f"rigstudio_{rig_name}_prev{position}"


class PreviewPublisher:
    def __init__(self, name: str, *, max_width: int = 2048, max_height: int = 2048,
                 decimate: int = 2, min_interval_s: float = 0.04) -> None:
        self.decimate = max(1, int(decimate))
        self._slot_bytes = _slot_bytes(max_width, max_height, self.decimate)
        size = _HEADER_BYTES + _SLOTS * self._slot_bytes
        try:
            self._shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        except FileExistsError:
            self._shm = shared_memory.SharedMemory(name=name)  # stale from a crash
        self._seq = 0
        self._slot = 0
        self._last_publish = 0.0
        self._last_frame_t = 0.0
        self._fps = 0.0
        self._min_interval = float(min_interval_s)

    def maybe_publish(self, image: np.ndarray, frame_id: int) -> bool:
        """Throttled, non-blocking publish; skips when the GUI is up to date enough."""
        now = time.perf_counter()
        if self._last_frame_t:
            dt = now - self._last_frame_t
            if dt > 0:
                self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)
        self._last_frame_t = now
        if now - self._last_publish < self._min_interval:
            return False
        self._last_publish = now
        small = image[::self.decimate, ::self.decimate]
        small = np.ascontiguousarray(small)
        if small.nbytes > self._slot_bytes:
            return False
        self._slot = (self._slot + 1) % _SLOTS
        self._seq += 1  # odd: writing
        buf = self._shm.buf
        _HEADER.pack_into(buf, 0, self._seq, small.shape[1], small.shape[0],
                          self._slot, 0, time.time(), int(frame_id), self._fps)
        start = _HEADER_BYTES + self._slot * self._slot_bytes
        buf[start:start + small.nbytes] = small.tobytes()
        self._seq += 1  # even: consistent
        _HEADER.pack_into(buf, 0, self._seq, small.shape[1], small.shape[0],
                          self._slot, 0, time.time(), int(frame_id), self._fps)
        return True

    def close(self, unlink: bool = True) -> None:
        self._shm.close()
        if unlink:
            try:
                self._shm.unlink()
            except FileNotFoundError:
                pass


class PreviewReader:
    def __init__(self, name: str, *, max_width: int = 2048, max_height: int = 2048,
                 decimate: int = 2) -> None:
        self._name = name
        self._slot_bytes = _slot_bytes(max_width, max_height, max(1, int(decimate)))
        self._shm: shared_memory.SharedMemory | None = None
        self._last_seq = 0

    def _attach(self) -> bool:
        if self._shm is not None:
            return True
        try:
            self._shm = shared_memory.SharedMemory(name=self._name)
            return True
        except FileNotFoundError:
            return False

    def read_latest(self) -> dict | None:
        """Newest complete frame since the last call, or None. Copies out."""
        if not self._attach():
            return None
        buf = self._shm.buf
        for _ in range(4):  # seqlock retries
            seq0, width, height, slot, _, t_wall, frame_id, fps = _HEADER.unpack_from(buf, 0)
            if seq0 % 2 or seq0 == self._last_seq or width == 0:
                if seq0 == self._last_seq:
                    return None
                continue
            start = _HEADER_BYTES + slot * self._slot_bytes
            image = np.frombuffer(buf, np.uint8, width * height, start).reshape(height, width).copy()
            seq1 = _HEADER.unpack_from(buf, 0)[0]
            if seq1 == seq0:
                self._last_seq = seq0
                return {"image": image, "frame_id": frame_id, "t_wall": t_wall, "fps": fps}
        return None

    def close(self) -> None:
        if self._shm is not None:
            self._shm.close()
            self._shm = None
