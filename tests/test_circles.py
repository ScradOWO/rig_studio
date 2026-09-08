"""Circle-stimulus geometry: .ass parsing, row modes, patch rendering."""
from pathlib import Path

import numpy as np
import pytest

from rig_studio.stimulus import circles as ci

CIRCLE_DRAWING = ("m -100 -100 b -45 -155 45 -155 100 -100 b 155 -45 155 45 "
                  "100 100 b 46 155 -45 155 -100 100 b -155 45 -155 -45 -100 -100")
HEADER = "Dialogue: 1,0:00:00.00,0:00:05.00,Default,,0,0,0,,"


def _ass(lines: list[str]) -> str:
    return "[Events]\n" + "\n".join(lines) + "\n"


def _line(x: float, y: float, fscx: float = 43, fscy: float = 16) -> str:
    return (f"{HEADER}{{\\an7\\bord0\\shad0\\blur1\\p1\\fscx{fscx}\\fscy{fscy}"
            f"\\pos({x},{y})}}{CIRCLE_DRAWING}")


def test_parse_recovers_center_and_radii(tmp_path: Path):
    path = tmp_path / "c.ass"
    path.write_text(_ass([_line(117.82, 613.81)]), encoding="utf-8")
    (el,) = ci.parse_ass(path)
    # bezier circle extent = +-141.25 drawing units; libass puts the drawing
    # origin at \pos (measured against an ffmpeg/libass render), so the
    # origin-centered circle's center IS \pos
    assert el["rx"] == pytest.approx(141.25 * 0.43, abs=0.01)
    assert el["ry"] == pytest.approx(141.25 * 0.16, abs=0.01)
    assert el["cx"] == pytest.approx(117.82, abs=0.01)
    assert el["cy"] == pytest.approx(613.81, abs=0.01)


def test_parse_real_placement_file():
    path = Path(__file__).parents[1] / "configs" / "circles.ass"
    elements = ci.parse_ass(path)
    assert len(elements) == 24
    rows = sorted({round(e["cy"], 1) for e in elements})
    assert len(rows) == 2  # one row per tank wall
    aspect = elements[0]["ry"] / elements[0]["rx"]
    assert aspect == pytest.approx(16 / 43, rel=0.01)  # squish preserved


def test_assign_modes_splits_rows():
    elements = [_el(cy=636.0), _el(cy=998.0)]
    top, bottom = ci.assign_modes(elements, "top")
    assert (top["mode"], bottom["mode"]) == (1, -1)
    top, bottom = ci.assign_modes(elements, "bottom")
    assert (top["mode"], bottom["mode"]) == (-1, 1)


def test_prepare_rows_per_wall_size_pins_rim_edge():
    elements = [_el(cy=636.0), _el(cy=998.0)]
    top, bottom = sorted(ci.prepare_rows(elements, size_top=1.5,
                                         size_bottom=0.7),
                         key=lambda e: e["cy"])
    assert (top["rx"], top["ry"]) == (90.0, 33.0)
    assert top["ry"] / top["rx"] == pytest.approx(22 / 60)  # squish preserved
    # rim-side edge pinned: top edge of top row, bottom edge of bottom row
    assert top["cy"] - top["ry"] == pytest.approx(636.0 - 22.0)
    assert bottom["rx"] == pytest.approx(42.0)
    assert bottom["cy"] + bottom["ry"] == pytest.approx(998.0 + 22.0)


def test_prepare_rows_nth_keeps_alternates_and_rects():
    elements = ([_el(cx=x, cy=636.0) for x in (100, 200, 300, 400)]
                + [_el(cx=x, cy=998.0) for x in (100, 200)]
                + [_el(cx=960, cy=636.0, shape="rect")])
    kept = ci.prepare_rows(elements, nth_top=2, nth_bottom=1)
    top_xs = sorted(e["cx"] for e in kept
                    if e["row"] == "top" and e["shape"] == "ellipse")
    assert top_xs == [100, 300]                       # alternates, from left
    assert len([e for e in kept if e["row"] == "bottom"]) == 2  # untouched
    rect = next(e for e in kept if e["shape"] == "rect")
    assert (rect["rx"], rect["ry"]) == (60.0, 22.0)   # rects never resize


def test_scales_sawtooth():
    assert ci.scales(0.0, 2.0) == (0.0, 1.0)
    se, sc = ci.scales(1.0, 2.0)
    assert (se, sc) == (0.5, 0.5)
    se, sc = ci.scales(2.5, 2.0)  # wraps
    assert se == pytest.approx(0.25) and sc == pytest.approx(0.75)


def _el(**kw) -> dict:
    # no "row" key: split_rows must assign it (and keeps it if already set)
    base = {"cx": 200.0, "cy": 700.0, "rx": 60.0, "ry": 22.0, "mode": 1,
            "shape": "ellipse", "color": "#000000"}
    return {**base, **kw}


