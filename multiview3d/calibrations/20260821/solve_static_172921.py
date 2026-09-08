"""Static-board calibration: solve each top cam's pose in the BOARD frame,
then validate by triangulating corners shared by adjacent cameras."""
import glob
import itertools
import json
import os

import cv2
import numpy as np

BOARD_COLS, BOARD_ROWS = 45, 5
SQUARE_MM, MARKER_MM = 4.0, 2.8
F_NOMINAL_PX = 6000.0 / 5.5  # 6 mm lens / 5.5 um pitch (full-res mode)
OUT = os.path.dirname(os.path.abspath(__file__))

FOLDERS = {
    0: r"D:\h5_rec_cam0\charucoall_20260821_172921_cam0_png",
    1: r"C:\h5_rec_cam1\charucoall_20260821_172921_cam1_png",
    2: r"D:\h5_rec_cam2\charucoall_20260821_172921_cam2_png",
    3: r"C:\h5_rec_cam3\charucoall_20260821_172921_cam3_png",
}


def detect(folder, detector):
    paths = sorted(glob.glob(os.path.join(folder, "*.png")))
    stack = np.stack([cv2.imread(p, cv2.IMREAD_GRAYSCALE).astype(np.float32)
                      for p in paths])
    mean_u8 = np.clip(stack.mean(axis=0), 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(16, 16)).apply(mean_u8)
    best = (0, None, None, None)
    for img in (mean_u8, clahe):
        corners, ids, _, _ = detector.detectBoard(img)
        n = 0 if ids is None else len(ids)
        if n > best[0]:
            best = (n, corners, ids, img.shape)
    return best[1][:, 0, :], best[2].ravel(), best[3]


def main() -> None:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250)
    board = cv2.aruco.CharucoBoard((BOARD_COLS, BOARD_ROWS), SQUARE_MM, MARKER_MM,
                                   dictionary)
    detector = cv2.aruco.CharucoDetector(board)
    object_points = board.getChessboardCorners()  # (44*4, 3) in mm, z=0

    cams = {}
    for cam, folder in FOLDERS.items():
        px, ids, shape = detect(folder, detector)
        h, w = shape
        K = np.array([[F_NOMINAL_PX, 0, w / 2.0],
                      [0, F_NOMINAL_PX, h / 2.0],
                      [0, 0, 1.0]])
        dist = np.zeros(5)
        obj = object_points[ids].astype(np.float64)
        img_pts = px.astype(np.float64)
        ok, rvec, tvec = cv2.solvePnP(obj, img_pts, K, dist,
                                      flags=cv2.SOLVEPNP_ITERATIVE)
        rvec, tvec = cv2.solvePnPRefineLM(obj, img_pts, K, dist, rvec, tvec)
        proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
        rms = float(np.sqrt(np.mean(np.sum((proj[:, 0] - img_pts) ** 2, axis=1))))
        R, _ = cv2.Rodrigues(rvec)
        P = K @ np.hstack([R, tvec])
        cams[cam] = {"ids": ids, "px": img_pts, "K": K, "R": R, "t": tvec,
                     "P": P, "rms": rms, "cam_height_mm": float(tvec[2])}
        print(f"cam{cam}: {len(ids)} corners, reproj RMS {rms:.3f} px, "
              f"board distance {float(tvec[2]):.1f} mm")

    print("\ncross-camera triangulation of SHARED corners vs true board grid:")
    all_errors = []
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
        truth = object_points[shared]
        err = np.linalg.norm(X - truth, axis=1)
        depth_err = np.abs(X[:, 2] - truth[:, 2])
        all_errors.extend(err.tolist())
        print(f"  cam{a}+cam{b}: {len(shared)} shared corners | "
              f"3D error mean {err.mean():.3f} mm, max {err.max():.3f} mm | "
              f"z-error mean {depth_err.mean():.3f} mm")

    if all_errors:
        print(f"\nOVERALL: {len(all_errors)} triangulated corners, "
              f"mean 3D error {np.mean(all_errors):.3f} mm, "
              f"95th pct {np.percentile(all_errors, 95):.3f} mm")

    out = {str(c): {"K": cams[c]["K"].tolist(), "dist": [0.0] * 5,
                    "R": cams[c]["R"].tolist(), "t": cams[c]["t"].ravel().tolist(),
                    "rms_px": cams[c]["rms"], "n_corners": int(len(cams[c]["ids"]))}
           for c in cams}
    with open(os.path.join(OUT, "calibration_tops_172921.json"), "w") as f:
        json.dump({"frame": "board_mm", "board": "45x5 charuco 4mm DICT_4X4_250",
                   "note": "extrinsics in board frame; nominal intrinsics",
                   "cameras": out}, f, indent=1)
    print("\nwrote calibration_tops_172921.json")


if __name__ == "__main__":
    main()

