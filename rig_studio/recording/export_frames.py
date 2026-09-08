"""Lossless Mono8 PNG export from the authoritative raw HDF5 recording."""
from __future__ import annotations

import binascii
import struct
import zlib
from pathlib import Path

import h5py
import numpy as np

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF))


def write_gray8_png(path: str | Path, image: np.ndarray) -> None:
    """Write an exact 8-bit grayscale array using only lossless PNG filters."""
    frame = np.ascontiguousarray(image, dtype=np.uint8)
    if frame.ndim != 2:
        raise ValueError(f"expected a 2D Mono8 frame, got shape {frame.shape}")
    height, width = frame.shape
    scanlines = b"".join(b"\0" + row.tobytes() for row in frame)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    data = (PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(scanlines, 6))
            + _chunk(b"IEND", b""))
    Path(path).write_bytes(data)


def selected_frame_indices(valid_count: int, requested_count: int) -> np.ndarray:
    """Evenly sample a recording; requested_count=0 exports every valid frame."""
    valid_count = int(valid_count)
    requested_count = int(requested_count)
    if valid_count < 0 or requested_count < 0:
        raise ValueError("frame counts must be non-negative")
    if valid_count == 0:
        return np.empty(0, dtype=np.int64)
    if requested_count == 0 or requested_count >= valid_count:
        return np.arange(valid_count, dtype=np.int64)
    return np.rint(np.linspace(0, valid_count - 1, requested_count)).astype(np.int64)


def export_h5_png_frames(path: str | Path, requested_count: int = 0,
                         output_dir: str | Path | None = None) -> dict:
    """Export raw valid HDF5 rows to a new sibling PNG directory."""
    source = Path(path)
    destination = (Path(output_dir) if output_dir is not None
                   else source.with_name(f"{source.stem}_png"))
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with h5py.File(source, "r") as handle:
            width = int(handle.attrs["image_width"])
            height = int(handle.attrs["image_height"])
            timestamps = handle["timestamps"]
            valid_rows = np.flatnonzero(np.any(timestamps[:] != 0.0, axis=1))
            choices = selected_frame_indices(len(valid_rows), requested_count)
            exported = []
            for number, choice in enumerate(choices):
                source_index = int(valid_rows[int(choice)])
                frame_id = int(round(float(timestamps[source_index, 2])))
                frame = np.asarray(handle["images"][source_index], dtype=np.uint8).reshape(
                    height, width)
                name = (f"frame{number:06d}_source{source_index:06d}_"
                        f"fid{frame_id:010d}.png")
                write_gray8_png(destination / name, frame)
                exported.append(name)
    except Exception:
        try:
            destination.rmdir()  # only removes an empty failed destination
        except OSError:
            pass
        raise
    return {
        "source": str(source),
        "output_dir": str(destination),
        "valid_frames": int(len(valid_rows)),
        "frames_exported": len(exported),
        "files": exported,
    }

