"""Exact port of the PTB SquareWaveShader grating (stimulus_only.m).

luminance(u) = clip(contrast * (smoothstep(e0, e1, sin(2*pi*u/period + phase)) * 2 - 1), 0, 1)

The "square wave" is a smoothstep-hardened sine (~10 px soft edge at period
157.5), NOT an ideal square; the PTB `duty` parameter is a shader no-op and is
deliberately not implemented. Modulation axis u is texture-x rotated by
rotation_deg; at 0 deg the bars are vertical and drift horizontally.
"""
from __future__ import annotations

import numpy as np

DEFAULT_EDGES = (-0.2, 0.2)


def smoothstep(e0: float, e1: float, v: np.ndarray) -> np.ndarray:
    t = np.clip((v - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def profile(u_px: np.ndarray, period_px: float, phase_px: float,
            edges: tuple[float, float] = DEFAULT_EDGES,
            contrast: float = 1.0) -> np.ndarray:
    """Luminance in [0,1] along the modulation axis; phase in pixels (as PTB yoffset)."""
    sv = np.sin(2.0 * np.pi * (np.asarray(u_px, np.float64) + phase_px) / period_px)
    sv = smoothstep(edges[0], edges[1], sv) * 2.0 - 1.0
    return np.clip(contrast * sv, 0.0, 1.0)


def row_u8(width: int, period_px: float, phase_px: float,
           edges: tuple[float, float] = DEFAULT_EDGES,
           contrast: float = 1.0) -> np.ndarray:
    """One uint8 row for the rotation=0 fast path (exact subpixel phase)."""
    values = profile(np.arange(width, dtype=np.float64), period_px, phase_px,
                     edges, contrast)
    return np.rint(values * 255.0).astype(np.uint8)


def frame_u8(width: int, height: int, rotation_deg: float, period_px: float,
             phase_px: float, edges: tuple[float, float] = DEFAULT_EDGES,
             contrast: float = 1.0) -> np.ndarray:
    """Full uint8 frame at arbitrary rotation (reference/test path; slow)."""
    theta = np.deg2rad(rotation_deg)
    x = np.arange(width, dtype=np.float64)[None, :]
    y = np.arange(height, dtype=np.float64)[:, None]
    u = x * np.cos(theta) + y * np.sin(theta)
    values = profile(u, period_px, phase_px, edges, contrast)
    return np.rint(values * 255.0).astype(np.uint8)


def step_phase(phase_px: float, direction: int, speed_px_s: float, dt: float,
               period_px: float) -> float:
    """PTB per-flip update: yoffset = mod(yoffset + dir*speed*dt, period)."""
    return float(np.mod(phase_px + direction * speed_px_s * dt, period_px))


def corrected_dt(measured_dt: float, ifi_s: float, reject_above_s: float = 0.05) -> float:
    """PTB dt correction: fall back to the flip interval on bad measurements."""
    if not np.isfinite(measured_dt) or measured_dt <= 0.0 or measured_dt > reject_above_s:
        return ifi_s
    return measured_dt


def region_rect(preset: str, width: int, height: int,
                custom_rect: tuple[int, int, int, int] | None) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the active region, clamped to the window."""
    if preset == "bottomhalf":
        rect = (0, round(height / 2), width, height)
    elif preset == "tophalf":
        rect = (0, 0, width, round(height / 2))
    elif preset == "full":
        rect = (0, 0, width, height)
    elif preset == "custom":
        if custom_rect is None:
            raise ValueError("custom region requires custom_rect")
        rect = tuple(int(v) for v in custom_rect)
    else:
        raise ValueError(f"unknown region preset: {preset}")
    left = max(0, min(rect[0], width))
    top = max(0, min(rect[1], height))
    right = max(left, min(rect[2], width))
    bottom = max(top, min(rect[3], height))
    if right - left == 0 or bottom - top == 0:
        raise ValueError(f"empty stimulus region: {rect}")
    return (left, top, right, bottom)
