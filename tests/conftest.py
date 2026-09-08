from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rig_studio.backend.sim_backend import SimBackend, SimHandle
from rig_studio.config import RigConfig, load_config
from rig_studio.safety.allowlist import MUTABLE_ALLOWLIST
from rig_studio.safety.guarded import GuardedRig

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "rig.yaml"


@pytest.fixture(scope="session")
def rig_config() -> RigConfig:
    return load_config(CONFIG_PATH)


class RecordingHandle(SimHandle):
    """SimHandle that records every node write in order."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.writes: list[tuple[str, Any]] = []

    def node_write(self, name: str, value: Any) -> None:
        super().node_write(name, value)
        self.writes.append((name, value))


class RecordingBackend(SimBackend):
    def open(self, serial: str):
        serial = str(serial)
        if serial not in self._serials:
            raise RuntimeError(f"Configured rig serial is missing: {serial}")
        handle = RecordingHandle(serial, self, self._rois.get(serial, (1024, 750, 0, 160)))
        self._handles.append(handle)
        return handle


@pytest.fixture
def sim_backend(rig_config: RigConfig) -> SimBackend:
    rois = {
        cam.serial: (cam.roi.width, cam.roi.height, cam.roi.offset_x, cam.roi.offset_y)
        for cam in rig_config.cameras
    }
    return SimBackend(rig_config.serials, rois, fps=500.0)  # fast fps keeps tests quick


@pytest.fixture
def recording_backend(rig_config: RigConfig) -> RecordingBackend:
    rois = {
        cam.serial: (cam.roi.width, cam.roi.height, cam.roi.offset_x, cam.roi.offset_y)
        for cam in rig_config.cameras
    }
    return RecordingBackend(rig_config.serials, rois, fps=500.0)


def make_rig(backend, serials, tmp_path: Path, allowlist=MUTABLE_ALLOWLIST) -> GuardedRig:
    return GuardedRig(
        backend,
        list(serials),
        allowlist,
        snapshot_dir=tmp_path / "snapshots",
        marker_path=tmp_path / "MARKER.json",
        exposure_tolerance_us=25.0,
    )
