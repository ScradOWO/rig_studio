"""Triangulate SLEAP 2D tracks from the 6 cameras into board-mm 3D tracks.

    python triangulate3d.py --trial trial2_20260825_172019
    python triangulate3d.py --trial <TAG> --calib <CALIB_TAG> --min-score 0.3

Inputs (single fish, single-instance SLEAP models):
- SLEAP analysis exports: one .analysis.h5 PER VIDEO (File -> Export Analysis
  HDF5). It does not matter how videos were grouped into SLEAP projects
  (all 4 tops in one project, sides in others) — each export names its video,
  and cameras are matched by the `cam<N>` token in the filename. Default
  search dir: Desktop/Trials/<trial>/.
- The trial's recording H5s (for /timestamps): mp4 frame i == H5 valid frame i,
  so each camera's frames map to hardware frame ids; cameras are aligned on
  hw_frame_id, NEVER on array index (starts differ by +-2 frames).

Output (in <analysis dir>/triangulated_<calib tag>/):
  points3d.h5 (points_mm [F,nodes,3], reproj_px, n_views, hw_frame_id, t_host),
  points3d.csv (long format), qc_report.txt.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import h5py
import numpy as np

ROOTS = {0: r"D:\h5_rec_cam0", 1: r"C:\h5_rec_cam1", 2: r"D:\h5_rec_cam2",
         3: r"C:\h5_rec_cam3", 4: r"D:\h5_rec_cam4", 5: r"C:\h5_rec_cam5"}
TRIALS_DIR = Path.home() / "Desktop" / "Trials"
CALIB_DIR = Path(__file__).parent / "calibrations"
REPROJ_DROP_PX = 8.0  # with >=3 views, drop the worst view above this


def load_calibration(tag: str | None) -> tuple[str, dict[int, dict]]:
    """{cam: {'P': 3x4, 'K', 'R', 't'}} from a calibration folder (or latest)."""
    if tag:
        folder = CALIB_DIR / tag
    else:
        folder = max((d for d in CALIB_DIR.iterdir() if d.is_dir()),
                     key=lambda d: d.stat().st_mtime)
    (json_path,) = folder.glob("calibration_*.json")
    data = json.loads(json_path.read_text())
    cameras = {}
    for cid, cam in data["cameras"].items():
        K = np.array(cam["K"], float)
        R = np.array(cam["R"], float)
        t = np.array(cam["t"], float).reshape(3, 1)
        cameras[int(cid)] = {"K": K, "R": R, "t": t,
                             "P": K @ np.hstack([R, t])}
    return data["tag"], cameras


def load_analysis(path: Path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """(xy [F,nodes,2], scores [F,nodes], node_names) from a SLEAP analysis
    export; single-instance -> track 0."""
    with h5py.File(path, "r") as f:
        tracks = f["tracks"][:]           # (n_tracks, 2, n_nodes, n_frames)
        scores = f["point_scores"][:] if "point_scores" in f else None
        names = [n.decode() if isinstance(n, bytes) else str(n)
                 for n in f["node_names"][:]]
    xy = np.moveaxis(tracks[0], [0, 1, 2], [2, 1, 0])  # -> (F, nodes, 2)
    if scores is not None:
        sc = scores[0].T                   # (n_nodes, n_frames) -> (F, nodes)
    else:
        sc = np.where(np.isfinite(xy[..., 0]), 1.0, 0.0)
    return xy, sc, names


def find_analysis_files(folder: Path) -> dict[int, Path]:
    """cam id -> newest matching *.analysis.h5 (matched on the cam<N> token)."""
    found: dict[int, Path] = {}
    for path in sorted(folder.glob("*.analysis.h5"),
                       key=lambda p: p.stat().st_mtime):
        match = re.search(r"cam(\d)", path.name)
        if match:
            found[int(match.group(1))] = path
    return found


def load_frame_index(trial: str, cam: int) -> tuple[np.ndarray, np.ndarray]:
    """(hw_frame_id, t_host) for each mp4/analysis frame of one camera."""
    h5_path = Path(ROOTS[cam]) / f"{trial}_cam{cam}.h5"
    with h5py.File(h5_path, "r") as f:
        ts = f["timestamps"][:]
    n = int((ts[:, 1] > 0).sum())
    return ts[:n, 2].astype(np.int64), ts[:n, 0]


def triangulate_dlt(projections: list[np.ndarray],
                    points_px: list[np.ndarray]) -> np.ndarray:
    """DLT: 3D point (mm) from >=2 (P, xy) pairs."""
    rows = []
    for P, xy in zip(projections, points_px):
        rows.append(xy[0] * P[2] - P[0])
        rows.append(xy[1] * P[2] - P[1])
    _, _, vt = np.linalg.svd(np.asarray(rows))
    X = vt[-1]
    return X[:3] / X[3]


def reproject_px(P: np.ndarray, X: np.ndarray) -> np.ndarray:
    x = P @ np.append(X, 1.0)
    return x[:2] / x[2]


def _solve(views: list[tuple[int, np.ndarray]],
           cameras: dict[int, dict]) -> tuple[np.ndarray, list[float]]:
    Ps = [cameras[c]["P"] for c, _ in views]
    X = triangulate_dlt(Ps, [xy for _, xy in views])
    errs = [float(np.linalg.norm(reproject_px(P, X) - xy))
            for P, (_, xy) in zip(Ps, views)]
    return X, errs


def triangulate_views(views: list[tuple[int, np.ndarray]],
                      cameras: dict[int, dict]) -> tuple[np.ndarray, float, int]:
    """(X_mm, worst reprojection px, n_views used).

    Outlier views are removed only on majority CONSENSUS (RANSAC over camera
    pairs): every pair proposes a solution and votes inliers
    (reproj < REPROJ_DROP_PX). The largest inlier set wins if it is backed by
    more than half the views and beats every competing set of the same size
    by a clear error margin. Residual-based 'drop the worst' is wrong here —
    with near-parallel cameras a corrupted view can push its residual onto a
    clean one. When the vote is ambiguous (e.g. 3 views, 1 bad) all views are
    kept and the large error is REPORTED, never silently resolved wrong."""
    _, errs = _solve(views, cameras)
    if len(views) >= 3 and max(errs) > REPROJ_DROP_PX:
        from itertools import combinations

        candidates: dict[frozenset, float] = {}  # inlier set -> best err sum
        for i, j in combinations(range(len(views)), 2):
            X, _ = _solve([views[i], views[j]], cameras)
            all_errs = [float(np.linalg.norm(
                reproject_px(cameras[c]["P"], X) - xy)) for c, xy in views]
            inliers = frozenset(k for k, e in enumerate(all_errs)
                                if e < REPROJ_DROP_PX)
            err_sum = sum(all_errs[k] for k in inliers)
            candidates[inliers] = min(candidates.get(inliers, np.inf), err_sum)
        best_n = max(len(s) for s in candidates)
        top = sorted(((candidates[s], s) for s in candidates
                      if len(s) == best_n))
        margin_clear = len(top) == 1 or top[1][0] - top[0][0] > 1.0
        if best_n > len(views) / 2 and margin_clear:
            views = [views[k] for k in sorted(top[0][1])]
    X, errs = _solve(views, cameras)
    return X, max(errs), len(views)


def run(trial: str, calib_tag: str | None, analysis_dir: Path | None,
        min_score: float, min_views: int) -> Path:
    tag, cameras = load_calibration(calib_tag)
    folder = analysis_dir or (TRIALS_DIR / trial)
    files = find_analysis_files(folder)
    usable = sorted(set(files) & set(cameras))
    if len(usable) < min_views:
        raise SystemExit(f"only {len(usable)} analysis files with calibration "
                         f"({usable}) in {folder} — need >= {min_views}")
    data, index = {}, {}
    names = None
    for cam in usable:
        xy, sc, names = load_analysis(files[cam])
        hwid, t_host = load_frame_index(trial, cam)
        n = min(len(xy), len(hwid))  # analysis frames == mp4 frames == hwids
        data[cam] = (xy[:n], sc[:n])
        index[cam] = (hwid[:n], t_host[:n])

    all_ids = np.unique(np.concatenate([index[c][0] for c in usable]))
    frame_of = {c: dict(zip(index[c][0].tolist(), range(len(index[c][0]))))
                for c in usable}
    n_nodes = len(names)
    F = len(all_ids)
    points = np.full((F, n_nodes, 3), np.nan)
    reproj = np.full((F, n_nodes), np.nan)
    n_views = np.zeros((F, n_nodes), np.int8)
    t_host_out = np.full(F, np.nan)
    for fi, hwid in enumerate(all_ids):
        times = [index[c][1][frame_of[c][hwid]] for c in usable
                 if hwid in frame_of[c]]
        t_host_out[fi] = float(np.mean(times))
        for node in range(n_nodes):
            views = []
            for cam in usable:
                row = frame_of[cam].get(int(hwid))
                if row is None:
                    continue
                xy = data[cam][0][row, node]
                if np.all(np.isfinite(xy)) and data[cam][1][row, node] >= min_score:
                    views.append((cam, xy))
            if len(views) >= min_views:
                X, err, used = triangulate_views(views, cameras)
                points[fi, node] = X
                reproj[fi, node] = err
                n_views[fi, node] = used

    out_dir = folder / f"triangulated_{tag}"
    out_dir.mkdir(exist_ok=True)
    with h5py.File(out_dir / "points3d.h5", "w") as f:
        f.attrs["trial"] = trial
        f.attrs["calibration"] = tag
        f.attrs["frame"] = "board_mm (x 0-180 along tank, y 0-20, z DOWN: away from the top cameras; water surface ~ z=-22)"
        f.attrs["min_score"] = min_score
        f.create_dataset("points_mm", data=points)
        f.create_dataset("reproj_px", data=reproj)
        f.create_dataset("n_views", data=n_views)
        f.create_dataset("hw_frame_id", data=all_ids)
        f.create_dataset("t_host", data=t_host_out)
        f.create_dataset("node_names",
                         data=np.array(names, dtype=h5py.string_dtype()))
    with open(out_dir / "points3d.csv", "w", encoding="utf-8") as f:
        f.write("hw_frame_id,t_host,node,x_mm,y_mm,z_mm,n_views,reproj_px\n")
        for fi, hwid in enumerate(all_ids):
            for node, name in enumerate(names):
                if n_views[fi, node]:
                    x, y, z = points[fi, node]
                    f.write(f"{hwid},{t_host_out[fi]:.6f},{name},"
                            f"{x:.3f},{y:.3f},{z:.3f},"
                            f"{n_views[fi, node]},{reproj[fi, node]:.2f}\n")
    valid = n_views > 0
    lines = [f"trial {trial}  calibration {tag}",
             f"cameras used: {usable}",
             f"frames {F}  nodes {names}",
             f"coverage: {valid.mean() * 100:.1f}% node-frames triangulated",
             f"median reproj: {np.nanmedian(reproj):.2f} px   "
             f"p95: {np.nanpercentile(reproj, 95):.2f} px"]
    for node, name in enumerate(names):
        pts = points[valid[:, node], node]
        if len(pts):
            lo, hi = pts.min(axis=0), pts.max(axis=0)
            lines.append(f"  {name}: {valid[:, node].mean() * 100:5.1f}%  "
                         f"x {lo[0]:.1f}..{hi[0]:.1f}  y {lo[1]:.1f}..{hi[1]:.1f}"
                         f"  z {lo[2]:.1f}..{hi[2]:.1f} mm")
    report = "\n".join(lines)
    (out_dir / "qc_report.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {out_dir}")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trial", required=True)
    parser.add_argument("--calib", default=None,
                        help="calibration tag (default: newest)")
    parser.add_argument("--analysis-dir", type=Path, default=None,
                        help="folder with .analysis.h5 (default Trials/<trial>)")
    parser.add_argument("--min-score", type=float, default=0.2)
    parser.add_argument("--min-views", type=int, default=2)
    args = parser.parse_args()
    run(args.trial, args.calib, args.analysis_dir, args.min_score,
        args.min_views)


if __name__ == "__main__":
    main()
