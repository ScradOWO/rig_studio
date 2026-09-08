"""Named stimulus profiles: YAML round-trip + panel save/load."""
from __future__ import annotations

import dataclasses
import os

import pytest

from rig_studio.stimulus import profiles

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_roundtrip(tmp_path):
    path = tmp_path / "stimulus_profiles.yaml"
    assert profiles.load_all(path) == {}
    profiles.save(path, "omr slow", {"speed_px_s": 39.3})
    profiles.save(path, "looming", {"speed_px_s": 0.0, "circles": {"enabled": True}})
    assert set(profiles.load_all(path)) == {"omr slow", "looming"}
    profiles.save(path, "omr slow", {"speed_px_s": 40.0})  # overwrite
    assert profiles.load_all(path)["omr slow"]["speed_px_s"] == 40.0
    profiles.delete(path, "looming")
    assert set(profiles.load_all(path)) == {"omr slow"}
    profiles.delete(path, "missing")  # no-op


def test_panel_settings_roundtrip(rig_config, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from rig_studio.gui.panels.stimulus_panel import StimulusPanel

    QApplication.instance() or QApplication([])
    config = dataclasses.replace(
        rig_config, stimulus=dataclasses.replace(
            rig_config.stimulus,
            profiles_path=str(tmp_path / "profiles.yaml")))
    panel = StimulusPanel(config, tmp_path / "status.json")
    panel.speed.setValue(39.3)
    panel.circles_on.setChecked(True)
    panel.size_top.setValue(1.5)
    panel.nth_top.setValue(2)
    saved = panel._settings()
    profiles.save(config.stimulus.profiles_path, "test", saved)
    panel._reload_profiles(select="test")
    panel.speed.setValue(999.0)
    panel.circles_on.setChecked(False)
    panel._profile_load()
    assert panel.speed.value() == 39.3
    assert panel.circles_on.isChecked()
    assert (panel.size_top.value(), panel.nth_top.value()) == (1.5, 2)
    assert panel._settings() == saved
    # the loaded state is exactly what arm/preview would use
    assert panel.grating_cfg().speed_px_s == 39.3
    circles = panel.circles_cfg()
    assert circles.enabled and (circles.size_top, circles.nth_top) == (1.5, 2)
    # swap exchanges the per-wall column
    panel._swap_walls()
    assert (panel.size_top.value(), panel.size_bottom.value()) == (1.0, 1.5)
    assert (panel.nth_top.value(), panel.nth_bottom.value()) == (1, 2)
