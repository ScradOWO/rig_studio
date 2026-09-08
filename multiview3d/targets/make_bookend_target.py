"""Generate the print-ready 'bookend' calibration target (v1), 100% scale A4.

Pieces (all DICT_4X4_250, disjoint ID ranges so every camera knows what it sees):
  - CENTER STRIP: 45x5 squares, 4.00 mm / 2.80 mm markers, aruco IDs 0-111
    (identical to the proven dragstrip strip)
  - TAB A: 5x3 squares, 8.00 mm / 5.60 mm markers, IDs 120-127  (faces cam4)
  - TAB B: 5x3 squares, 8.00 mm / 5.60 mm markers, IDs 140-147  (faces cam5)

Page 1: cut-out patterns + 100 mm scale bar.  Page 2: assembly + the caliper
measurements the solver needs (fill assembly_measurements.json).
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrow, Rectangle

HERE = Path(__file__).parent
OUT_DIR = HERE / "bookend_v1"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250)
PX_PER_MM = 20  # ~508 dpi rasters embedded in the PDF

STRIP = {"squares": (45, 5), "square_mm": 4.0, "marker_mm": 2.8,
         "ids": np.arange(0, 112, dtype=np.int32)}          # FLOOR strip
STRIP_HI = {"squares": (45, 5), "square_mm": 4.0, "marker_mm": 2.8,
            "ids": np.arange(112, 224, dtype=np.int32)}     # RAISED strip (parallel)
TAB_A = {"squares": (5, 3), "square_mm": 8.0, "marker_mm": 5.6,
         "ids": np.arange(230, 237, dtype=np.int32)}
TAB_B = {"squares": (5, 3), "square_mm": 8.0, "marker_mm": 5.6,
         "ids": np.arange(240, 247, dtype=np.int32)}


def make_board(spec):
    sx, sy = spec["squares"]
    n_markers = (sx * sy) // 2
    ids = spec["ids"][:n_markers]
    board = cv2.aruco.CharucoBoard((sx, sy), spec["square_mm"], spec["marker_mm"],
                                   DICT, ids)
    w_mm, h_mm = sx * spec["square_mm"], sy * spec["square_mm"]
    img = board.generateImage((int(w_mm * PX_PER_MM), int(h_mm * PX_PER_MM)),
                              marginSize=0, borderBits=1)
    return board, img, (w_mm, h_mm), ids


def self_test(board, img, expected_ids, name):
    detector = cv2.aruco.CharucoDetector(board)
    _, _, mk_corners, mk_ids = detector.detectBoard(img)
    got = set() if mk_ids is None else set(int(v) for v in mk_ids.ravel())
    expected = set(int(v) for v in expected_ids)
    assert got == expected, f"{name}: decoded {sorted(got)} != expected {sorted(expected)}"
    print(f"self-test {name}: all {len(expected)} marker IDs decode correctly "
          f"({min(expected)}-{max(expected)})")


def place(ax, img, x_mm, y_mm, w_mm, h_mm):
    ax.imshow(img, cmap="gray", vmin=0, vmax=255, interpolation="none",
              extent=[x_mm, x_mm + w_mm, y_mm, y_mm + h_mm], zorder=2)
    pad = 1.0
    ax.add_patch(Rectangle((x_mm - pad, y_mm - pad), w_mm + 2 * pad, h_mm + 2 * pad,
                           fill=False, ls=(0, (4, 3)), lw=0.7, ec="0.4", zorder=3))


def page_axes(fig):
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 297)
    ax.set_ylim(0, 210)
    ax.set_aspect("equal")
    ax.axis("off")
    return ax


def main() -> None:
    strip_board, strip_img, strip_wh, strip_ids = make_board(STRIP)
    hi_board, hi_img, hi_wh, hi_ids = make_board(STRIP_HI)
    taba_board, taba_img, tab_wh, taba_ids = make_board(TAB_A)
    tabb_board, tabb_img, _, tabb_ids = make_board(TAB_B)
    self_test(strip_board, strip_img, strip_ids, "floor strip")
    self_test(hi_board, hi_img, hi_ids, "raised strip")
    self_test(taba_board, taba_img, taba_ids, "tab A")
    self_test(tabb_board, tabb_img, tabb_ids, "tab B")

    pdf_path = OUT_DIR / "bookend_target_v1_A4.pdf"
    with PdfPages(pdf_path) as pdf:
        # ---------- page 1: patterns ----------
        fig = plt.figure(figsize=(297 / 25.4, 210 / 25.4))
        ax = page_axes(fig)
        ax.text(10, 200, "DRAGSTRIP BOOKEND TARGET v1 — PRINT AT 100% / ACTUAL SIZE",
                fontsize=13, weight="bold", va="top")
        ax.text(10, 193, "Disable fit/shrink/scale-to-page. Toner on film or paper; "
                         "MATTE laminate each piece FLAT; cut on dashed lines.",
                fontsize=8.5, va="top", color="0.25")
        # floor strip
        place(ax, strip_img, 58, 158, *strip_wh)
        ax.text(58, 182, "FLOOR STRIP — 45x5, square 4.00 mm, marker 2.80 mm, "
                         "IDs 0-111 (lies on the tank floor)", fontsize=8, va="bottom")
        # raised strip
        place(ax, hi_img, 58, 122, *hi_wh)
        ax.text(58, 146, "RAISED STRIP — 45x5, square 4.00 mm, marker 2.80 mm, "
                         "IDs 112-223 (mounts on the spacer, parallel to the floor strip)",
                fontsize=8, va="bottom")
        # tabs
        place(ax, taba_img, 58, 80, *tab_wh)
        ax.text(58, 108, "TAB A — faces cam4 (x=180 end)\n"
                         "5x3, sq 8.00 mm, marker 5.60 mm, IDs 230-236",
                fontsize=8, va="bottom")
        place(ax, tabb_img, 175, 80, *tab_wh)
        ax.text(175, 108, "TAB B — faces cam5 (x=0 end)\n"
                          "5x3, sq 8.00 mm, marker 5.60 mm, IDs 240-246",
                fontsize=8, va="bottom")
        # scale bar
        y0 = 48
        ax.plot([58, 158], [y0, y0], color="black", lw=1.2)
        for k in range(11):
            x = 58 + 10 * k
            ax.plot([x, x], [y0, y0 + (4 if k % 5 == 0 else 2.5)], color="black", lw=1.0)
            if k % 5 == 0:
                ax.text(x, y0 + 6, str(k * 10), ha="center", fontsize=7)
        ax.text(58, y0 - 6, "100 mm print-size check — measure endpoint-to-endpoint "
                            "after printing: must be 100.0 mm", fontsize=8, va="top")
        ax.text(10, 30, "After laminating, confirm black stays dark in the RG830+IR "
                        "view before assembling.", fontsize=8.5, color="0.25")
        pdf.savefig(fig)
        plt.close(fig)

        # ---------- page 2: assembly + measurements ----------
        fig = plt.figure(figsize=(297 / 25.4, 210 / 25.4))
        ax = page_axes(fig)
        ax.text(10, 200, "ASSEMBLY (side view) + THE 7 CALIPER MEASUREMENTS",
                fontsize=13, weight="bold", va="top")
        # END-ON cross-section (looking along the tank): both strips side by side
        base_y = 125
        ax.text(60, base_y + 44, "END VIEW (looking along the tank) — every top camera "
                                 "sees BOTH heights, full length", fontsize=9, va="bottom")
        ax.add_patch(Rectangle((80, base_y), 40, 2.5, fc="0.75", ec="black", lw=0.8))
        ax.text(100, base_y - 6, "FLOOR strip (IDs 0-111)\non the tank floor",
                ha="center", fontsize=8, va="top")
        ax.add_patch(Rectangle((140, base_y), 40, 14, fc="0.92", ec="black", lw=0.8))
        ax.add_patch(Rectangle((140, base_y + 14), 40, 2.5, fc="0.75", ec="black", lw=0.8))
        ax.text(160, base_y - 6, "spacer block (~12-15 mm)\n+ RAISED strip (IDs 112-223)",
                ha="center", fontsize=8, va="top")
        ax.add_patch(FancyArrow(100, base_y + 36, 0, -10, width=0.8, color="0.3"))
        ax.add_patch(FancyArrow(160, base_y + 36, 0, -10, width=0.8, color="0.3"))
        ax.text(130, base_y + 38, "top cams look down", ha="center", fontsize=8)
        ax.text(210, base_y + 8, "TABS: vertical at both tank ends,\n"
                                 "pattern facing its side camera\n"
                                 "(A→cam4 at x=180, B→cam5 at x=0)",
                fontsize=8.5, va="center")
        ax.text(10, 95, "Both strips run the FULL tank length, side by side. Mount "
                        "everything RIGID (acrylic jig / epoxy). Tabs vertical,\n"
                        "pattern outward to their camera. Then measure with calipers:",
                fontsize=9.5, va="top")
        checklist = [
            "M1  raised-strip height: floor-strip top surface -> raised-strip top surface [mm]",
            "M2  lateral gap: floor-strip pattern edge (y=20) -> raised-strip pattern edge, across the tank [mm]",
            "M3  lengthwise shift between the two strips' x=0 pattern corners (0 if flush) [mm]",
            "M4  TAB A: horizontal gap from floor-strip x=180 pattern edge to tab A pattern plane [mm]",
            "M5  TAB A: height of tab pattern's BOTTOM edge above the floor-strip top surface [mm]",
            "M6  TAB A: sideways offset of tab pattern's left edge from the floor-strip y=0 edge [mm]",
            "M7-M9  TAB B: same three as M4-M6 on the cam5 (x=0) end [mm]",
        ]
        for i, line in enumerate(checklist):
            ax.text(14, 78 - i * 7.5, line, fontsize=9, family="monospace", va="top")
        ax.text(10, 24, "Enter the numbers into assembly_measurements.json (template "
                        "beside this PDF); the solver consumes it directly.\n"
                        "Then: submerge once, capture ~30 synced frames on all six "
                        "cameras, run the calibration scripts — no clicking, no board "
                        "movement, focal refinement included.", fontsize=9, va="top",
                color="0.2")
        pdf.savefig(fig)
        plt.close(fig)

    template = {
        "_units": "mm; see bookend_target_v1_A4.pdf page 2 for the measurement diagram",
        "raised_strip_height_M1": None,
        "lateral_gap_between_strips_M2": None,
        "lengthwise_shift_between_strips_M3": None,
        "tab_a": {"gap_from_strip_end_M4": None, "pattern_bottom_above_strip_M5": None,
                  "sideways_offset_M6": None},
        "tab_b": {"gap_from_strip_end_M7": None, "pattern_bottom_above_strip_M8": None,
                  "sideways_offset_M9": None},
        "printed_square_check": {"floor_strip_square_mm": None,
                                 "raised_strip_square_mm": None, "tab_square_mm": None,
                                 "_note": "measure 10 squares with calipers, divide by 10"},
    }
    with open(OUT_DIR / "assembly_measurements.json", "w") as f:
        json.dump(template, f, indent=1)
    for name, img in (("floor_strip", strip_img), ("raised_strip", hi_img),
                      ("tab_a", taba_img), ("tab_b", tabb_img)):
        cv2.imwrite(str(OUT_DIR / f"pattern_{name}.png"), img)
    print(f"wrote {pdf_path}")
    print(f"wrote {OUT_DIR / 'assembly_measurements.json'} (fill after assembly)")


if __name__ == "__main__":
    main()
