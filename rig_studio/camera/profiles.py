"""Camera configuration profiles. Write ORDER is load-bearing — ported from
flir_camera_helper_c.cpp (MEX): VideoMode → PixelFormat → binning → sizes →
offsets → exposure/gain → trigger (Mode Off FIRST … Mode On LAST) → line →
stream buffers → throughput → chunks. AcquisitionFrameRate is NEVER written:
the frame rate is the external Line0 clock.
"""
from __future__ import annotations

import logging
from typing import Any

from rig_studio.config import CameraDefaults, CameraEntry, Quirks
from rig_studio.safety.guarded import GuardedRig

log = logging.getLogger(__name__)

MODE_SENSOR_SCALE = {
    "Mode0": (1, 1),
    "Mode1": (2, 2),
    "Mode2": (1, 2),
}


def scale_geometry_for_video_mode(
    current_mode: str,
    target_mode: str,
    width: int,
    height: int,
    offset_x: int,
    offset_y: int,
) -> tuple[int, int, int, int]:
    """Preserve the same sensor area across per-axis video-mode binning."""
    try:
        current_x, current_y = MODE_SENSOR_SCALE[current_mode]
        target_x, target_y = MODE_SENSOR_SCALE[target_mode]
    except KeyError as error:
        raise ValueError(f"unsupported video mode: {error.args[0]}") from error
    return (
        max(0, round(width * current_x / target_x)),
        max(0, round(height * current_y / target_y)),
        max(0, round(offset_x * current_x / target_x)),
        max(0, round(offset_y * current_y / target_y)),
    )


