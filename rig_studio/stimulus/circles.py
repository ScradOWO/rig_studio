"""Operator-placed stimulus shapes (Aegisub .ass) composited over the grating.

The .ass file is the layout editor: active Dialogue lines with \\p1 drawings
become shapes (Comment lines are ignored); bezier drawings render as ellipses,
l-command drawings as rectangles. Each shape keeps its Aegisub color (\\c,
ASS BGR order) and its squish (\\fscx/\\fscy) verbatim. Placement rule,
verified against ffmpeg/libass renders of the operator's files for \\an5 and
\\an7 alike: the drawing's scaled bbox CENTER lands at \\pos.

Shapes are stamped either as opaque fills (stamp_fill) or as windows that
reveal the grating through a flat background (stamp_window). One wall row can
pulse point->full while the other pulses full->point (scales/assign_modes).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

_TAG = re.compile(r"\\(pos)\(([-\d.]+),([-\d.]+)\)|\\(fscx|fscy)([\d.]+)"
                  r"|\\1?(c)&H([0-9A-Fa-f]{6,8})&")


def hex_to_rgb(color: str) -> np.ndarray:
    color = color.lstrip("#")
    return np.array([int(color[i:i + 2], 16) for i in (0, 2, 4)], np.float64)


def _drawing_bbox(drawing: str) -> tuple[float, float, float, float, bool]:
    """(x0, y0, x1, y1, is_ellipse) of an ASS vector drawing (m/l/b)."""
    tokens = drawing.split()
    xs: list[float] = []
    ys: list[float] = []
    current = (0.0, 0.0)
    i = 0
    command = ""
    has_bezier = False
    while i < len(tokens):
        if tokens[i] in ("m", "l", "b"):
            command = tokens[i]
            i += 1
            continue
        if command == "b":
            has_bezier = True
            c = [float(v) for v in tokens[i:i + 6]]
            i += 6
            t = np.linspace(0.0, 1.0, 33)[:, None]
            pts = np.array([current, c[0:2], c[2:4], c[4:6]])
            curve = ((1 - t) ** 3 * pts[0] + 3 * (1 - t) ** 2 * t * pts[1]
                     + 3 * (1 - t) * t ** 2 * pts[2] + t ** 3 * pts[3])
            xs.extend(curve[:, 0]); ys.extend(curve[:, 1])
            current = (c[4], c[5])
        else:  # m / l
            current = (float(tokens[i]), float(tokens[i + 1]))
            i += 2
            xs.append(current[0]); ys.append(current[1])
    if not xs:
        raise ValueError("empty drawing")
    return min(xs), min(ys), max(xs), max(ys), has_bezier


def parse_ass(path: str | Path) -> list[dict]:
    """[{cx, cy, rx, ry, shape, color}] in canvas px from active \\p1 lines."""
    elements = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        text = line.split(",", 9)[9]
        if "\\p1" not in text or "}" not in text:
            continue
        pos = None
        sx = sy = 1.0
        color = "#ffffff"  # ASS default primary is white
        for match in _TAG.finditer(text):
            if match.group(1):
                pos = (float(match.group(2)), float(match.group(3)))
            elif match.group(4) == "fscx":
                sx = float(match.group(5)) / 100.0
            elif match.group(4) == "fscy":
                sy = float(match.group(5)) / 100.0
            elif match.group(6):
                bgr = match.group(7)[-6:]  # ASS stores &H[AA]BBGGRR&
                color = f"#{bgr[4:6]}{bgr[2:4]}{bgr[0:2]}".lower()
        if pos is None:
            raise ValueError(f"drawing line without \\pos: {text[:60]}")
        x0, y0, x1, y1, is_ellipse = _drawing_bbox(text.rsplit("}", 1)[1])
        elements.append({
            "cx": pos[0], "cy": pos[1],
            "rx": (x1 - x0) / 2 * sx, "ry": (y1 - y0) / 2 * sy,
            "shape": "ellipse" if is_ellipse else "rect",
            "color": color,
        })
    if not elements:
        raise ValueError(f"no active \\p1 drawings found in {path}")
    return elements


def split_rows(elements: list[dict]) -> list[dict]:
    """Tag each shape 'top' or 'bottom' wall by cy. Splits at the cy
    MIDRANGE, not the median — decimation makes row counts unequal and a
    median then lands on one row, mis-tagging it. Existing tags are kept."""
    cys = [e["cy"] for e in elements]
    cy_mid = (min(cys) + max(cys)) / 2.0
    return [{**e, "row": e.get("row",
                              "top" if e["cy"] <= cy_mid else "bottom")}
            for e in elements]


def assign_modes(elements: list[dict], expand_row: str) -> list[dict]:
    """mode +1 = expanding row, -1 = contracting (used only when pulsing)."""
    return [{**e, "mode": 1 if (e["row"] == expand_row) else -1}
            for e in split_rows(elements)]


def prepare_rows(elements: list[dict], size_top: float = 1.0,
                 size_bottom: float = 1.0, nth_top: int = 1,
                 nth_bottom: int = 1) -> list[dict]:
    """Per-wall size and spacing, applied to ELLIPSES only (rectangles are
    backdrops — wall bands etc. — and are kept verbatim).

    nth keeps every Nth circle of a row (left to right). Resizing pins the
    rim-side edge (top row's top edge, bottom row's bottom edge) so circles
    extend toward the tank floor, preserving the squish aspect."""
    size = {"top": size_top, "bottom": size_bottom}
    nth = {"top": max(1, int(nth_top)), "bottom": max(1, int(nth_bottom))}
    keep, counts = [], {"top": 0, "bottom": 0}
    for el in sorted(split_rows(elements), key=lambda e: e["cx"]):
        if el["shape"] != "ellipse":
            keep.append(el)
            continue
        row = el["row"]
        if counts[row] % nth[row] == 0:
            factor = size[row]
            keep.append({**el,
                         "rx": el["rx"] * factor, "ry": el["ry"] * factor,
                         "cy": el["cy"] + (factor - 1.0) * el["ry"]
                         * (1.0 if row == "top" else -1.0)})
        counts[row] += 1
    return keep


def build_patches(elements: list[dict], rect: tuple[int, int, int, int]) -> list:
    """Per-shape dicts with a normalized-radius grid over the clipped bbox,
    x-major (surfarray convention), region-local coords."""
    left, top, right, bottom = rect
    rw, rh = right - left, bottom - top
    patches = []
    # z-order: rectangles are backdrops (wall bands) — stamp them first so
    # circles always render on top
    for el in sorted(elements, key=lambda e: e["shape"] == "ellipse"):
        cx, cy = el["cx"] - left, el["cy"] - top
        rx, ry = float(el["rx"]), float(el["ry"])
        x0 = max(0, int(np.floor(cx - rx)) - 2)
        x1 = min(rw, int(np.ceil(cx + rx)) + 3)
        y0 = max(0, int(np.floor(cy - ry)) - 2)
        y1 = min(rh, int(np.ceil(cy + ry)) + 3)
        if x1 <= x0 or y1 <= y0:
            continue  # entirely outside the stimulus region
        x = np.abs(np.arange(x0, x1, dtype=np.float64) - cx)[:, None] / rx
        y = np.abs(np.arange(y0, y1, dtype=np.float64) - cy)[None, :] / ry
        rho = np.sqrt(x * x + y * y) if el["shape"] == "ellipse" \
            else np.maximum(x, y)
        patches.append({"x0": x0, "y0": y0, "rho": rho, "rx": rx,
                        "shape": el["shape"],
                        "mode": int(el.get("mode", 1)),
                        "row": el.get("row", "top"),
                        "rgb": hex_to_rgb(el["color"])})
    return patches


def scales(t_s: float, cycle_s: float) -> tuple[float, float]:
    """(expanding, contracting) radius fraction at accumulated stimulus time."""
    phase = (t_s / cycle_s) % 1.0
    return phase, 1.0 - phase


def _alpha(patch: dict, s_expand: float, s_contract: float,
           soft_px: float) -> np.ndarray | None:
    # rectangles are backdrops: always full size, never animated
    s = 1.0 if patch["shape"] == "rect" \
        else (s_expand if patch["mode"] > 0 else s_contract)
    if s * patch["rx"] <= 0.5:  # sub-pixel: nothing to draw
        return None
    return np.clip((s - patch["rho"]) * (patch["rx"] / max(soft_px, 0.1)),
                   0.0, 1.0)[:, :, None]


def stamp_fill(frame: np.ndarray, patches: list, s_expand: float,
               s_contract: float, soft_px: float = 5.0,
               top_rgb: np.ndarray | None = None,
               bottom_rgb: np.ndarray | None = None) -> None:
    """Blend opaque shapes into an x-major (W,H,3) uint8 frame. Per-wall
    color overrides (top_rgb / bottom_rgb) recolor CIRCLES only — backdrop
    rectangles always keep their own .ass color."""
    for patch in patches:
        alpha = _alpha(patch, s_expand, s_contract, soft_px)
        if alpha is None:
            continue
        override = (top_rgb if patch["row"] == "top" else bottom_rgb) \
            if patch["shape"] == "ellipse" else None
        rgb = patch["rgb"] if override is None else override
        h, w = patch["rho"].shape
        sub = frame[patch["x0"]:patch["x0"] + h, patch["y0"]:patch["y0"] + w]
        sub[:] = np.rint(sub * (1.0 - alpha) + rgb * alpha).astype(np.uint8)


def stamp_window(out: np.ndarray, grating: np.ndarray, patches: list,
                 s_expand: float, s_contract: float,
                 soft_px: float = 5.0) -> None:
    """Reveal the grating frame through the shapes into `out` (both (W,H,3))."""
    for patch in patches:
        alpha = _alpha(patch, s_expand, s_contract, soft_px)
        if alpha is None:
            continue
        h, w = patch["rho"].shape
        window = (slice(patch["x0"], patch["x0"] + h),
                  slice(patch["y0"], patch["y0"] + w))
        out[window] = np.rint(out[window] * (1.0 - alpha)
                              + grating[window] * alpha).astype(np.uint8)
