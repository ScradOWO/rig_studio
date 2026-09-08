# Node-name constants for the FLIR GS3-U3-41C6NIR fleet.
# Base sets vendored from graycode_v2/gc2/cameras.py @ 2026-08-17; extended for
# rig-studio's SpinView-parity controls (ROI, VideoMode, TLStream, chunks).
# GS3 firmware naming: GammaEnabled / AcquisitionFrameRateEnabled (not *Enable).
from __future__ import annotations

SNAPSHOT_NODES = (
    "AcquisitionMode", "VideoMode", "PixelFormat",
    "Width", "Height", "OffsetX", "OffsetY",
    "BinningHorizontal", "BinningVertical",
    "DecimationHorizontal", "DecimationVertical",
    "GammaEnabled", "Gamma", "ExposureAuto", "ExposureMode", "ExposureTime",
    "GainAuto", "Gain",
    "pgrExposureCompensationAuto", "pgrExposureCompensation",
    "BlackLevel", "BlackLevelRaw",
    "TriggerMode", "TriggerSource", "TriggerActivation", "TriggerOverlap",
    "AcquisitionFrameRateAuto", "AcquisitionFrameRateEnabled",
    "AcquisitionFrameRate", "DeviceLinkThroughputLimit",
    "ChunkModeActive",
    "LineSelector", "LineMode", "LineInverter", "LineDebouncerTimeRaw",
)

# Rig invariant: AcquisitionFrameRate* stays writable only for restore paths;
# the recording profile never sets it (frame rate comes from the Line0 clock).
MUTABLE_ALLOWLIST = frozenset({
    "AcquisitionMode", "VideoMode", "PixelFormat",
    "Width", "Height", "OffsetX", "OffsetY",
    "BinningHorizontal", "BinningVertical",
    "GammaEnabled", "Gamma",
    "ExposureAuto", "ExposureMode", "ExposureTime", "GainAuto", "Gain",
    "pgrExposureCompensationAuto", "BlackLevel",
    "TriggerSelector", "TriggerMode", "TriggerSource", "TriggerActivation",
    "TriggerOverlap", "TriggerDelay",
    "AcquisitionFrameRateAuto", "AcquisitionFrameRateEnabled",
    "AcquisitionFrameRate",
    "DeviceLinkThroughputLimit",
    "ChunkModeActive", "ChunkSelector", "ChunkEnable",
    "LineSelector", "LineMode", "LineInverter", "LineDebouncerTimeRaw",
    "TLStream:StreamBufferCountMode",
    "TLStream:StreamBufferCountManual",
    "TLStream:StreamBufferHandlingMode",
})

FORBIDDEN_SUBSTRINGS = ("FactoryReset", "UserSet", "DeviceReset",
                        "FileAccess", "Firmware", "DeviceRegisters")

# Dependency-safe restore order (see gc2: TriggerMode=On locks the frame-rate
# node group on the GS3, so the trigger chain restores LAST).
RESTORE_ORDER = (
    "AcquisitionMode", "VideoMode", "PixelFormat", "GammaEnabled", "Gamma",
    "_ENABLE_RATE_GATE",  # sentinel: AcquisitionFrameRateEnabled=True
    "AcquisitionFrameRate", "AcquisitionFrameRateAuto",
    "AcquisitionFrameRateEnabled",
    "ExposureAuto", "ExposureMode", "ExposureTime", "GainAuto", "Gain",
    "pgrExposureCompensationAuto", "BlackLevel",
    "TriggerActivation", "TriggerSource", "TriggerMode",
)

DEFAULT_RESTORE = {"AcquisitionFrameRateEnabled": True}
