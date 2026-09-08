from __future__ import annotations

import numpy as np

from rig_studio.stimulus.grating import (
    corrected_dt,
    frame_u8,
    profile,
    region_rect,
    row_u8,
    smoothstep,
    step_phase,
)

PERIOD = 157.5


def test_smoothstep_cubic():
    # t = (0.1 + 0.2)/0.4 = 0.75 -> 3t^2 - 2t^3 = 0.84375
    assert np.isclose(smoothstep(-0.2, 0.2, np.asarray(0.1)), 0.84375)
    assert smoothstep(-0.2, 0.2, np.asarray(-0.5)) == 0.0
    assert smoothstep(-0.2, 0.2, np.asarray(0.5)) == 1.0


def test_profile_hand_computed_points():
    # sin = 0 at x=0, phase 0 -> smoothstep = 0.5 -> sv = 0 -> luminance 0
    assert np.isclose(profile(np.asarray([0.0]), PERIOD, 0.0)[0], 0.0)
    # plateau white at quarter period (sin = 1)
    assert np.isclose(profile(np.asarray([PERIOD / 4]), PERIOD, 0.0)[0], 1.0)
    # plateau black at three-quarter period (sin = -1, clipped)
    assert np.isclose(profile(np.asarray([3 * PERIOD / 4]), PERIOD, 0.0)[0], 0.0)
    # sin = 0.1 -> sv = 2*0.84375 - 1 = 0.6875
    x = PERIOD * np.arcsin(0.1) / (2 * np.pi)
    assert np.isclose(profile(np.asarray([x]), PERIOD, 0.0)[0], 0.6875)


def test_soft_edge_width():
    x = np.linspace(-15, 15, 30001)
    # pre-clip smoothstep transition occupies |sin| < 0.2 => ~10.1 px at period 157.5
    sv = smoothstep(-0.2, 0.2, np.sin(2 * np.pi * x / PERIOD)) * 2.0 - 1.0
    full = x[(sv > -0.999) & (sv < 0.999)]
    assert 9.0 < full.max() - full.min() < 11.0
    # rendered luminance clips sv<0 to black (as the GL framebuffer does),
    # so the visible black->white ramp is the positive half: ~5 px
    values = profile(x, PERIOD, 0.0)
    ramp = x[(values > 0.001) & (values < 0.999)]
    assert 4.0 < ramp.max() - ramp.min() < 6.0


def test_period_wrap_and_phase_shift():
    x = np.linspace(0, 500, 977)
    assert np.allclose(profile(x, PERIOD, 0.0), profile(x + PERIOD, PERIOD, 0.0))
    assert np.allclose(profile(x, PERIOD, 0.0), profile(x, PERIOD, PERIOD))
    # phase in pixels translates the pattern: f(x, phase=p) == f(x+p, 0)
    assert np.allclose(profile(x, PERIOD, 33.25), profile(x + 33.25, PERIOD, 0.0))


def test_contrast_scaling():
    x = np.asarray([PERIOD / 4])
    assert np.isclose(profile(x, PERIOD, 0.0, contrast=0.5)[0], 0.5)


def test_frame_matches_row_at_zero_rotation():
    frame = frame_u8(64, 8, 0.0, PERIOD, 12.5)
    row = row_u8(64, PERIOD, 12.5)
    assert np.array_equal(frame, np.tile(row, (8, 1)))


def test_rotated_frame_modulates_along_rotated_axis():
    frame = frame_u8(32, 32, 90.0, PERIOD, 0.0)
    # at 90 deg the bars are horizontal: rows constant along x
    assert np.array_equal(frame, np.tile(frame[:, :1], (1, 32)))


def test_step_phase_accumulation():
    # PTB: yoffset = mod(yoffset + dir*speed*dt, period)
    phase = 0.0
    for _ in range(100):
        phase = step_phase(phase, 1, 120.0, 1 / 240, PERIOD)
    assert np.isclose(phase, (100 * 120.0 / 240) % PERIOD)
    assert np.isclose(step_phase(1.0, -1, 120.0, 0.1, PERIOD), (1.0 - 12.0) % PERIOD)


def test_corrected_dt_replicates_ptb_rejection():
    ifi = 1 / 240
    assert corrected_dt(0.004, ifi) == 0.004
    assert corrected_dt(-0.001, ifi) == ifi
    assert corrected_dt(0.0, ifi) == ifi
    assert corrected_dt(0.06, ifi) == ifi  # > 0.05 rejected
    assert corrected_dt(float("nan"), ifi) == ifi


def test_region_rects():
    assert region_rect("bottomhalf", 1920, 1080, None) == (0, 540, 1920, 1080)
    assert region_rect("tophalf", 1920, 1080, None) == (0, 0, 1920, 540)
    assert region_rect("full", 1920, 1080, None) == (0, 0, 1920, 1080)
    assert region_rect("custom", 1920, 1080, (100, 200, 300, 400)) == (100, 200, 300, 400)
    # clamped to the window
    assert region_rect("custom", 1920, 1080, (-5, 0, 5000, 5000)) == (0, 0, 1920, 1080)
