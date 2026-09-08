from __future__ import annotations

import pytest

from rig_studio.safety.guarded import GuardViolation, GuardedRig
from tests.conftest import RecordingHandle, make_rig


def test_allowlist_violation_raises(sim_backend, rig_config, tmp_path):
    serial = rig_config.serials[0]
    with make_rig(sim_backend, [serial], tmp_path) as rig:
        with pytest.raises(GuardViolation, match="not allowlisted"):
            rig.set(serial, "AcquisitionStart", 1)


def test_readback_mismatch_raises(rig_config, tmp_path, recording_backend):
    serial = rig_config.serials[0]

    class LyingHandle(RecordingHandle):
        def node_read(self, name):
            if name == "Gain" and self.writes and self.writes[-1][0] == "Gain":
                return 0.0  # firmware silently ignored the write
            return super().node_read(name)

    recording_backend._handles.clear()

    def lying_open(s):
        handle = LyingHandle(str(s), recording_backend, (1024, 750, 0, 160))
        recording_backend._handles.append(handle)
        return handle

    recording_backend.open = lying_open  # type: ignore[method-assign]
    rig = make_rig(recording_backend, [serial], tmp_path)
    with rig:
        with pytest.raises(GuardViolation, match="Readback mismatch"):
            rig.set(serial, "Gain", 12.0)
        # leave the sim in a restorable state
        lying = rig.handle(serial)
        lying._nodes["Gain"]["value"] = rig.snapshot(serial)["Gain"]["value"]


def test_restore_on_exit(sim_backend, rig_config, tmp_path):
    serial = rig_config.serials[0]
    marker = tmp_path / "MARKER.json"
    rig = GuardedRig(sim_backend, [serial], frozenset({"ExposureTime", "Gain"}),
                     snapshot_dir=tmp_path / "snaps", marker_path=marker,
                     exposure_tolerance_us=25.0)
    with rig:
        assert marker.exists()
        rig.set(serial, "ExposureTime", 8000.0)
        rig.set(serial, "Gain", 20.0)
        handle = rig.handle(serial)
        assert handle.node_read("ExposureTime") == 8000.0
    # exit restored exactly-as-found and removed the marker
    assert handle.node_read("ExposureTime") == 5000.0
    assert handle.node_read("Gain") == 9.8
    assert not marker.exists()
    assert (tmp_path / "snaps" / f"{serial}.json").exists()


def test_restore_skips_snapshot_readonly_nodes(sim_backend, rig_config, tmp_path):
    """Gamma is RO while GammaEnabled=False (GS3): a node that was not writable
    at snapshot time must be skipped, not failed (the ACTIVE_22246599 bug)."""
    from rig_studio.safety.guarded import restore_handle

    handle = sim_backend.open(rig_config.serials[0])
    handle._nodes["Gamma"]["access"] = "3"  # numeric RO code, as real dumps record
    handle._nodes["Gain"]["value"] = 20.0   # drifted; must be restored
    snapshot = {
        "Gamma": {"access": "3", "type": "5", "value": 1.0605},
        "Gain": {"access": "4", "type": "5", "value": 9.8},
    }
    failures = restore_handle(handle, snapshot, frozenset({"Gamma", "Gain"}),
                              exposure_tolerance_us=25.0)
    assert failures == []
    assert handle.node_read("Gain") == 9.8
    sim_backend.close()


def test_restore_skips_nodes_inaccessible_at_restore_time(sim_backend, rig_config, tmp_path):
    """TriggerOverlap is NA while disarmed (and Gamma while gamma is off):
    inaccessible-now nodes are skipped, not failed (the six-marker lockout)."""
    from rig_studio.safety.guarded import restore_handle

    handle = sim_backend.open(rig_config.serials[0])
    handle._nodes["TriggerOverlap"]["access"] = "1"  # NA right now
    snapshot = {
        "TriggerOverlap": {"access": "4", "type": "9", "value": "ReadOut"},
        "Gain": {"access": "4", "type": "5", "value": 9.8},
    }
    handle._nodes["Gain"]["value"] = 15.0
    failures = restore_handle(handle, snapshot, frozenset({"TriggerOverlap", "Gain"}),
                              exposure_tolerance_us=25.0)
    assert failures == []
    assert handle.node_read("Gain") == 9.8
    sim_backend.close()


def test_restore_disarms_triggers_first_rearms_last(recording_backend, rig_config, tmp_path):
    """Armed triggers gate the frame-rate/exposure node group (cam3 marker bug):
    restore must disarm both selectors up front and re-arm as the LAST write."""
    from rig_studio.safety.guarded import restore_handle

    handle = recording_backend.open(rig_config.serials[0])
    handle._nodes["Gain"]["value"] = 20.0  # drifted
    snapshot = {
        "Gain": {"access": "4", "type": "5", "value": 9.8},
        "TriggerMode": {"access": "4", "type": "9", "value": "On"},
        "TriggerSource": {"access": "4", "type": "9", "value": "Line0"},
    }
    failures = restore_handle(handle, snapshot,
                              frozenset({"Gain", "TriggerMode", "TriggerSource"}),
                              exposure_tolerance_us=25.0)
    assert failures == []
    trigger_writes = [i for i, (n, v) in enumerate(handle.writes) if n == "TriggerMode"]
    gain_write = next(i for i, (n, v) in enumerate(handle.writes) if n == "Gain")
    assert handle.writes[trigger_writes[0]][1] == "Off"   # disarmed first
    assert handle.writes[trigger_writes[-1]][1] == "On"   # re-armed last
    assert trigger_writes[0] < gain_write < trigger_writes[-1]
    assert handle.node_read("Gain") == 9.8
    recording_backend.close()


def test_commit_no_restore_keeps_changes(sim_backend, rig_config, tmp_path):
    """Adopt mode: a clean close keeps session changes and clears the marker."""
    serial = rig_config.serials[0]
    marker = tmp_path / "MARKER.json"
    rig = GuardedRig(sim_backend, [serial], frozenset({"Gain"}),
                     snapshot_dir=tmp_path / "snaps", marker_path=marker,
                     exposure_tolerance_us=25.0)
    with rig:
        rig.set(serial, "Gain", 20.0)
        handle = rig.handle(serial)
        rig.commit_no_restore()
    assert handle.node_read("Gain") == 20.0  # NOT reverted
    assert not marker.exists()


def test_existing_marker_blocks_new_session(sim_backend, rig_config, tmp_path):
    serial = rig_config.serials[0]
    marker = tmp_path / "MARKER.json"
    marker.write_text("{}", encoding="utf-8")
    rig = GuardedRig(sim_backend, [serial], frozenset(), snapshot_dir=tmp_path,
                     marker_path=marker, exposure_tolerance_us=25.0)
    with pytest.raises(Exception, match="Crash marker"):
        rig.__enter__()
