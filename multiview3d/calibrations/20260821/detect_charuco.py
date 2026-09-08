"""Detect the 45x5 ChArUco strip in the static-board captures, per camera."""
import glob
import json
import os

import cv2
import numpy as np

BOARD_COLS, BOARD_ROWS = 45, 5
SQUARE_MM, MARKER_MM = 4.0, 2.8
OUT = os.path.dirname(os.path.abspath(__file__))

FOLDERS = {
    0: r"D:\h5_rec_cam0\run_20260821_155804_cam0_png",
    1: r"C:\h5_rec_cam1\run_20260821_155804_cam1_png",
    2: r"D:\h5_rec_cam2\run_20260821_155804_cam2_png",
    3: r"C:\h5_rec_cam3\run_20260821_155804_cam3_png",
}


def main() -> None:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250)
    board = cv2.aruco.CharucoBoard((BOARD_COLS, BOARD_ROWS), SQUARE_MM, MARKER_MM,
                                   dictionary)
    detector = cv2.aruco.CharucoDetector(board)
    report = {}
    for cam, folder in FOLDERS.items():
        paths = sorted(glob.glob(os.path.join(folder, "*.png")))
        if not paths:
            report[cam] = {"error": "no pngs"}
            continue
        # static scene: temporal mean denoises hard
        stack = np.stack([cv2.imread(p, cv2.IMREAD_GRAYSCALE).astype(np.float32)
                          for p in paths])
        mean = stack.mean(axis=0)
        mean_u8 = np.clip(mean, 0, 255).astype(np.uint8)
        # also try CLAHE for contrast through water
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(16, 16)).apply(mean_u8)
        best = None
        for label, img in (("mean", mean_u8), ("clahe", clahe)):
            corners, ids, mk_corners, mk_ids = detector.detectBoard(img)
            n = 0 if ids is None else len(ids)
            if best is None or n > best[0]:
                best = (n, label, corners, ids, mk_ids, img)
        n, label, corners, ids, mk_ids, img = best
        row = {
            "frames": len(paths),
            "shape": list(stack.shape[1:]),
            "variant": label,
            "n_charuco_corners": int(n),
            "n_markers": 0 if mk_ids is None else int(len(mk_ids)),
        }
        if n:
            id_list = sorted(int(v) for v in ids.ravel())
            xs = corners[:, 0, 0]
            ys = corners[:, 0, 1]
            # column index of each corner on the 44x4 interior grid
            cols = [v % (BOARD_COLS - 1) for v in id_list]
            row.update({
                "corner_id_min": id_list[0], "corner_id_max": id_list[-1],
                "board_col_min": min(cols), "board_col_max": max(cols),
                "px_bbox": [float(xs.min()), float(ys.min()),
                            float(xs.max()), float(ys.max())],
            })
            # per-frame jitter of a few corners
            jit = []
            for p in paths[:10]:
                f = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
                c2, i2, _, _ = detector.detectBoard(f)
                if i2 is not None and len(i2):
                    common = np.intersect1d(ids.ravel(), i2.ravel())
                    if len(common):
                        a = {int(i): c for i, c in zip(ids.ravel(), corners[:, 0])}
                        b = {int(i): c for i, c in zip(i2.ravel(), c2[:, 0])}
                        d = [np.linalg.norm(a[i] - b[i]) for i in common]
                        jit.append(float(np.mean(d)))
            row["mean_jitter_px"] = float(np.mean(jit)) if jit else None
            # save overlay
            vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            cv2.aruco.drawDetectedCornersCharuco(vis, corners, ids, (0, 0, 255))
            cv2.imwrite(os.path.join(OUT, f"overlay_cam{cam}.png"), vis)
        report[cam] = row
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
