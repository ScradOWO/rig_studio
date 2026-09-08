"""Stimulus host process: fullscreen pygame window on the projector, driven by
JSON-lines commands on stdin; state transitions land in a JSON status file.

Runs out-of-process (projector_host.pyw pattern) so 240 Hz flip pacing never
competes with the GUI. Replicates the PTB grating loop: measured-dt phase
stepping, black inactive region, per-frame log, ESC abort.

Commands:
  {"cmd":"open", "projector":{...}, "windowed":bool}      create the window
  {"cmd":"preview", "grating":{...}}                      free-running grating
  {"cmd":"arm", "grating":{...}, "schedule":{"start_posix":f,"grating_s":f},
   "log_path":str, "base":str}                            black -> timed run
  {"cmd":"fliptest", "seconds":f}                         measure refresh
  {"cmd":"black"} | {"cmd":"stop"}                        back to black/idle
  {"cmd":"quit"}
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np

from rig_studio.stimulus import circles as ci
from rig_studio.stimulus import grating as gr
from rig_studio.stimulus.stimlog import StimLog, save_mat


def _write_status(path: Path, state: str, **extra) -> None:
    payload = {"state": state, "t": time.time(), **extra}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # GUI reading the file at this instant (Windows)
            time.sleep(0.02)
    # status is advisory — never let it take the host down


def _stdin_reader(commands: "queue.Queue[dict]") -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            commands.put(json.loads(line))
        except json.JSONDecodeError:
            commands.put({"cmd": "error", "raw": line})
    commands.put({"cmd": "quit"})


class Host:
    def __init__(self, status_path: Path) -> None:
        self.status_path = status_path
        self.commands: queue.Queue[dict] = queue.Queue()
        self.screen = None
        self.size = (0, 0)
        self.ifi = 1 / 60.0
        self.grating_surface = None
        self.quit = False

    def status(self, state: str, **extra) -> None:
        try:
            _write_status(self.status_path, state, **extra)
        except OSError:
            pass  # advisory only

    # ---- window ----
    def open_window(self, projector: dict, windowed: bool) -> None:
        import pygame

        if windowed:
            width, height = projector.get("width", 1920) // 2, projector.get("height", 1080) // 2
            os.environ.pop("SDL_VIDEO_WINDOW_POS", None)
        else:
            from rig_studio.util.display import enumerate_displays, select_projector

            displays = enumerate_displays()
            try:
                chosen = select_projector(
                    displays, projector["width"], projector["height"],
                    projector["refresh_hz"], name_hint=projector.get("name_hint", ""),
                )
            except RuntimeError:
                # Windows often lists the GamePix at 60 Hz (mode/EDID); fall back
                # to a resolution-only match, preferring a non-primary display.
                # Drift is dt-corrected, so speed in px/s is refresh-independent.
                fallback = [d for d in displays
                            if (d["width"], d["height"]) ==
                               (projector["width"], projector["height"])]
                fallback.sort(key=lambda d: d.get("primary", False))
                if not fallback:
                    raise
                chosen = fallback[0]
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{chosen['x']},{chosen['y']}"
            width, height = chosen["width"], chosen["height"]
        pygame.display.init()
        flags = pygame.NOFRAME
        try:
            self.screen = pygame.display.set_mode((width, height),
                                                  flags | pygame.SCALED, vsync=1)
        except pygame.error:
            self.screen = pygame.display.set_mode((width, height), flags)
        pygame.mouse.set_visible(False)
        self.size = (width, height)
        self.ifi = self.measure_ifi(0.5)
        self.status("ready", width=width, height=height, ifi=self.ifi,
                    refresh_hz=(1.0 / self.ifi if self.ifi else 0.0))

    def measure_ifi(self, seconds: float) -> float:
        import pygame

        self.screen.fill((0, 0, 0))
        pygame.display.flip()
        samples = []
        last = time.perf_counter()
        deadline = last + seconds
        while time.perf_counter() < deadline:
            pygame.display.flip()
            now = time.perf_counter()
            samples.append(now - last)
            last = now
        return float(np.median(samples)) if samples else 1 / 60.0

    # ---- grating rendering ----
    def _build_region(self, spec: dict) -> tuple:
        width, height = self.size
        rect = gr.region_rect(spec.get("region_preset", "bottomhalf"), width, height,
                              tuple(spec["custom_rect"]) if spec.get("custom_rect") else None)
        return rect

    def _make_region_surface(self, rw: int, rh: int):
        import pygame

        return pygame.Surface((rw, rh), depth=24)

    def run_grating(self, spec: dict, duration_s: float | None,
                    log: StimLog | None) -> str:
        """PTB loop: dt-corrected phase, black clear, region blit. Returns exit reason."""
        import pygame
        import pygame.surfarray as surfarray

        rect = self._build_region(spec)
        left, top, right, bottom = rect
        rw, rh = right - left, bottom - top
        period = float(spec.get("period_px", 157.5))
        speed = float(spec.get("speed_px_s", 120.0))
        direction = int(spec.get("direction", 1))
        contrast = float(spec.get("contrast", 1.0))
        rotation = float(spec.get("rotation_deg", 0.0))
        edges = tuple(spec.get("smoothstep_edges", (-0.2, 0.2)))
        zero_rotation = abs(math.sin(math.radians(rotation))) < 1e-9
        region_surface = self._make_region_surface(rw, rh)
        # luminance -> RGB ramp between the two grating colors
        bright = ci.hex_to_rgb(spec.get("color_bright", "#ffffff"))
        dark = ci.hex_to_rgb(spec.get("color_dark", "#000000"))
        ramp = np.linspace(0.0, 1.0, 256)[:, None]
        lut_rgb = np.rint(dark + (bright - dark) * ramp).astype(np.uint8)
        bg_rgb = tuple(int(v) for v in ci.hex_to_rgb(spec.get("bg_color", "#000000")))
        frame_buf = np.empty((rw, rh, 3), np.uint8)
        circles = spec.get("circles")
        patches = window_buf = top_rgb = bottom_rgb = None
        cycle_s = soft_px = 0.0
        pulse = True
        style = "fill"
        if circles:
            patches = ci.build_patches(circles["elements"], rect)
            cycle_s = float(circles.get("cycle_s", 2.0))
            soft_px = float(circles.get("soft_px", 5.0))
            pulse = bool(circles.get("pulse", True))
            style = str(circles.get("style", "fill"))
            if circles.get("color_top"):
                top_rgb = ci.hex_to_rgb(circles["color_top"])
            if circles.get("color_bottom"):
                bottom_rgb = ci.hex_to_rgb(circles["color_bottom"])
            if style == "window":
                window_buf = np.empty((rw, rh, 3), np.uint8)
                window_rgb = ci.hex_to_rgb(circles.get("window_bg", "#636363"))
        u_idx = lut = None
        if not zero_rotation:
            # Arbitrary rotation: 1D RGB LUT over one period + a static
            # rotated-coordinate index grid; per frame is one gather.
            lut_n = 4096
            lut = lut_rgb[np.rint(
                gr.profile(np.arange(lut_n) * (period / lut_n),
                           period, 0.0, edges, contrast) * 255.0).astype(np.uint8)]
            theta = math.radians(rotation)
            x = np.arange(rw, dtype=np.float64)[:, None]   # surfarray is (W, H)
            y = np.arange(rh, dtype=np.float64)[None, :]
            u = x * math.cos(theta) + y * math.sin(theta)
            u_idx = np.mod(np.rint(u * (lut_n / period)), lut_n).astype(np.int32)
        phase = 0.0
        t_acc = 0.0  # dt-corrected stimulus clock (drives the circle pulse)
        scale_expand = 0.0
        started = time.perf_counter()
        last_flip = None
        prev_flip_dt = float("nan")  # PTB: phase advances by the measured vbl-to-vbl dt
        while True:
            for event in pygame.event.get():
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    return "escape"
                if event.type == pygame.QUIT:
                    return "quit"
            if not self.commands.empty():
                return "command"
            now = time.perf_counter()
            if duration_s is not None and now - started >= duration_s:
                return "elapsed"
            dt = gr.corrected_dt(prev_flip_dt, self.ifi)
            phase = gr.step_phase(phase, direction, speed, dt, period)
            if zero_rotation:
                row_rgb = lut_rgb[gr.row_u8(rw, period, phase, edges, contrast)]
                frame_buf[:] = row_rgb[:, None, :]
                frame = frame_buf
            else:
                phase_idx = int(round(phase * (len(lut) / period))) % len(lut)
                frame = lut[(u_idx + phase_idx) % len(lut)]
            if patches is not None:
                if pulse:
                    t_acc += dt
                    scale_expand, scale_contract = ci.scales(t_acc, cycle_s)
                else:
                    scale_expand = scale_contract = 1.0
                if window_buf is None:
                    ci.stamp_fill(frame, patches, scale_expand, scale_contract,
                                  soft_px, top_rgb, bottom_rgb)
                else:
                    window_buf[:] = window_rgb
                    ci.stamp_window(window_buf, frame, patches,
                                    scale_expand, scale_contract, soft_px)
                    frame = window_buf
            surfarray.blit_array(region_surface, frame)
            self.screen.fill(bg_rgb)
            self.screen.blit(region_surface, (left, top))
            pygame.display.flip()
            flip_t = time.perf_counter()
            measured = flip_t - last_flip if last_flip else self.ifi
            prev_flip_dt = measured
            if log is not None:
                log.append(vbl=flip_t, t_stim=flip_t - started, dt=measured,
                           missed=1.0 if measured > 1.5 * self.ifi else 0.0,
                           yoffset=phase, phase_deg=(phase / period) * 360.0,
                           circle_scale=scale_expand)
            last_flip = flip_t

    def show_black(self) -> None:
        import pygame

        if self.screen is not None:
            self.screen.fill((0, 0, 0))
            pygame.display.flip()

    # ---- scheduled run (record + stimulus) ----
    def run_armed(self, message: dict) -> None:
        spec = message["grating"]
        schedule = message["schedule"]
        start_posix = float(schedule["start_posix"])
        grating_s = float(schedule["grating_s"])
        self.show_black()
        self.status("armed", start_posix=start_posix)
        while time.time() < start_posix:
            if not self.commands.empty():
                self.status("aborted")
                return
            self.show_black()  # keep flipping so vsync stays warm
            if start_posix - time.time() > 0.1:
                time.sleep(0.01)
        capacity = int(math.ceil(grating_s / max(self.ifi, 1e-4))) + 1000
        log = StimLog(capacity)
        self.status("grating", started_posix=time.time())
        reason = self.run_grating(spec, grating_s, log)
        self.show_black()
        log_path = message.get("log_path")
        if log_path:
            width, height = self.size
            rect = self._build_region(spec)
            metadata = {
                "speedPxPerSec": spec.get("speed_px_s", 120.0),
                "direction": spec.get("direction", 1),
                "ifi_f": self.ifi,
                "refreshHz": 1.0 / self.ifi if self.ifi else 0.0,
                "rect": np.array([0, 0, width, height], float),
                "width": width, "height": height,
                "spatial_period": spec.get("period_px", 157.5),
                "freqCpp": 1.0 / float(spec.get("period_px", 157.5)),
                "contrast": spec.get("contrast", 1.0),
                "duty": 0.5,  # preserved for MATLAB compat; a shader no-op there too
                "rotDegShader": spec.get("rotation_deg", 0.0),
                "stimProjectRegion": spec.get("region_preset", "bottomhalf"),
                "stimRect": np.array(rect, float),
                "stimWidth": rect[2] - rect[0], "stimHeight": rect[3] - rect[1],
                "stimInactiveColor": np.array([0.0, 0.0, 0.0]),
                "colorBright": spec.get("color_bright", "#ffffff"),
                "colorDark": spec.get("color_dark", "#000000"),
                "bgColor": spec.get("bg_color", "#000000"),
                "exitReason": reason,
                "startPosix": start_posix,
            }
            circles = spec.get("circles")
            if circles:
                # circleScale column = expanding-row radius fraction
                # (contracting row = 1 - circleScale; constant 1 when not pulsing)
                metadata["circlesCycleS"] = float(circles.get("cycle_s", 2.0))
                metadata["circlesSoftPx"] = float(circles.get("soft_px", 5.0))
                metadata["circlesSizes"] = np.array(
                    circles.get("sizes", [1.0, 1.0]), float)  # top, bottom
                metadata["circlesNths"] = np.array(
                    circles.get("nths", [1, 1]), float)       # top, bottom
                metadata["circlesStyle"] = str(circles.get("style", "fill"))
                metadata["circlesPulse"] = bool(circles.get("pulse", True))
                metadata["circlesWindowBg"] = str(circles.get("window_bg", "#636363"))
                metadata["circlesColorTop"] = str(circles.get("color_top") or "")
                metadata["circlesColorBottom"] = str(
                    circles.get("color_bottom") or "")
                metadata["circlesColors"] = np.array(
                    [e["color"] for e in circles["elements"]])
                metadata["circlesElements"] = np.array(
                    [[e["cx"], e["cy"], e["rx"], e["ry"], e["mode"]]
                     for e in circles["elements"]], float)  # cols: cx cy rx ry mode
            save_mat(log_path, log, metadata)
        self.status("done", frames=log.count, exit_reason=reason,
                    missed=int(np.sum(log.missed[:log.count] > 0)))

    # ---- main loop ----
    def run(self) -> None:
        threading.Thread(target=_stdin_reader, args=(self.commands,),
                         daemon=True).start()
        self.status("starting")
        while not self.quit:
            try:
                message = self.commands.get(timeout=0.1)
            except queue.Empty:
                if self.screen is not None:
                    import pygame

                    pygame.event.pump()
                continue
            try:
                self.dispatch(message)
            except Exception as error:  # noqa: BLE001
                self.status("error", error=f"{type(error).__name__}: {error}")

    def dispatch(self, message: dict) -> None:
        cmd = message.get("cmd")
        if cmd == "open":
            self.open_window(message["projector"], bool(message.get("windowed")))
        elif cmd == "preview":
            self.status("preview")
            reason = self.run_grating(message["grating"], None, None)
            self.show_black()
            self.status("ready", exit_reason=reason)
        elif cmd == "arm":
            self.run_armed(message)
        elif cmd == "fliptest":
            ifi = self.measure_ifi(float(message.get("seconds", 2.0)))
            self.ifi = ifi
            self.status("ready", ifi=ifi, refresh_hz=1.0 / ifi if ifi else 0.0)
        elif cmd in ("black", "stop"):
            self.show_black()
            self.status("ready")
        elif cmd == "quit":
            self.quit = True
            self.status("exited")
        else:
            self.status("error", error=f"unknown command: {cmd}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    args = parser.parse_args()
    try:
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00000080)  # HIGH_PRIORITY
    except Exception:  # noqa: BLE001
        pass
    Host(Path(args.status)).run()


if __name__ == "__main__":
    main()