def test_stamp_fill_colors_center_only():
    rect = (0, 540, 1920, 1080)
    patches = ci.build_patches([_el(color="#ff0000")], rect)
    frame = np.full((1920, 540, 3), 255, np.uint8)  # x-major, region-local
    ci.stamp_fill(frame, patches, 1.0, 1.0)
    assert tuple(frame[200, 160]) == (255, 0, 0)    # center (y = 700-540)
    assert frame[200 + 70, 160, 1] == 255           # outside rx untouched
    assert frame[200, 160 + 30, 1] == 255           # outside ry untouched
    # per-wall overrides win over the .ass color (this element is row 'top')
    frame[:] = 255
    ci.stamp_fill(frame, patches, 1.0, 1.0,
                  top_rgb=ci.hex_to_rgb("#0000ff"),
                  bottom_rgb=ci.hex_to_rgb("#00ff00"))
    assert tuple(frame[200, 160]) == (0, 0, 255)


def test_stamp_rect_shape_fills_corners():
    rect = (0, 540, 1920, 1080)
    patches = ci.build_patches([_el(shape="rect", rx=50.0, ry=20.0)], rect)
    frame = np.full((1920, 540, 3), 255, np.uint8)
    ci.stamp_fill(frame, patches, 1.0, 1.0, soft_px=0.5)
    # a rect covers its corners; an ellipse would leave them white
    assert tuple(frame[200 - 45, 160 - 17]) == (0, 0, 0)


def test_row_split_survives_unequal_decimation():
    # 12+12 circles + 2 bands, top decimated to 6: a median split mis-tagged
    # every circle 'top' (regression: red/green walls rendered same color)
    elements = ([_el(cx=x, cy=613.81) for x in range(0, 1200, 100)]
                + [_el(cx=x, cy=976.0) for x in range(0, 1200, 100)]
                + [_el(cx=910, cy=567.0, shape="rect"),
                   _el(cx=910, cy=1028.0, shape="rect")])
    prepared = ci.assign_modes(ci.prepare_rows(elements, 1.5, 0.7, 2, 1), "top")
    rows = {e["row"] for e in prepared if e["shape"] == "ellipse"
            and e["cy"] > 800}
    assert rows == {"bottom"}
    assert {e["row"] for e in prepared if e["shape"] == "ellipse"
            and e["cy"] < 800} == {"top"}


def test_rects_are_inert_backdrops():
    rect = (0, 540, 1920, 1080)
    patches = ci.build_patches(
        [_el(shape="rect", color="#636363"), _el(cx=400.0)], rect)
    frame = np.full((1920, 540, 3), 255, np.uint8)
    # pulse scales at 0: circles vanish, the rect stays at full size...
    ci.stamp_fill(frame, patches, 0.0, 0.0, soft_px=0.5,
                  top_rgb=ci.hex_to_rgb("#ff0000"))
    assert tuple(frame[200, 160]) == (0x63, 0x63, 0x63)
    # ...and the per-wall color override recolors only the circle
    ci.stamp_fill(frame, patches, 1.0, 1.0, soft_px=0.5,
                  top_rgb=ci.hex_to_rgb("#ff0000"))
    assert tuple(frame[200, 160]) == (0x63, 0x63, 0x63)  # rect keeps .ass gray
    assert tuple(frame[400, 160]) == (255, 0, 0)         # circle recolored


def test_stamp_window_reveals_grating():
    rect = (0, 540, 1920, 1080)
    patches = ci.build_patches([_el()], rect)
    grating = np.full((1920, 540, 3), 200, np.uint8)
    out = np.full((1920, 540, 3), 99, np.uint8)  # window background
    ci.stamp_window(out, grating, patches, 1.0, 1.0)
    assert tuple(out[200, 160]) == (200, 200, 200)  # grating inside shape
    assert tuple(out[200 + 70, 160]) == (99, 99, 99)  # bg outside


def test_stamp_zero_scale_is_noop():
    rect = (0, 540, 1920, 1080)
    patches = ci.build_patches([_el()], rect)
    frame = np.full((1920, 540, 3), 255, np.uint8)
    ci.stamp_fill(frame, patches, 0.0, 0.0)
    assert frame.min() == 255


def test_patch_clipped_to_region():
    rect = (0, 540, 1920, 1080)
    inside = _el(cx=100.0, cy=545.0)
    outside = _el(cx=100.0, cy=100.0)
    patches = ci.build_patches([inside, outside], rect)
    assert len(patches) == 1
    assert patches[0]["y0"] == 0  # clipped at region top
    frame = np.full((1920, 540, 3), 255, np.uint8)
    ci.stamp_fill(frame, patches, 1.0, 1.0)  # must not raise / write OOB


def test_parse_floor_visible_rects_and_colors():
    path = Path(__file__).parents[1] / "configs" / "floor_visible.ass"
    elements = ci.parse_ass(path)  # Comment lines must be ignored
    assert len(elements) == 2
    top, bottom = sorted(elements, key=lambda e: e["cy"])
    # \an5 rects: bbox center at \pos (verified vs libass render)
    assert (top["cx"], top["cy"]) == (909.82, 567.0)
    assert top["shape"] == "rect" and top["color"] == "#636363"
    assert top["rx"] == pytest.approx(2360 / 2) and top["ry"] == pytest.approx(167 / 2)
    assert (bottom["cy"], bottom["ry"]) == (1028.0, pytest.approx(232 / 2))
