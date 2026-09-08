from __future__ import annotations

from rig_studio.camera.profiles import (
    apply_recording_profile,
    configure_exposure_gain,
    set_free_run,
    set_hw_trigger,
)
from tests.conftest import make_rig


def _apply(recording_backend, rig_config, tmp_path):
    cam = rig_config.by_position(0)
    with make_rig(recording_backend, [cam.serial], tmp_path) as rig:
        apply_recording_profile(rig, cam, rig_config.defaults, rig_config.quirks)
        handle = rig.handle(cam.serial)
        writes = list(handle.writes)
        # restore mutates the handle afterwards; snapshot state checks go here
        state = {name: handle.node_read(name) for name in
                 ("Width", "Height", "OffsetX", "OffsetY", "ExposureTime", "Gain")}
        trigger_modes = {}
        for selector in rig_config.quirks.trigger_selectors_both:
            handle.node_write("TriggerSelector", selector)
            trigger_modes[selector] = handle.node_read("TriggerMode")
    return writes, state, trigger_modes


def test_mex_write_order(recording_backend, rig_config, tmp_path):
    writes, state, trigger_modes = _apply(recording_backend, rig_config, tmp_path)
    names = [name for name, _ in writes]

    def first(name):
        return names.index(name)

    # VideoMode -> PixelFormat -> sizes -> offsets -> exposure/gain
    assert first("VideoMode") < first("PixelFormat") < first("Width")
    assert first("Width") < first("Height") < first("OffsetX") < first("OffsetY")
    assert first("OffsetY") < first("ExposureAuto") < first("ExposureTime")
    assert first("ExposureTime") < first("GainAuto") < first("Gain")
    # TriggerMode Off before any trigger-node edit; On is the LAST TriggerMode write
    trigger_mode_values = [value for name, value in writes if name == "TriggerMode"]
    assert trigger_mode_values[0] == "Off"
    assert trigger_mode_values[-1] == "On"
    off_index = next(i for i, w in enumerate(writes) if w == ("TriggerMode", "Off"))
    assert off_index < first("TriggerSource") < first("TriggerActivation")
    # GS3 TriggerMode is ONE shared node (hardware-verified 2026-08-20): both
    # selectors read the same armed state
    assert trigger_modes == {"FrameStart": "On", "ExposureActive": "On"}
    # the line debouncer is load-bearing: without it, trigger-line ringing
    # fires a second frame per pulse (2x rate) whenever the sensor is idle
    assert ("LineDebouncerTimeRaw", 1000) in writes
    # TriggerOverlap is gated behind TriggerMode On — must be written AFTER
    overlap_index = names.index("TriggerOverlap")
    last_mode_on = max(i for i, w in enumerate(writes) if w == ("TriggerMode", "On"))
    assert overlap_index > last_mode_on
    # Rig invariant: the frame-rate VALUE is never written (Line0 clock only);
    # the rate GATE is disabled before exposure to lift the GS3 exposure ceiling
    assert "AcquisitionFrameRate" not in names
    assert ("AcquisitionFrameRateEnabled", False) in writes
    assert first("AcquisitionFrameRateEnabled") < first("ExposureTime")
    # Stream + throughput + chunks applied
    assert ("TLStream:StreamBufferHandlingMode", "OldestFirst") in writes
    assert first("TriggerMode") < first("DeviceLinkThroughputLimit")
    assert ("ChunkModeActive", True) in writes
    # ROI + exposure landed on the camera
    assert state == {"Width": 704, "Height": 800, "OffsetX": 160, "OffsetY": 112,
                     "ExposureTime": 5000.0, "Gain": 9.8}


def test_free_run_disarms_both_selectors(recording_backend, rig_config, tmp_path):
    cam = rig_config.by_position(1)
    with make_rig(recording_backend, [cam.serial], tmp_path) as rig:
        handle = rig.handle(cam.serial)
        set_free_run(rig, cam.serial, rig_config.quirks)
        for selector in rig_config.quirks.trigger_selectors_both:
            handle.node_write("TriggerSelector", selector)
            assert handle.node_read("TriggerMode") == "Off"
        assert not handle._flowing() or True  # free-run: frames flow without a clock
        assert handle._flowing()
        set_hw_trigger(rig, cam.serial, rig_config.defaults, rig_config.quirks)
        for selector in rig_config.quirks.trigger_selectors_both:
            handle.node_write("TriggerSelector", selector)
            assert handle.node_read("TriggerMode") == "On"  # shared node
        assert not handle._flowing()  # armed + no sim clock -> dark


def test_runtime_exposure_gain(recording_backend, rig_config, tmp_path):
    cam = rig_config.by_position(2)
    with make_rig(recording_backend, [cam.serial], tmp_path) as rig:
        configure_exposure_gain(rig, cam.serial, 4167.0, 0.0)
        handle = rig.handle(cam.serial)
        assert handle.node_read("ExposureTime") == 4167.0
        assert handle.node_read("Gain") == 0.0