def clamp_node_value(entry: dict[str, Any], value: int) -> int:
    """Clamp an integer node value to its current GenICam bounds/increment."""
    lo = int(entry.get("min", 0))
    hi = int(entry.get("max", value))
    inc = max(1, int(entry.get("inc", 1)))
    value = max(lo, min(int(value), hi))
    return lo + ((value - lo) // inc) * inc


def _soft(rig: GuardedRig, serial: str, name: str, value: Any) -> bool:
    """MEX-style soft write: failure is logged, not fatal."""
    try:
        rig.set(serial, name, value)
        return True
    except Exception as error:  # noqa: BLE001
        log.warning("%s: soft write %s=%r failed: %s", serial, name, value, error)
        return False


def _limit(rig: GuardedRig, serial: str, name: str, key: str, fallback: int) -> int:
    entry = rig.snapshot(serial).get(name, {})
    value = entry.get(key)
    return int(value) if isinstance(value, (int, float)) else fallback


def apply_recording_profile(
    rig: GuardedRig, cam: CameraEntry, defaults: CameraDefaults, quirks: Quirks
) -> None:
    serial = cam.serial
    _soft(rig, serial, "VideoMode", cam.video_mode)  # enum string, e.g. "Mode2"
    _soft(rig, serial, "PixelFormat", defaults.pixel_format)
    if defaults.binning_vertical > 1:
        _soft(rig, serial, "BinningVertical", defaults.binning_vertical)
    if defaults.binning_horizontal > 1:
        _soft(rig, serial, "BinningHorizontal", defaults.binning_horizontal)
    # Sizes before offsets so GenICam limits validate (MEX comment).
    rig.set(serial, "Width", cam.roi.width)
    rig.set(serial, "Height", cam.roi.height)
    rig.set(serial, "OffsetX", cam.roi.offset_x)
    rig.set(serial, "OffsetY", cam.roi.offset_y)
    # A free-running boot state gates ExposureTime max to the frame period
    # (~4.1 ms at 244 Hz — rejects the 5000 µs default). Disabling the rate
    # gate lifts the ceiling (GS3 quirk, documented in graycode). This is the
    # gate node, not a frame-rate value: the rate itself is never written.
    _soft(rig, serial, "AcquisitionFrameRateAuto", "Off")
    _soft(rig, serial, "AcquisitionFrameRateEnabled", False)
    rig.set(serial, "ExposureAuto", defaults.exposure_auto)
    rig.set(serial, "ExposureMode", defaults.exposure_mode)
    rig.set(serial, "ExposureTime", defaults.exposure_time_us)
    rig.set(serial, "GainAuto", defaults.gain_auto)
    rig.set(serial, "Gain", defaults.gain_db)
    _soft(rig, serial, "GammaEnabled", defaults.gamma_enabled)
    # TriggerMode must be Off (both selectors) while trigger nodes are edited.
    for selector in quirks.trigger_selectors_both:
        rig.set(serial, "TriggerSelector", selector)
        rig.set(serial, "TriggerMode", "Off")
    rig.set(serial, "TriggerSelector", defaults.trigger_selector)
    rig.set(serial, "TriggerSource", defaults.trigger_source)
    rig.set(serial, "TriggerActivation", defaults.trigger_activation)
    delay_min = rig.snapshot(serial).get("TriggerDelay", {}).get("min")
    if delay_min is not None:
        _soft(rig, serial, "TriggerDelay", float(delay_min))
    _soft(rig, serial, "LineSelector", "Line0")
    _soft(rig, serial, "LineMode", "Input")
    _soft(rig, serial, "LineInverter", False)
    # the debouncer is load-bearing: trigger-line ringing fires a second frame
    # per pulse whenever the sensor is idle (2x rate below ~90 Hz)
    _soft(rig, serial, "LineDebouncerTimeRaw", defaults.line_debouncer_raw)
    # Re-arm LAST (MEX order). GS3 TriggerMode is ONE shared node, not banked
    # per selector, so arming under FrameStart is the whole story.
    rig.set(serial, "TriggerSelector", defaults.trigger_selector)
    rig.set(serial, "TriggerMode", "On")
    # TriggerOverlap is gated: only writable while TriggerMode is On
    _soft(rig, serial, "TriggerOverlap", defaults.trigger_overlap)
    _soft(rig, serial, "TLStream:StreamBufferCountMode", defaults.stream_buffer_count_mode)
    count_max = _limit(rig, serial, "TLStream:StreamBufferCountManual", "max",
                       defaults.stream_buffer_count_max)
    _soft(rig, serial, "TLStream:StreamBufferCountManual",
          min(defaults.stream_buffer_count_max, count_max))
    _soft(rig, serial, "TLStream:StreamBufferHandlingMode", defaults.stream_handling_mode)
    limit = defaults.device_link_throughput_limit
    lo = _limit(rig, serial, "DeviceLinkThroughputLimit", "min", limit)
    hi = _limit(rig, serial, "DeviceLinkThroughputLimit", "max", limit)
    _soft(rig, serial, "DeviceLinkThroughputLimit", max(lo, min(limit, hi)))
    if _soft(rig, serial, "ChunkModeActive", True):
        for chunk in defaults.chunk_selectors:
            _soft(rig, serial, "ChunkSelector", chunk)
            _soft(rig, serial, "ChunkEnable", True)


def apply_infrastructure_profile(
    rig: GuardedRig, serial: str, defaults: CameraDefaults, quirks: Quirks
) -> None:
    """Adopt the camera's current imaging state (exposure/gain/gamma/ROI/format
    stay EXACTLY as set in SpinView); configure only what the pipeline needs:
    trigger wiring, line input, stream buffers, USB throughput, chunks, and the
    frame-rate gate (exposure ceiling)."""
    _soft(rig, serial, "AcquisitionFrameRateAuto", "Off")
    _soft(rig, serial, "AcquisitionFrameRateEnabled", False)
    for selector in quirks.trigger_selectors_both:
        rig.set(serial, "TriggerSelector", selector)
        rig.set(serial, "TriggerMode", "Off")
    rig.set(serial, "TriggerSelector", defaults.trigger_selector)
    rig.set(serial, "TriggerSource", defaults.trigger_source)
    rig.set(serial, "TriggerActivation", defaults.trigger_activation)
    _soft(rig, serial, "LineSelector", "Line0")
    _soft(rig, serial, "LineMode", "Input")
    _soft(rig, serial, "LineInverter", False)
    # load-bearing: filters trigger-line ringing (2x frame rate without it)
    _soft(rig, serial, "LineDebouncerTimeRaw", defaults.line_debouncer_raw)
    rig.set(serial, "TriggerSelector", defaults.trigger_selector)
    rig.set(serial, "TriggerMode", "On")
    # TriggerOverlap is gated: only writable while TriggerMode is On
    _soft(rig, serial, "TriggerOverlap", defaults.trigger_overlap)
    _soft(rig, serial, "TLStream:StreamBufferCountMode", defaults.stream_buffer_count_mode)
    count_max = _limit(rig, serial, "TLStream:StreamBufferCountManual", "max",
                       defaults.stream_buffer_count_max)
    _soft(rig, serial, "TLStream:StreamBufferCountManual",
          min(defaults.stream_buffer_count_max, count_max))
    _soft(rig, serial, "TLStream:StreamBufferHandlingMode", defaults.stream_handling_mode)
    limit = defaults.device_link_throughput_limit
    lo = _limit(rig, serial, "DeviceLinkThroughputLimit", "min", limit)
    hi = _limit(rig, serial, "DeviceLinkThroughputLimit", "max", limit)
    _soft(rig, serial, "DeviceLinkThroughputLimit", max(lo, min(limit, hi)))
    if _soft(rig, serial, "ChunkModeActive", True):
        for chunk in defaults.chunk_selectors:
            _soft(rig, serial, "ChunkSelector", chunk)
            _soft(rig, serial, "ChunkEnable", True)


def set_free_run(rig: GuardedRig, serial: str, quirks: Quirks) -> None:
    """Preview without the Line0 clock: TriggerMode Off on BOTH selectors."""
    for selector in quirks.trigger_selectors_both:
        rig.set(serial, "TriggerSelector", selector)
        rig.set(serial, "TriggerMode", "Off")
    rig.set(serial, "TriggerSelector", quirks.trigger_selectors_both[0])


def set_hw_trigger(rig: GuardedRig, serial: str, defaults: CameraDefaults,
                   quirks: Quirks) -> None:
    """Re-arm hardware trigger: disarm, edit, arm LAST (MEX order). GS3
    TriggerMode is one shared node, so FrameStart is the whole story."""
    for selector in quirks.trigger_selectors_both:
        rig.set(serial, "TriggerSelector", selector)
        rig.set(serial, "TriggerMode", "Off")
        rig.set(serial, "TriggerSource", defaults.trigger_source)
    rig.set(serial, "TriggerSelector", defaults.trigger_selector)
    rig.set(serial, "TriggerActivation", defaults.trigger_activation)
    _soft(rig, serial, "LineDebouncerTimeRaw", defaults.line_debouncer_raw)
    rig.set(serial, "TriggerMode", "On")
    _soft(rig, serial, "TriggerOverlap", defaults.trigger_overlap)


def configure_exposure_gain(rig: GuardedRig, serial: str,
                            exposure_us: float, gain_db: float) -> None:
    """Runtime reconfigure (MEX `configure` command)."""
    rig.set(serial, "ExposureTime", float(exposure_us))
    rig.set(serial, "Gain", float(gain_db))


def gamma_canary_ok(rig: GuardedRig, quirks: Quirks) -> bool:
    """22246599 historically drifts GammaEnabled back on; re-check after writes."""
    try:
        handle = rig.handle(quirks.gamma_canary_serial)
    except KeyError:
        return True
    value = bool(handle.node_read_nocache("GammaEnabled"))
    if value:
        log.warning("gamma canary %s: GammaEnabled drifted to True",
                    quirks.gamma_canary_serial)
    return not value
