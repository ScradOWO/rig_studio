"""Render click-reference images for the side-camera manual PnP."""
import glob
import os

import cv2
import numpy as np

OUT = os.path.dirname(os.path.abspath(__file__))
PX_PER_MM = 25  # render scale: 4mm square -> 100 px

board = cv2.aruco.CharucoBoard((45, 5), 4.0, 2.8,
                               cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250))
full = board.generateImage((45 * 100, 5 * 100), marginSize=0, borderBits=1)
full = cv2.cvtColor(full, cv2.COLOR_GRAY2BGR)

RED = (40, 40, 230)
GREEN = (60, 180, 60)


def mark(img, px, py, number):
    cv2.circle(img, (px, py), 14, RED, 3)
    cv2.circle(img, (px, py), 3, RED, -1)
    cv2.putText(img, str(number), (px + 18, py - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, RED, 3)


def reference_for(cam):
    # click order in the tool: (1sq in, edge A), (1sq in, edge D), (2sq in, A) ...
    if cam == 4:
        xs_mm = [176, 172, 168]          # near end = 180 mm end ("RIGHT" on the sheet)
        crop = full[:, 45 * 100 - 800:]  # last 8 squares
        offset = 45 * 100 - 800
        end_label = "NEAR END (printed sheet: RIGHT end)"
    else:
        xs_mm = [4, 8, 12]               # near end = 0 mm end ("LEFT" on the sheet)
        crop = full[:, :800]
        offset = 0
        end_label = "NEAR END (printed sheet: LEFT end)"
    img = crop.copy()
    number = 1
    for x_mm in xs_mm:
        for y_mm in (4, 16):             # the two long-edge crossing rows (A, D)
            px = x_mm * PX_PER_MM - offset
            py = y_mm * PX_PER_MM
            mark(img, px, py, number)
            number += 1
    # banner
    banner = np.full((90, img.shape[1], 3), 255, np.uint8)
    cv2.putText(banner, f"cam{cam} reference - {end_label} of the strip",
                (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (0, 0, 0), 2)
    cv2.putText(banner, "click 1..6 at the circled BLACK/WHITE crossings "
                        "(1,3,5 on one long edge; 2,4,6 on the other)",
                (14, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.75, RED, 2)
    return np.vstack([banner, img])


def camera_view(cam, folder, arrow_xy, note_xy):
    paths = sorted(glob.glob(os.path.join(folder, "*.png")))
    stack = np.stack([cv2.imread(p, cv2.IMREAD_GRAYSCALE).astype(np.float32)
                      for p in paths])
    mean = np.clip(stack.mean(0), 0, 255).astype(np.uint8)
    view = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(10, 10)).apply(mean)
    view = cv2.cvtColor(view, cv2.COLOR_GRAY2BGR)
    ax, ay = arrow_xy
    cv2.arrowedLine(view, (ax, ay + 170), (ax, ay + 30), GREEN, 6, tipLength=0.25)
    cv2.putText(view, "near pattern edge - count crossings 1,2,3 going AWAY",
                note_xy, cv2.FONT_HERSHEY_SIMPLEX, 1.0, GREEN, 3)
    return view


cv2.imwrite(os.path.join(OUT, "reference_board_cam4.png"), reference_for(4))
cv2.imwrite(os.path.join(OUT, "reference_board_cam5.png"), reference_for(5))
cv2.imwrite(os.path.join(OUT, "reference_view_cam4.png"),
            camera_view(4, r"D:\h5_rec_cam4\run_20260821_174123_cam4_png",
                        (1040, 960), (330, 1240)))
cv2.imwrite(os.path.join(OUT, "reference_view_cam5.png"),
            camera_view(5, r"C:\h5_rec_cam5\run_20260821_174123_cam5_png",
                        (1040, 960), (330, 1240)))
print("references written")
