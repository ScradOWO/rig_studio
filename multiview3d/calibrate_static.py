"""Static-board calibration for the dragstrip rig — one command.

    python calibrate_static.py --tag charucoall_20260821_172921

Finds <root>\<tag>_camN_png for every camera on C:/D:, detects the 45x5
ChArUco strip (temporal-mean + CLAHE), solves each camera's pose in the BOARD
frame (world = strip mm), cross-validates every camera pair on shared corners
against the printed 4 mm grid, and writes
    calibrations/<tag>/calibration_<tag>.json  +  report.txt

Cameras whose markers can't auto-decode (the side cams at grazing angle) are
listed for the manual step:  python click_side_pnp.py <cam> --tag <tag>
"""
from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
BOARD_COLS, BOARD_ROWS = 45, 5
SQUARE_MM, MARKER_MM = 4.0, 2.8
F_NOMINAL_PX = 6000.0 / 5.5   # 6 mm lens / 5.5 um pitch (full-res readout)
MIN_CORNERS = 8
QC_MEAN_MM = 1.5              # fail the run if cross-cam error exceeds this

ROOTS = {0: r"D:\h5_rec_cam0", 1: r"C:\h5_rec_cam1", 2: r"D:\h5_rec_cam2",
         3: r"C:\h5_rec_cam3", 4: r"D:\h5_rec_cam4", 5: r"C:\h5_rec_cam5"}


def mean_image(folder: str) -> np.ndarray | None:
    paths = sorted(glob.glob(os.path.join(folder, "*.png")))
    if not paths:
        return None
    stack = np.stack([cv2.imread(p, cv2.IMREAD_GRAYSCALE).astype(np.float32)
                      for p in paths])
    return np.clip(stack.mean(0), 0, 255).astype(np.uint8)


def detect(image: np.ndarray, detector) -> tuple[np.ndarray, np.ndarray] | None:
    best = None
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(16, 16)).apply(image)
    for img in (image, clahe):
        corners, ids, _, _ = detector.detectBoard(img)
        n = 0 if ids is None else len(ids)
        if best is None or n > best[0]:
            best = (n, corners, ids)
    if best[0] < MIN_CORNERS:
        return None
    return best[1][:, 0, :].astype(np.float64), best[2].ravel()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True,
                        help="capture tag, e.g. charucoall_20260821_172921")
    parser.add_argument("--focal-px", type=float, default=F_NOMINAL_PX,
                        help="nominal focal length in pixels (default: full-res GS3)")
    args = parser.parse_args()

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250)
    board = cv2.aruco.CharucoBoard((BOARD_COLS, BOARD_ROWS), SQUARE_MM, MARKER_MM,
                                   dictionary)
    detector = cv2.aruco.CharucoDetector(board)
    object_points = board.getChessboardCorners()

    out_dir = HERE / "calibrations" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    report: list[str] = [f"calibration run: {args.tag}"]

    cams, missing = {}, []
    for cam, root in ROOTS.items():
        folder = os.path.join(root, f"{args.tag}_cam{cam}_png")
        image = mean_image(folder)
        if image is None:
            missing.append((cam, f"no frames at {folder}"))
            continue
        found = detect(image, detector)
        if found is None:
            missing.append((cam, "board not auto-detectable (side cam? -> manual "
                                 f"clicks: python click_side_pnp.py {cam} --tag {args.tag})"))
            continue
        px, ids = found
        h, w = image.shape
        K = np.array([[args.focal_px, 0, w / 2.0],
                      [0, args.focal_px, h / 2.0], [0, 0, 1.0]])
        obj = object_points[ids].astype(np.float64)
        ok, rvec, tvec = cv2.solvePnP(obj, px, K, np.zeros(5),
                                      flags=cv2.SOLVEPNP_ITERATIVE)
        rvec, tvec = cv2.solvePnPRefineLM(obj, px, K, np.zeros(5), rvec, tvec)
        proj, _ = cv2.projectPoints(obj, rvec, tvec, K, np.zeros(5))
        rms = float(np.sqrt(np.mean(np.sum((proj[:, 0] - px) ** 2, axis=1))))
        R, _ = cv2.Rodrigues(rvec)
        cams[cam] = {"ids": ids, "px": px, "K": K, "R": R, "t": tvec,
                     "P": K @ np.hstack([R, tvec]), "rms": rms,
                     "shape": [int(h), int(w)]}
        line = (f"cam{cam}: {len(ids)} corners, reproj RMS {rms:.2f} px, "
                f"image {w}x{h}")
        print(line)
        report.append(line)
    for cam, why in missing:
        line = f"cam{cam}: MISSING — {why}"
        print(line)
        report.append(line)

    # cross-camera validation on shared corners
    print("\ncross-camera triangulation vs the printed grid:")
    report.append("")
    all_err = []
    for a, b in itertools.combinations(sorted(cams), 2):
        shared = np.intersect1d(cams[a]["ids"], cams[b]["ids"])
        if len(shared) < 2:
            continue
        ia = {v: i for i, v in enumerate(cams[a]["ids"])}
        ib = {v: i for i, v in enumerate(cams[b]["ids"])}
        pa = np.array([cams[a]["px"][ia[s]] for s in shared]).T
        pb = np.array([cams[b]["px"][ib[s]] for s in shared]).T
        X = cv2.triangulatePoints(cams[a]["P"], cams[b]["P"], pa, pb)
        X = (X[:3] / X[3]).T
        err = np.linalg.norm(X - object_points[shared], axis=1)
        all_err.extend(err.tolist())
        line = (f"  cam{a}+cam{b}: {len(shared)} shared corners | mean "
                f"{err.mean():.3f} mm | max {err.max():.3f} mm")
        print(line)
        report.append(line)

    passed = bool(all_err) and float(np.mean(all_err)) <= QC_MEAN_MM
    summary = (f"\nOVERALL: {len(all_err)} corners, mean "
               f"{np.mean(all_err):.3f} mm — {'PASS' if passed else 'FAIL'} "
               f"(gate {QC_MEAN_MM} mm)") if all_err else "\nOVERALL: FAIL — no camera pairs"
    print(summary)
    report.append(summary)

    payload = {
        "frame": "board_mm (x 0-180 along tank, y 0-20, z DOWN: away from the top cameras; water surface ~ z=-22)",
        "tag": args.tag,
        "qc": {"pass": passed,
               "cross_cam_mean_mm": float(np.mean(all_err)) if all_err else None},
        "note": "extrinsics in board frame; nominal intrinsics, no distortion model. "
                "Valid ONLY for the camera settings used in this capture.",
        "cameras": {str(c): {"K": cams[c]["K"].tolist(), "dist": [0.0] * 5,
                             "R": cams[c]["R"].tolist(),
                             "t": cams[c]["t"].ravel().tolist(),
                             "rms_px": cams[c]["rms"],
                             "image_hw": cams[c]["shape"],
                             "n_corners": int(len(cams[c]["ids"]))}
                    for c in cams},
        "missing_cameras": [c for c, _ in missing],
    }
    with open(out_dir / f"calibration_{args.tag}.json", "w") as f:
        json.dump(payload, f, indent=1)
    (out_dir / "report.txt").write_text("\n".join(report), encoding="utf-8")
    print(f"\nwrote {out_dir / f'calibration_{args.tag}.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
