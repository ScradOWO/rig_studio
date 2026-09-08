from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rig_studio.config import load_config
from rig_studio.recording.session import max_frames, output_path, session_tag
from tests.conftest import CONFIG_PATH


def test_fixed_hardware_facts(rig_config):
    assert [c.serial for c in sorted(rig_config.cameras, key=lambda c: c.position)] == [
        "22246592", "22246599", "22065066", "22246605", "22246594", "22246585"
    ]
    rois = {c.position: (c.roi.width, c.roi.height, c.roi.offset_x, c.roi.offset_y)
            for c in rig_config.cameras}
    # user's roiCrops table (2026-08-18), Mode2 coordinate space
    assert rois == {
        0: (704, 800, 160, 112), 1: (704, 800, 160, 112), 2: (704, 800, 160, 112),
        3: (704, 800, 160, 112), 4: (1024, 704, 0, 160), 5: (1024, 704, 0, 160),
    }
    assert rig_config.recording.fps_nominal == 178.0
    assert rig_config.recording.chunk_frames == 32
    assert rig_config.trigger.channels_by_position == (0, 3, 6, 4, 7, 2)
    assert rig_config.trigger.duty == 0.5
    assert rig_config.stimulus.grating.period_px == 157.5
    assert rig_config.stimulus.grating.speed_px_s == 78.6  # 1.00 cm/s on tank
    assert rig_config.stimulus.grating.smoothstep_edges == (-0.2, 0.2)
    assert rig_config.stimulus.projector.refresh_hz == 240
    assert rig_config.defaults.exposure_time_us == 5000.0
    assert rig_config.defaults.gain_db == 9.8
    assert rig_config.defaults.device_link_throughput_limit == 400 * 1024 * 1024
    assert rig_config.quirks.trigger_selectors_both == ("FrameStart", "ExposureActive")
    assert rig_config.gui.default_use_positions == (0, 1, 2, 3, 4, 5)
    assert rig_config.gui.default_view_positions == (0, 1, 2, 3, 4, 5)
    assert rig_config.recording.default_positions == (0, 1, 2, 3, 4, 5)
    assert rig_config.by_position(0).output_root == "D:/h5_rec_cam0"
    assert rig_config.by_serial("22246599").label == "top1"
    # all cameras run in Mode2 (vertical binning 2 — the rig's non-square-pixel convention)
    assert all(c.video_mode == "Mode2" for c in rig_config.cameras)
    assert all(cam.preview_rotation == 0 for cam in rig_config.cameras)


def _mutated(tmp_path: Path, mutate) -> Path:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "rig.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_duplicate_serial_rejected(tmp_path):
    def mutate(data):
        data["cameras"]["positions"][1]["serial"] = "22246592"

    with pytest.raises(ValueError, match="duplicate"):
        load_config(_mutated(tmp_path, mutate))


def test_bad_preview_rotation_rejected(tmp_path):
    def mutate(data):
        data["cameras"]["positions"][4]["preview_rotation"] = 45

    with pytest.raises(ValueError, match="preview_rotation"):
        load_config(_mutated(tmp_path, mutate))


def test_bad_direction_rejected(tmp_path):
    def mutate(data):
        data["stimulus"]["grating"]["direction"] = 2

    with pytest.raises(ValueError, match="direction"):
        load_config(_mutated(tmp_path, mutate))


def test_bad_region_rejected(tmp_path):
    def mutate(data):
        data["stimulus"]["grating"]["region"]["preset"] = "lefthalf"

    with pytest.raises(ValueError, match="preset"):
        load_config(_mutated(tmp_path, mutate))


def test_camera_selection_defaults_and_subsets(tmp_path):
    def mutate(data):
        data["gui"]["default_use_positions"] = [0, 2, 4]
        data["gui"]["default_view_positions"] = [0, 2]
        data["recording"]["default_positions"] = [2, 4]

    config = load_config(_mutated(tmp_path, mutate))
    assert config.gui.default_use_positions == (0, 2, 4)
    assert config.gui.default_view_positions == (0, 2)
    assert config.recording.default_positions == (2, 4)


def test_record_selection_must_be_enabled_for_use(tmp_path):
    def mutate(data):
        data["gui"]["default_use_positions"] = [0, 1]
        data["recording"]["default_positions"] = [0, 2]

    with pytest.raises(ValueError, match="subset"):
        load_config(_mutated(tmp_path, mutate))


def test_unknown_default_camera_rejected(tmp_path):
    def mutate(data):
        data["gui"]["default_view_positions"] = [99]

    with pytest.raises(ValueError, match="unknown"):
        load_config(_mutated(tmp_path, mutate))


def test_session_naming(rig_config):
    assert max_frames(25.0, 178.0, 1000) == 5450
    path = output_path("D:/h5_rec_cam0", rig_config.recording.filename_template,
                       "camonly_20260817_120000", 0)
    assert path.as_posix() == "D:/h5_rec_cam0/camonly_20260817_120000_cam0.h5"
    assert len(session_tag()) == 15
