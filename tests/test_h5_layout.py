from __future__ import annotations

import h5py
import numpy as np
import pytest

from rig_studio.recording.h5_writer import ZSTD_FILTER_ID, CamH5Writer

W, H = 20, 10  # tiny frames; layout rules are size-independent


def _write_file(path, *, max_frames=64, zstd_level=None, frames=5):
    writer = CamH5Writer(path, camera_index=3, width=W, height=H,
                        max_frames=max_frames, chunk_frames=32, zstd_level=zstd_level)
    images = np.arange(frames * W * H, dtype=np.uint64).reshape(frames, W * H) % 251
    images = images.astype(np.uint8)
    written = writer.write_batch(images,
                                 np.linspace(0.0, 0.1, frames),
                                 np.arange(100, 100 + frames, dtype=np.float64))
    writer.close()
    return images, written


def test_mex_layout(tmp_path):
    path = tmp_path / "rec_cam3.h5"
    images, written = _write_file(path)
    assert written == 5
    with h5py.File(path, "r") as f:
        assert set(f.keys()) == {"images", "timestamps"}
        assert f["images"].dtype == np.uint8
        assert f["images"].shape == (64, W * H)
        assert f["images"].chunks == (32, W * H)
        assert f["timestamps"].dtype == np.float64
        assert f["timestamps"].shape == (64, 3)
        for name, value in (("camera_index", 3), ("max_frames", 64),
                            ("image_width", W), ("image_height", H)):
            assert f.attrs[name] == value
            assert f.attrs[name].dtype == np.uint64
        assert np.array_equal(f["images"][:5], images)
        ts = f["timestamps"][:]
        assert np.array_equal(ts[:5, 1], np.arange(1, 6))  # writer_seq 1-based
        assert np.array_equal(ts[:5, 2], np.arange(100, 105))
        # unwritten rows are zeros -> "first all-zero timestamp row = end of data"
        assert not ts[5:].any()
        valid = int(np.argmax(~ts.any(axis=1)))
        assert valid == 5


def test_truncates_at_max_frames(tmp_path):
    path = tmp_path / "full_cam0.h5"
    writer = CamH5Writer(path, camera_index=0, width=W, height=H,
                        max_frames=3, chunk_frames=32)
    images = np.zeros((5, W * H), np.uint8)
    assert writer.write_batch(images, np.zeros(5), np.zeros(5)) == 3
    assert writer.full
    assert writer.write_batch(images, np.zeros(5), np.zeros(5)) == 0
    writer.close()


def test_frame_size_mismatch_rejected(tmp_path):
    writer = CamH5Writer(tmp_path / "bad.h5", camera_index=0, width=W, height=H,
                        max_frames=4)
    with pytest.raises(ValueError, match="frame size"):
        writer.write_batch(np.zeros((1, 7), np.uint8), np.zeros(1), np.zeros(1))
    writer.close()


def test_zstd_filter_applied(tmp_path):
    pytest.importorskip("hdf5plugin")
    path = tmp_path / "z_cam1.h5"
    images, _ = _write_file(path, zstd_level=3)
    with h5py.File(path, "r") as f:
        plist = f["images"].id.get_create_plist()
        filters = [plist.get_filter(i)[0] for i in range(plist.get_nfilters())]
        assert ZSTD_FILTER_ID in filters
        assert np.array_equal(f["images"][:5], images)  # roundtrip through the filter
