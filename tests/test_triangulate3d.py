"""Triangulation core: DLT recovery, outlier-view drop, hwid alignment."""
from __future__ import annotations

import json

import numpy as np
import pytest

from multiview3d import triangulate3d as tri


def _camera(R: np.ndarray, t: np.ndarray) -> dict:
    K = np.array([[1000.0, 0, 700], [0, 1000.0, 700], [0, 0, 1]])
    t = t.reshape(3, 1)
    return {"K": K, "R": R, "t": t, "P": K @ np.hstack([R, t])}


@pytest.fixture
def rig() -> dict[int, dict]:
    # cams 0/2 look straight down -Z; cams 1/3 look along +X / -X (the sides)
    down = np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]])
    side_a = np.array([[0, 1.0, 0], [0, 0, -1.0], [1.0, 0, 0]])
    side_b = np.array([[0, -1.0, 0], [0, 0, -1.0], [-1.0, 0, 0]])
    return {0: _camera(down, np.array([0.0, 10.0, 300.0])),
            1: _camera(side_a, np.array([-10.0, 20.0, 100.0])),
            2: _camera(down, np.array([-90.0, 10.0, 300.0])),
            3: _camera(side_b, np.array([10.0, 20.0, 280.0]))}


def test_dlt_recovers_known_point(rig):
    X_true = np.array([90.0, 10.0, 12.0])
    views = [(c, tri.reproject_px(rig[c]["P"], X_true)) for c in rig]
    X, err, used = tri.triangulate_views(views, rig)
    assert np.allclose(X, X_true, atol=1e-9)
    assert err < 1e-6 and used == 4


def test_outlier_dropped_by_majority_consensus(rig):
    X_true = np.array([90.0, 10.0, 12.0])
    views = [(c, tri.reproject_px(rig[c]["P"], X_true)) for c in rig]
    views[2] = (2, views[2][1] + np.array([40.0, 0.0]))  # corrupt one of four
    X, err, used = tri.triangulate_views(views, rig)
    assert used == 3 and err < 1e-6
    assert np.allclose(X, X_true, atol=1e-6)


def test_ambiguous_three_views_kept_with_error_reported(rig):
    # 1 bad view of 3: every pair claims 2 inliers — no unique majority, so
    # nothing is dropped and the disagreement surfaces as a large error
    X_true = np.array([90.0, 10.0, 12.0])
    views = [(c, tri.reproject_px(rig[c]["P"], X_true)) for c in (0, 1, 2)]
    views[2] = (2, views[2][1] + np.array([40.0, 0.0]))
    _, err, used = tri.triangulate_views(views, rig)
    assert used == 3
    assert err > tri.REPROJ_DROP_PX


def test_two_views_never_dropped(rig):
    X_true = np.array([90.0, 10.0, 12.0])
    views = [(0, tri.reproject_px(rig[0]["P"], X_true) + 30.0),
             (1, tri.reproject_px(rig[1]["P"], X_true))]
    _, err, used = tri.triangulate_views(views, rig)
    assert used == 2  # never drops below two views; error reported instead
    assert err >= 0.0


def test_analysis_axis_order(tmp_path):
    import h5py

    xy_true = np.arange(5 * 3 * 2, dtype=float).reshape(5, 3, 2)  # F,nodes,2
    tracks = np.moveaxis(xy_true, [0, 1, 2], [2, 1, 0])[None]  # 1,2,nodes,F
    scores = np.full((1, 3, 5), 0.9)
    path = tmp_path / "x_cam0.analysis.h5"
    with h5py.File(path, "w") as f:
        f["tracks"] = tracks
        f["point_scores"] = scores
        f["node_names"] = np.array([b"head", b"mid", b"tail"])
    xy, sc, names = tri.load_analysis(path)
    assert xy.shape == (5, 3, 2) and np.array_equal(xy, xy_true)
    assert sc.shape == (5, 3) and names == ["head", "mid", "tail"]


def test_run_end_to_end_with_ragged_starts(rig, tmp_path, monkeypatch):
    """Full pipeline on synthetic data: cameras start +-2 frames apart (the
    real rig's raggedness) — alignment must use hw ids, not array index."""
    import h5py

    F, names = 40, [b"head", b"tail"]
    rng = np.random.default_rng(0)
    truth = np.stack([np.linspace([10, 5, 8], [170, 15, 20], F),
                      np.linspace([5, 5, 8], [165, 15, 18], F)], axis=1)
    starts = {0: 310, 1: 312, 2: 311, 3: 310}  # hw id of first frame per cam
    roots = {}
    for cam in rig:
        root = tmp_path / f"root{cam}"
        root.mkdir()
        roots[cam] = str(root)
        n = F - (starts[cam] - 310)
        ts = np.zeros((n + 5, 3))
        ts[:n, 0] = np.arange(n) / 120.0
        ts[:n, 1] = np.arange(1, n + 1)
        ts[:n, 2] = starts[cam] + np.arange(n)
        with h5py.File(root / f"t_cam{cam}.h5", "w") as f:
            f["timestamps"] = ts
        xy = np.full((n, 2, 2), np.nan)
        for fi in range(n):
            hw = starts[cam] + fi
            for node in range(2):
                xy[fi, node] = tri.reproject_px(rig[cam]["P"],
                                                truth[hw - 310, node])
        tracks = np.moveaxis(xy, [0, 1, 2], [2, 1, 0])[None]
        with h5py.File(tmp_path / f"t_cam{cam}.analysis.h5", "w") as f:
            f["tracks"] = tracks
            f["point_scores"] = np.full((1, 2, n), 0.9)
            f["node_names"] = np.array(names)
    monkeypatch.setattr(tri, "ROOTS", roots)
    calib_dir = tmp_path / "calibs" / "test_tag"
    calib_dir.mkdir(parents=True)
    (calib_dir / "calibration_test_tag.json").write_text(json.dumps({
        "tag": "test_tag",
        "cameras": {str(c): {"K": rig[c]["K"].tolist(),
                             "R": rig[c]["R"].tolist(),
                             "t": rig[c]["t"].ravel().tolist(),
                             "dist": [0] * 5} for c in rig}}))
    monkeypatch.setattr(tri, "CALIB_DIR", tmp_path / "calibs")
    out = tri.run("t", "test_tag", tmp_path, min_score=0.2, min_views=2)
    with h5py.File(out / "points3d.h5", "r") as f:
        points = f["points_mm"][:]
        hwids = f["hw_frame_id"][:]
        nviews = f["n_views"][:]
    assert hwids[0] == 310 and len(hwids) == F
    assert (nviews[3:] == 4).all()  # after the ragged starts, all cams vote
    err = np.nanmax(np.abs(points - truth)[nviews >= 2])
    assert err < 1e-6


def test_find_analysis_files_matches_cam_token(tmp_path):
    for name in ("labels.v001.000_trial2_cam0.analysis.h5",
                 "sides_trial2_cam4.analysis.h5",
                 "notes.txt"):
        (tmp_path / name).touch()
    found = tri.find_analysis_files(tmp_path)
    assert sorted(found) == [0, 4]
    assert found[0].name.endswith("cam0.analysis.h5")
