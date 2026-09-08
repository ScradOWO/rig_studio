from __future__ import annotations

import struct
import zlib

import numpy as np

from rig_studio.recording.export_frames import (
    PNG_SIGNATURE,
    export_h5_png_frames,
    selected_frame_indices,
)
from rig_studio.recording.h5_writer import CamH5Writer


def _read_gray8_png(path) -> np.ndarray:
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE)
    cursor = len(PNG_SIGNATURE)
    payload = b""
    width = height = None
    while cursor < len(data):
        size = struct.unpack(">I", data[cursor:cursor + 4])[0]
        kind = data[cursor + 4:cursor + 8]
        value = data[cursor + 8:cursor + 8 + size]
        cursor += 12 + size
        if kind == b"IHDR":
            width, height = struct.unpack(">II", value[:8])
        elif kind == b"IDAT":
            payload += value
        elif kind == b"IEND":
            break
    rows = zlib.decompress(payload)
    stride = width + 1
    assert all(rows[row * stride] == 0 for row in range(height))
    return np.vstack([
        np.frombuffer(rows[row * stride + 1:(row + 1) * stride], dtype=np.uint8)
        for row in range(height)
    ])


def test_selected_frame_indices():
    assert selected_frame_indices(0, 3).tolist() == []
    assert selected_frame_indices(5, 0).tolist() == [0, 1, 2, 3, 4]
    assert selected_frame_indices(5, 3).tolist() == [0, 2, 4]


def test_export_is_pixel_exact_and_counted(tmp_path):
    source = tmp_path / "capture.h5"
    frames = np.arange(5 * 3 * 4, dtype=np.uint8).reshape(5, 3, 4)
    writer = CamH5Writer(source, camera_index=2, width=4, height=3,
                         max_frames=8, chunk_frames=2)
    writer.write_batch(frames.reshape(5, -1), np.arange(1, 6, dtype=float),
                       np.arange(100, 105, dtype=float))
    writer.close()

    report = export_h5_png_frames(source, requested_count=3)
    output = tmp_path / "capture_png"
    assert report["valid_frames"] == 5
    assert report["frames_exported"] == 3
    exported = sorted(output.glob("*.png"))
    assert len(exported) == 3
    for path, expected in zip(exported, frames[[0, 2, 4]], strict=True):
        assert np.array_equal(_read_gray8_png(path), expected)

