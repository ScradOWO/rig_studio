# Vendored from dragstrip_instrument/dragstrip_instrument/display.py @ 2026-08-17 (unchanged).
from __future__ import annotations

import sys
from typing import Iterable


def enumerate_displays() -> list[dict]:
    """Read active Windows displays, including mode and friendly monitor name."""

    if not sys.platform.startswith("win"):
        raise RuntimeError("projector display discovery currently requires Windows")
    import ctypes
    import ctypes.wintypes as wt

    class DisplayDevice(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("DeviceName", ctypes.c_wchar * 32),
                    ("DeviceString", ctypes.c_wchar * 128),
                    ("StateFlags", wt.DWORD), ("DeviceID", ctypes.c_wchar * 128),
                    ("DeviceKey", ctypes.c_wchar * 128)]

    class DevMode(ctypes.Structure):
        _fields_ = [("dmDeviceName", ctypes.c_wchar * 32),
                    ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                    ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD),
                    ("dmFields", wt.DWORD), ("dmPositionX", ctypes.c_long),
                    ("dmPositionY", ctypes.c_long), ("dmDisplayOrientation", wt.DWORD),
                    ("dmDisplayFixedOutput", wt.DWORD), ("dmColor", ctypes.c_short),
                    ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                    ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                    ("dmFormName", ctypes.c_wchar * 32), ("dmLogPixels", wt.WORD),
                    ("dmBitsPerPel", wt.DWORD), ("dmPelsWidth", wt.DWORD),
                    ("dmPelsHeight", wt.DWORD), ("dmDisplayFlags", wt.DWORD),
                    ("dmDisplayFrequency", wt.DWORD), ("dmICMMethod", wt.DWORD),
                    ("dmICMIntent", wt.DWORD), ("dmMediaType", wt.DWORD),
                    ("dmDitherType", wt.DWORD), ("dmReserved1", wt.DWORD),
                    ("dmReserved2", wt.DWORD), ("dmPanningWidth", wt.DWORD),
                    ("dmPanningHeight", wt.DWORD)]

    user32 = ctypes.windll.user32
    output = []
    adapter_index = 0
    while True:
        adapter = DisplayDevice(); adapter.cb = ctypes.sizeof(adapter)
        if not user32.EnumDisplayDevicesW(None, adapter_index,
                                          ctypes.byref(adapter), 0):
            break
        adapter_index += 1
        if not adapter.StateFlags & 0x1:
            continue
        mode = DevMode(); mode.dmSize = ctypes.sizeof(mode)
        if not user32.EnumDisplaySettingsW(adapter.DeviceName, -1,
                                           ctypes.byref(mode)):
            continue
        friendly = adapter.DeviceString
        child = DisplayDevice(); child.cb = ctypes.sizeof(child)
        if user32.EnumDisplayDevicesW(adapter.DeviceName, 0,
                                      ctypes.byref(child), 0):
            friendly = child.DeviceString or friendly
        output.append({
            "device": adapter.DeviceName,
            "name": friendly,
            "x": int(mode.dmPositionX), "y": int(mode.dmPositionY),
            "width": int(mode.dmPelsWidth), "height": int(mode.dmPelsHeight),
            "refresh_hz": int(mode.dmDisplayFrequency),
            "primary": bool(adapter.StateFlags & 0x4),
        })
    return output


def select_projector(displays: Iterable[dict], width: int, height: int,
                     refresh_hz: float, name_hint: str = "",
                     preferred_xy: tuple[int, int] | None = None) -> dict:
    rows = [dict(display) for display in displays]
    candidates = [display for display in rows
                  if (display["width"], display["height"]) == (width, height)
                  and abs(float(display["refresh_hz"]) - refresh_hz) <= 1.0]
    hint = name_hint.casefold().strip()
    named = [display for display in candidates
             if hint and hint in str(display.get("name", "")).casefold()]
    if len(named) == 1:
        return named[0]
    if preferred_xy is not None:
        positioned = [display for display in candidates
                      if (display["x"], display["y"]) == preferred_xy]
        if len(positioned) == 1:
            return positioned[0]
    if len(candidates) == 1:
        return candidates[0]
    details = "; ".join(
        f"{d.get('name')} {d['width']}x{d['height']}@{d['refresh_hz']} "
        f"at ({d['x']},{d['y']})" for d in rows)
    if not candidates:
        raise RuntimeError(f"no {width}x{height}@{refresh_hz:g}Hz projector; {details}")
    raise RuntimeError(f"multiple matching projectors; set a name hint or position; {details}")
