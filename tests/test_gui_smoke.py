from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_roi_overlay_rect():
    from rig_studio.gui.panels.camera_settings import roi_overlay_rect

    # rect is FRACTIONS of the frozen reference frame (decimation-independent)
    ref = (160, 112, 704, 800)
    # same mode: plain offset difference
    assert roi_overlay_rect("Mode2", ref, "Mode2", 200, 112, 704, 800) == \
        (40 / 704, 0.0, 1.0, 1.0)
    # frozen in Mode2 (vertical bin 2), editing in native Mode0: the SAME
    # physical sensor area (double the rows in Mode0) covers the whole reference
    assert roi_overlay_rect("Mode2", ref, "Mode0", 160, 224, 704, 1600) == \
        (0.0, 0.0, 1.0, 1.0)


def test_drag_rect():
    from rig_studio.gui.panels.camera_grid import drag_rect

    assert drag_rect("move", (0.1, 0.1, 0.5, 0.5), 0.2, -0.05) == \
        (0.30000000000000004, 0.05, 0.5, 0.5)
    assert drag_rect("r", (0.1, 0.1, 0.5, 0.5), 0.2, 0.0) == (0.1, 0.1, 0.7, 0.5)
    x, y, w, h = drag_rect("lt", (0.1, 0.1, 0.5, 0.5), 0.1, 0.1)
    assert (round(x, 6), round(y, 6), round(w, 6), round(h, 6)) == \
        (0.2, 0.2, 0.4, 0.4)
    # collapsing past the far edge clamps at the minimum size
    assert drag_rect("l", (0.1, 0.1, 0.5, 0.5), 9.0, 0.0)[2] == pytest.approx(0.02)


def test_preview_rotation_and_display_levels():
    import numpy as np
    from PySide6.QtWidgets import QApplication

    from rig_studio.gui.panels.camera_grid import CamTile, rotate_rect

    QApplication.instance() or QApplication([])
    source = np.array([[0, 10, 20], [30, 40, 255]], dtype=np.uint8)
    tile = CamTile(4, "serial", "side", preview_rotation=180)
    assert np.array_equal(tile.display_image(source), np.rot90(source, 2))
    rect = (0.1, 0.2, 0.3, 0.4)
    assert rotate_rect(rotate_rect(rect, 180), 180) == pytest.approx(rect)
    tile.set_display_options(True, 1.5)
    assert tile.display_image(source).shape == source.shape


def test_apply_overlay_drag(rig_config):
    from PySide6.QtWidgets import QApplication

    from rig_studio.gui.panels.camera_settings import CameraSettingsPanel

    QApplication.instance() or QApplication([])
    sent = []
    panel = CameraSettingsPanel(rig_config, lambda pos, msg: sent.append((pos, msg)))
    panel.position = 0
    panel.video_mode.setCurrentText("Mode2")
    for spin, value in ((panel.offset_x, 160), (panel.offset_y, 112),
                        (panel.width, 704), (panel.height, 800)):
        spin.setValue(value)
    panel._freeze_basis = ("Mode2", (160, 112, 704, 800))
    panel.apply_overlay_drag(0, (0.25, 0.0, 0.5, 1.0))
    assert panel.offset_x.value() == 336 and panel.width.value() == 352
    assert panel.offset_y.value() == 112 and panel.height.value() == 800
    writes = [(msg["name"], msg["value"]) for _, msg in sent
              if msg["cmd"] == "node_write"]
    # shrinking on x: Width written before OffsetX
    assert writes.index(("Width", 352)) < writes.index(("OffsetX", 336))
    # a drag on a non-selected tile is ignored
    before = len(sent)
    panel.apply_overlay_drag(3, (0.0, 0.0, 1.0, 1.0))
    assert len(sent) == before


def test_trigger_retune_live(rig_config):
    from PySide6.QtWidgets import QApplication

    from rig_studio.gui.panels.trigger_panel import TriggerPanel
    from rig_studio.trigger.digilent import plan_clock

    QApplication.instance() or QApplication([])
    panel = TriggerPanel(rig_config, main_window=None)

    class FakeClock:
        owned = True
        running = True
        calls: list = []

        def configure(self, fps, duty):
            self.calls.append(("configure", fps, duty))
            return plan_clock(100e6, fps, duty, 2**31)

        def start(self):
            self.calls.append(("start",))

    panel.clock = FakeClock()
    panel.fps.setValue(20.0)
    panel._retune_running()  # what the debounce timer fires
    assert ("configure", 20.0, 0.5) in panel.clock.calls
    assert ("start",) in panel.clock.calls
    assert "20.000 Hz (retuned live)" in panel.status.text()
    # stopped clock: hands off
    panel.clock.calls.clear()
    panel.clock.running = False
    panel._retune_running()
    assert panel.clock.calls == []
    # external ownership: hands off even if a clock object lingers
    panel.clock.running = True
    panel.ownership.setCurrentText("external")
    panel._retune_running()
    assert panel.clock.calls == []


