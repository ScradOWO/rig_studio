"""End-to-end sim recording: real worker processes, real HDF5 files, real gate."""
from __future__ import annotations

import time
from pathlib import Path

import h5py
import numpy as np
import pytest
import yaml

from rig_studio.config import load_config
from rig_studio.recorder.supervisor import RecorderSupervisor
from tests.conftest import CONFIG_PATH


@pytest.fixture
def sim_config(tmp_path: Path):
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    data["rig"]["backend"] = "sim"
    data["recording"]["pool_seconds"] = 0.5
    data["recording"]["gate_delay_s"] = 0.3
    data["recording"]["preroll_s"] = 0.1
    for row in data["cameras"]["positions"]:
        row["output_root"] = str(tmp_path / f"root{row['position']}")
    path = tmp_path / "rig.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_config(path)


def test_two_camera_gated_recording(sim_config, tmp_path):
    supervisor = RecorderSupervisor(sim_config, state_dir=tmp_path / "state")
    try:
        supervisor.start(positions=[0, 1])
        supervisor.configure_all()
        paths = supervisor.arm_all("e2e_test", duration_s=0.8, wait=False)
        supervisor.set_sim_trigger(True)  # the simulated Digilent clock
        supervisor.wait_armed()
        gate_ns = supervisor.open_gate()
        time.sleep((gate_ns - time.time_ns()) / 1e9 + 0.8)
        reports = supervisor.stop_recording_all()
        supervisor.set_sim_trigger(False)
    finally:
        supervisor.shutdown()

    assert set(reports) == {0, 1}
    for position, report in reports.items():
        assert report["writer_error"] is None
        assert report["dropped"] == 0
        assert report["frames_written"] > 0.5 * 178 * 0.8  # roughly duration * fps
        cam = sim_config.by_position(position)
        with h5py.File(paths[position], "r") as f:
            assert f.attrs["camera_index"] == position
            assert f.attrs["image_width"] == cam.roi.width
            assert f.attrs["image_height"] == cam.roi.height
            ts = f["timestamps"][:]
            n = report["frames_written"]
            valid = ts[:n]
            assert np.array_equal(valid[:, 1], np.arange(1, n + 1))  # 1-based seq
            assert not ts[n:].any()  # zeros after the valid prefix
            # first frame is at/after the gate, within one trigger period
            assert 0.0 <= valid[0, 0] < 2.5 / 178
            assert np.all(np.diff(valid[:, 0]) > 0)  # t_host monotonic
            assert np.all(np.diff(valid[:, 2]) >= 1)  # hw frame ids increase
            image = f["images"][0].reshape(cam.roi.height, cam.roi.width)
            assert image[:24].max() > 0  # sim identity band landed in the file

    # both cameras started on (nearly) the same trigger edge
    starts = []
    for position in (0, 1):
        with h5py.File(paths[position], "r") as f:
            starts.append(f["timestamps"][0, 0])
    assert abs(starts[0] - starts[1]) < 2.0 / 178


def test_recording_subset_creates_only_selected_file(sim_config, tmp_path):
    supervisor = RecorderSupervisor(sim_config, state_dir=tmp_path / "state_subset")
    try:
        supervisor.start(positions=[0, 1])
        supervisor.configure_all()
        paths = supervisor.arm_all(
            "subset_test", duration_s=0.35, wait=False, positions={1}
        )
        supervisor.set_sim_trigger(True)
        supervisor.wait_armed(positions={1})
        gate_ns = supervisor.open_gate(positions={1})
        time.sleep(max(0.0, (gate_ns - time.time_ns()) / 1e9) + 0.35)
        reports = supervisor.stop_recording_all(positions={1})
        supervisor.set_sim_trigger(False)
    finally:
        supervisor.shutdown()

    assert set(paths) == {1}
    assert set(reports) == {1}
    assert reports[1]["frames_written"] > 0
    assert Path(paths[1]).is_file()
    unselected = Path(sim_config.by_position(0).output_root) / "subset_test_cam0.h5"
    assert not unselected.exists()


def test_frames_only_writes_pngs_without_h5(sim_config, tmp_path):
    supervisor = RecorderSupervisor(sim_config, state_dir=tmp_path / "state_frames")
    try:
        supervisor.start(positions=[0])
        supervisor.configure_all()
        paths = supervisor.arm_all(
            "frames_test", duration_s=1.2, wait=False, positions={0},
            fps_nominal=178.0, frames_only_count=10,
        )
        # Deliberately deliver far below nominal. Frame selection must use gate
        # time, not nominal frame indices, and still produce the requested count.
        supervisor.set_sim_trigger(True, 20.0)
        supervisor.wait_armed(positions={0})
        gate_ns = supervisor.open_gate(positions={0})
        time.sleep(max(0.0, (gate_ns - time.time_ns()) / 1e9) + 1.35)
        reports = supervisor.stop_recording_all(positions={0})
        supervisor.set_sim_trigger(False)
    finally:
        supervisor.shutdown()

    output = Path(paths[0])
    assert output.is_dir()
    assert len(list(output.glob("*.png"))) == 10
    assert reports[0]["output_kind"] == "png_frames"
    assert reports[0]["frames_written"] == 10
    assert reports[0]["writer_error"] is None
    assert not list(Path(sim_config.by_position(0).output_root).glob("*.h5"))


def test_simultaneous_rate_measurement(sim_config, tmp_path):
    supervisor = RecorderSupervisor(sim_config, state_dir=tmp_path / "state_rate")
    try:
        supervisor.start(positions=[0, 1])
        supervisor.configure_all()
        supervisor.set_sim_trigger(True, 120.0)
        supervisor.start_preview()
        supervisor.wait_for("acquisition", {0, 1}, 5.0)
        rates = supervisor.measure_rates(0.3, {0, 1})
    finally:
        supervisor.shutdown()

    assert set(rates) == {0, 1}
    assert all(105.0 <= rate <= 135.0 for rate in rates.values())
