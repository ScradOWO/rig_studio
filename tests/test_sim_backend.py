from __future__ import annotations

import pytest


def test_sim_frames_honor_roi_and_contiguity(sim_backend, rig_config):
    cam = rig_config.by_position(0)
    handle = sim_backend.open(cam.serial)
    handle.node_write("TriggerSelector", "FrameStart")
    handle.node_write("TriggerMode", "Off")  # free-run
    handle.begin_acquisition(16)
    frames = handle.grab(5, timeout_s=1.0)
    ids = [f.frame_id for f in frames]
    assert ids == list(range(ids[0], ids[0] + 5))
    assert frames[0].image.shape == (cam.roi.height, cam.roi.width)
    handle.end_acquisition()
    sim_backend.close()


def test_sim_triggered_camera_is_dark_without_clock(sim_backend, rig_config):
    handle = sim_backend.open(rig_config.serials[0])
    handle.begin_acquisition(16)  # TriggerMode defaults to On (armed)
    with pytest.raises(TimeoutError):
        handle.grab(1, timeout_s=0.05)
    sim_backend.trigger_running = True  # start the simulated Digilent clock
    assert handle.grab(1, timeout_s=1.0)[0].frame_id >= 1
    sim_backend.trigger_running = False
    sim_backend.close()


def test_sim_roi_write_validation(sim_backend, rig_config):
    handle = sim_backend.open(rig_config.serials[0])
    with pytest.raises(RuntimeError, match="increment"):
        handle.node_write("Width", 771)  # inc=8
    with pytest.raises(RuntimeError, match="outside"):
        handle.node_write("Width", 4096)
    handle.node_write("Width", 800)
    assert handle.node_read("Width") == 800
    sim_backend.close()
