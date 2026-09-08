"""Stimulus host round-trip on SDL's dummy video driver (no display needed)."""
from __future__ import annotations

import time

import numpy as np
import pytest
from scipy.io import loadmat

from rig_studio.stimulus.client import StimulusClient

pytest.importorskip("pygame")


def test_armed_run_writes_matlab_log(rig_config, tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    client = StimulusClient(rig_config.stimulus.projector, tmp_path / "status.json")
    try:
        state = client.start(windowed=True, timeout_s=30.0)
        assert state["state"] == "ready"
        log_path = tmp_path / "stim_log_test.mat"
        start = time.time() + 0.5
        client.arm(rig_config.stimulus.grating, start_posix=start, grating_s=0.4,
                   log_path=log_path, base="test")
        done = client.wait_state({"done", "error"}, 30.0)
        assert done["state"] == "done", done
        assert done["frames"] > 0
        data = loadmat(log_path, squeeze_me=True)
        stim_log = data["stimLog"]
        fields = set(stim_log.dtype.names)
        assert {"vbl", "tStim", "dt", "missed", "yoffset", "phaseDeg",
                "spatial_period", "freqCpp", "stimRect", "speedPxPerSec",
                "stimProjectRegion"} <= fields
        assert float(stim_log["spatial_period"]) == 157.5
        assert np.isclose(float(stim_log["freqCpp"]), 1 / 157.5)
        yoffset = np.atleast_1d(stim_log["yoffset"].item()
                                if stim_log["yoffset"].shape == () else stim_log["yoffset"])
        assert np.all(yoffset >= 0) and np.all(yoffset < 157.5)  # mod period
        # the grating must actually DRIFT at speed_px_s in real time
        t_stim = np.atleast_1d(stim_log["tStim"].item()
                               if stim_log["tStim"].shape == () else stim_log["tStim"])
        steps = np.diff(yoffset)
        steps[steps < -157.5 / 2] += 157.5  # unwrap the modulo
        speed = steps.sum() / (t_stim[-1] - t_stim[0])
        expected = rig_config.stimulus.grating.speed_px_s
        assert 0.85 * expected < speed < 1.15 * expected, \
            f"drift speed {speed:.2f} px/s (expected {expected})"
    finally:
        client.quit()


def test_armed_run_with_circles_logs_scale(rig_config, tmp_path, monkeypatch):
    import dataclasses

    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    circles = dataclasses.replace(rig_config.stimulus.circles, enabled=True)
    client = StimulusClient(rig_config.stimulus.projector, tmp_path / "status.json")
    try:
        client.start(windowed=True, timeout_s=30.0)
        log_path = tmp_path / "stim_log_circ.mat"
        client.arm(rig_config.stimulus.grating, circles=circles,
                   start_posix=time.time() + 0.5, grating_s=0.4,
                   log_path=log_path, base="circ")
        done = client.wait_state({"done", "error"}, 30.0)
        assert done["state"] == "done", done
        stim_log = loadmat(log_path, squeeze_me=True)["stimLog"]
        fields = set(stim_log.dtype.names)
        assert {"circleScale", "circlesCycleS", "circlesElements"} <= fields
        elements = np.atleast_2d(stim_log["circlesElements"].item())
        assert elements.shape == (24, 5)  # cx cy rx ry mode
        assert set(elements[:, 4]) == {1.0, -1.0}
        scale = np.atleast_1d(stim_log["circleScale"].item()
                              if stim_log["circleScale"].shape == ()
                              else stim_log["circleScale"])
        assert np.all((scale >= 0) & (scale < 1))
        assert scale[-1] > scale[1]  # sawtooth grows within one 2 s cycle
    finally:
        client.quit()
