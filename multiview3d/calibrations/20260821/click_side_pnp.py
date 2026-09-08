"""Manual PnP for the side cameras (cam4 / cam5).

The board's 3D geometry is known and the top cams already fixed it as the world
frame. The side cams see the strip's near end but the markers can't decode at
grazing angle — so YOU identify ~8 known crossings by clicking, and PnP does
the rest. Both left/right mirror hypotheses are solved; the better one wins.

Usage:  python click_side_pnp.py 4     (or 5)

Controls:  left-click = mark the point named in the console
           n = skip that point (can't see it)   z = undo   q/ESC = finish
Zoom into the window (it's resizable; use the OpenCV zoom) before clicking —
aim for the exact black/white crossing.
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

OUT = os.path.dirname(os.path.abspath(__file__))
SQ = 4.0  # mm

FOLDERS = {
    4: r"D:\h5_rec_cam4\run_20260821_174123_cam4_png",
    5: r"C:\h5_rec_cam5\run_20260821_174123_cam5_png",
}
F_NOMINAL = {4: 6000.0 / 5.5, 5: 6000.0 / 5.5}

# Which board end faces each side camera (from the top-cam detections):
# cam4 faces the x=180mm end (high columns); cam5 faces the x=0mm end.
# Points are chess crossings on the strip (z=0), listed near-edge first.
# "row A" = one long edge of the pattern, "row D" = the other long edge.
def points_for(cam: int):
    if cam == 4:
        xs = [180.0 - SQ * k for k in (1, 2, 3)]      # 176, 172, 168 mm
    else:
        xs = [SQ * k for k in (1, 2, 3)]              # 4, 8, 12 mm
    pts = []
    for i, x in enumerate(xs, start=1):
        pts.append((f"POINT {2 * i - 1} in the reference image "
                    f"({i} square(s) in from the near end, first long-edge row)",
                    (x, SQ * 1, 0.0)))
        pts.append((f"POINT {2 * i} in the reference image "
                    f"({i} square(s) in from the near end, OTHER long-edge row)",
                    (x, SQ * 4, 0.0)))
    return pts


def load_mean(cam: int):
    paths = sorted(glob.glob(os.path.join(FOLDERS[cam], "*.png")))
    stack = np.stack([cv2.imread(p, cv2.IMREAD_GRAYSCALE).astype(np.float32)
                      for p in paths])
    mean = np.clip(stack.mean(0), 0, 255).astype(np.uint8)
    return cv2.createCLAHE(clipLimit=4.0, tileGridSize=(10, 10)).apply(mean)


def solve(cam: int, clicked):
    """Try both mirror hypotheses for the row-A/row-D assignment."""
    img_pts = np.array([c["px"] for c in clicked], np.float64)
    h, w = clicked[0]["shape"]
    K = np.array([[F_NOMINAL[cam], 0, w / 2], [0, F_NOMINAL[cam], h / 2], [0, 0, 1.0]])
    best = None
    for flip in (False, True):
        obj = []
        for c in clicked:
            x, y, z = c["xyz"]
            obj.append((x, 20.0 - y if flip else y, z))
        obj = np.array(obj, np.float64)
        ok, rvec, tvec = cv2.solvePnP(obj, img_pts, K, np.zeros(5),
                                      flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            continue
        rvec, tvec = cv2.solvePnPRefineLM(obj, img_pts, K, np.zeros(5), rvec, tvec)
        proj, _ = cv2.projectPoints(obj, rvec, tvec, K, np.zeros(5))
        rms = float(np.sqrt(np.mean(np.sum((proj[:, 0] - img_pts) ** 2, axis=1))))
        R, _ = cv2.Rodrigues(rvec)
        cam_pos = (-R.T @ tvec).ravel()  # camera center in board frame, mm
        plausible = (cam_pos[2] > -30)   # not far below the board plane
        if best is None or (rms < best["rms"] and plausible):
            best = {"flip": flip, "rms": rms, "K": K.tolist(),
                    "R": R.tolist(), "t": tvec.ravel().tolist(),
                    "cam_pos_boardmm": cam_pos.tolist()}
    return best


def main() -> None:
    cam = int(sys.argv[1])
    img = load_mean(cam)
    vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    pts = points_for(cam)
    clicked = []
    state = {"i": 0}

    def prompt():
        if state["i"] < len(pts):
            print(f"\n[{state['i'] + 1}/{len(pts)}] CLICK: {pts[state['i']][0]}")
        else:
            print("\nall points done — press q to solve")

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and state["i"] < len(pts):
            label, xyz = pts[state["i"]]
            clicked.append({"px": (float(x), float(y)), "xyz": xyz,
                            "shape": img.shape, "label": label})
            cv2.circle(vis, (x, y), 4, (0, 0, 255), -1)
            cv2.putText(vis, str(state["i"] + 1), (x + 6, y - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            state["i"] += 1
            prompt()

    window = f"cam{cam} - click the named crossings (n=skip z=undo q=done)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.imshow(window, vis)   # realize the native window BEFORE hooking the mouse
    cv2.waitKey(50)
    cv2.resizeWindow(window, 1400, 1000)
    cv2.setMouseCallback(window, on_mouse)
    print(f"cam{cam}: the strip's NEAR end faces you. Count crossings from the "
          "near pattern edge. Zoom in (mouse wheel / +) before clicking.")
    prompt()
    while True:
        cv2.imshow(window, vis)
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("n") and state["i"] < len(pts):
            state["i"] += 1
            prompt()
        if key == ord("z") and clicked:
            clicked.pop()
            state["i"] -= 1
            vis[:] = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            for j, c in enumerate(clicked):
                p = tuple(int(v) for v in c["px"])
                cv2.circle(vis, p, 4, (0, 0, 255), -1)
                cv2.putText(vis, str(j + 1), (p[0] + 6, p[1] - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            prompt()
    cv2.destroyAllWindows()
    if len(clicked) < 4:
        print(f"only {len(clicked)} points — need at least 4. Nothing saved.")
        return
    result = solve(cam, clicked)
    if result is None:
        print("PnP failed — recheck the clicks.")
        return
    result["clicks"] = [{"px": c["px"], "xyz": c["xyz"]} for c in clicked]
    path = os.path.join(OUT, f"side_cam{cam}_pnp.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=1)
    print(f"\nsolved cam{cam}:  reprojection RMS {result['rms']:.2f} px  "
          f"(mirror flip: {result['flip']})")
    print(f"camera position in board frame [mm]: "
          f"{[round(v, 1) for v in result['cam_pos_boardmm']]}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