def test_main_window_builds(rig_config, tmp_path):
    from PySide6.QtWidgets import QApplication

    from rig_studio.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(rig_config, tmp_path)
    window.grid.tiles[0].clicked.emit(0)  # select a tile -> settings panel target
    assert window.settings_panel.position == 0
    window.grid.tiles[0].update_frame({"image": __import__("numpy").zeros((20, 30),
                                                                       dtype="uint8")})
    window.settings_panel.freeze_reference.setChecked(True)
    assert window.grid.tiles[0].reference_frozen
    assert window.grid.tiles[0].crop_overlay is not None  # rect drawn immediately
    window.settings_panel.offset_x.setValue(window.settings_panel.offset_x.value() + 8)
    window.settings_panel._push_overlay()
    assert window.grid.tiles[0].crop_overlay[0] != 0.0
    window.settings_panel.freeze_reference.setChecked(False)
    assert not window.grid.tiles[0].reference_frozen
    assert window.grid.tiles[0].crop_overlay is None
    # freezing with no frame received refuses and unchecks itself
    window.grid.tiles[1].clicked.emit(1)
    window.settings_panel.freeze_reference.setChecked(True)
    assert not window.settings_panel.freeze_reference.isChecked()
    assert not window.grid.tiles[1].reference_frozen
    window.grid.tiles[0].clicked.emit(0)
    window.open_full_camera_view(0)
    assert 0 in window.single_cam_windows
    window.single_cam_windows[0].close()
    assert len(window.grid.tiles) == 6
    assert window.grid.tiles[4].preview_rotation == 0
    assert window.selection_panel.use_positions() == set(range(6))
    assert window.selection_panel.view_positions() == set(range(6))
    assert window.selection_panel.record_positions() == set(range(6))
    window.selection_panel.view_boxes[1].setChecked(False)
    window.selection_panel.view_boxes[3].setChecked(False)
    assert window.grid.visible_positions == {0, 2, 4, 5}
    assert window.grid._layout.itemAtPosition(0, 1).widget().position == 2
    window.selection_panel.use_boxes[5].setChecked(False)
    assert 5 not in window.selection_panel.view_positions()
    assert 5 not in window.selection_panel.record_positions()
    window.selection_panel.use_boxes[5].setChecked(True)
    window.selection_panel.view_all.click()
    window.selection_panel.record_all.click()
    # grating cfg from the panel reflects the MATLAB defaults
    grating = window.stimulus_panel.grating_cfg()
    assert grating.period_px == 157.5
    assert grating.speed_px_s == 78.6  # 1.00 cm/s on tank
    assert grating.direction == 1
    # strip view: top cams only, side by side in readable order
    window.strip_action.setChecked(True)
    top_left = window.grid._layout.itemAtPosition(0, 0).widget()
    assert top_left.position == rig_config.gui.strip_top_order[0]
    assert window.grid._layout.itemAtPosition(0, 3) is not None
    for position in (4, 5):
        assert window.grid.tiles[position].isHidden()  # side cams not shown
    window.strip_action.setChecked(False)
    assert window.grid._layout.itemAtPosition(2, 0) is not None  # 3x2 again
    assert not window.grid.tiles[4].isHidden()
    window.selection_panel.set_connected(True)
    assert not window.selection_panel.use_boxes[0].isEnabled()
    assert window.selection_panel.record_boxes[0].isEnabled()
    window.selection_panel.set_running(True)
    assert not window.selection_panel.record_boxes[0].isEnabled()
    window.selection_panel.set_running(False)
    window.selection_panel.set_connected(False)
    # pop-out camera window and re-dock
    window.popout_action.setChecked(True)
    assert window.cam_window is not None
    assert window.grid.window() is window.cam_window
    window.popout_action.setChecked(False)
    assert window.cam_window is None
    assert window.centralWidget() is window.grid
    assert rig_config.apply_profile_on_connect is False  # adopt SpinView state
    window.trigger_panel.fps.setValue(120.0)
    assert window.desired_fps() == 120.0
    assert "120" in window.clock_mode.itemText(0)
    window.timer.stop()
    window.close()
    app.processEvents()
