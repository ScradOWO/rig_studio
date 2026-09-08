"""Per-camera HDF5 writer, byte-compatible with the MEX layout:

/images      uint8   [max_frames, width*height]   chunks [chunk_frames, width*height]
/timestamps  float64 [max_frames, 3]              cols: t_host_s, writer_seq (1-based), hw_frame_id
root attrs (uint64): camera_index, max_frames, image_width, image_height

Unwritten chunks read back as zeros, so "first all-zero timestamp row = end of
valid data" holds exactly as it does for the MEX's preallocated datasets.
"""
from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

ZSTD_FILTER_ID = 32015


class CamH5Writer:
    def __init__(
        self,
        path: str | Path,
        *,
        camera_index: int,
        width: int,
        height: int,
        max_frames: int,
        chunk_frames: int = 32,
        zstd_level: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.width = int(width)
        self.height = int(height)
        self.max_frames = int(max_frames)
        self.frame_bytes = self.width * self.height
        self._written = 0
        create_kwargs: dict = {}
        if zstd_level is not None:
            import hdf5plugin  # registers filter 32015

            create_kwargs.update(hdf5plugin.Zstd(clevel=int(zstd_level)))
        self._file = h5py.File(self.path, "w", rdcc_nbytes=64 * 2**20, rdcc_nslots=4001)
        self._images = self._file.create_dataset(
            "images",
            shape=(self.max_frames, self.frame_bytes),
            dtype=np.uint8,
            chunks=(min(int(chunk_frames), self.max_frames), self.frame_bytes),
            **create_kwargs,
        )
        self._timestamps = self._file.create_dataset(
            "timestamps",
            shape=(self.max_frames, 3),
            dtype=np.float64,
            chunks=(min(1024, self.max_frames), 3),
        )
        for name, value in (
            ("camera_index", camera_index),
            ("max_frames", self.max_frames),
            ("image_width", self.width),
            ("image_height", self.height),
        ):
            self._file.attrs.create(name, np.uint64(value), dtype=np.uint64)

    @property
    def frames_written(self) -> int:
        return self._written

    @property
    def full(self) -> bool:
        return self._written >= self.max_frames

    def write_batch(
        self,
        images: np.ndarray,
        t_host_s: np.ndarray,
        hw_frame_ids: np.ndarray,
    ) -> int:
        """Append up to len(images) frames; silently truncates at max_frames.

        images: uint8 [k, frame_bytes]; returns frames actually written.
        """
        k = min(len(images), self.max_frames - self._written)
        if k <= 0:
            return 0
        if images.shape[1] != self.frame_bytes:
            raise ValueError(
                f"frame size mismatch: got {images.shape[1]}, expected {self.frame_bytes}"
            )
        start = self._written
        self._images[start:start + k] = images[:k]
        rows = np.empty((k, 3), np.float64)
        rows[:, 0] = t_host_s[:k]
        rows[:, 1] = np.arange(start + 1, start + k + 1, dtype=np.float64)  # 1-based seq
        rows[:, 2] = hw_frame_ids[:k]
        self._timestamps[start:start + k] = rows
        self._written += k
        return k

    def write(self, image: np.ndarray, t_host_s: float, hw_frame_id: int) -> int:
        return self.write_batch(
            image.reshape(1, -1),
            np.asarray([t_host_s], np.float64),
            np.asarray([hw_frame_id], np.float64),
        )

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None  # type: ignore[assignment]
