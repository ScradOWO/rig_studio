"""Per-camera worker process: owns one native camera handle, a fetch
thread, an HDF5 writer thread, and a control loop on the parent pipe.

Pipe EOF => unconditional clean stop (a GUI crash can never corrupt H5 files).
Commands/events are small dicts. One worker == one crash domain == one file.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from rig_studio.camera.profiles import (
    apply_infrastructure_profile,
    apply_recording_profile,
    clamp_node_value,
    configure_exposure_gain,
    scale_geometry_for_video_mode,
    set_free_run,
    set_hw_trigger,
)
from rig_studio.config import CameraDefaults, Quirks
from rig_studio.recorder import clockcal
from rig_studio.recorder.previewbus import PreviewPublisher
from rig_studio.recorder.ring import FrameRing
from rig_studio.recording.h5_writer import CamH5Writer
from rig_studio.recording.export_frames import write_gray8_png
from rig_studio.safety.guarded import GuardedRig

log = logging.getLogger(__name__)

STATUS_INTERVAL_S = 0.25
WRITER_WAKE_S = 0.05
TIMED_FLUSH_S = 1.0
WRITER_READY_S = 10.0
PREVIEW_BUFFERS = 64  # lighter than the recording depth; faster start/stop


@dataclass(frozen=True)
class WorkerConfig:
    serial: str
    position: int
    label: str
    backend_kind: str            # spinnaker | harvesters | sim
    cti: str
    frame_metadata_nodes: dict
    roi: tuple                   # (width, height, offset_x, offset_y)
    video_mode: str
    defaults: CameraDefaults
    quirks: Quirks
    allowlist: frozenset
    snapshot_dir: str
    marker_path: str             # unique per serial
    exposure_tolerance_us: float
    pool_seconds: float
    fps_nominal: float
    queue_cap: int
    chunk_frames: int
    drop_log_every: int
    zstd_level: int | None
    shm_name: str
    preview_decimate: int
    preview_min_interval_s: float
    sim_fps: float = 178.0
    apply_profile: bool = False  # False = adopt current (SpinView) imaging state


def _is_timeout(error: BaseException) -> bool:
    return isinstance(error, TimeoutError) or "Timeout" in type(error).__name__


def _make_backend(cfg: WorkerConfig):
    if cfg.backend_kind == "sim":
        from rig_studio.backend.sim_backend import SimBackend

        return SimBackend([cfg.serial], {cfg.serial: tuple(cfg.roi)}, fps=cfg.sim_fps)
    if cfg.backend_kind == "harvesters":
        from rig_studio.backend.harvesters_backend import HarvestersBackend

        return HarvestersBackend(cfg.cti, [cfg.serial],
                                 frame_metadata_nodes=cfg.frame_metadata_nodes)
    if cfg.backend_kind == "spinnaker":
        from rig_studio.backend.spinnaker_backend import SpinnakerBackend

        return SpinnakerBackend([cfg.serial])
    raise ValueError(f"unknown backend: {cfg.backend_kind}")


def _raise_priority() -> None:
    try:
        import psutil

        psutil.Process().nice(psutil.HIGH_PRIORITY_CLASS)
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:  # noqa: BLE001
        pass


class _Recording:
    """State shared between the control loop, fetch thread, and writer thread."""

    def __init__(self, cfg: WorkerConfig, out_path: str, max_frames: int,
                 width: int, height: int, fps_nominal: float,
                 frames_only_count: int | None = None,
                 capture_duration_s: float | None = None) -> None:
        width, height = int(width), int(height)
        requested_pool = math.ceil(cfg.pool_seconds * max(float(fps_nominal) * 1.01, 1.0))
        pool_frames = max(2, min(requested_pool, cfg.queue_cap))
        self.ring = FrameRing(pool_frames, width * height,
                              queue_cap=cfg.queue_cap, drop_log_every=cfg.drop_log_every)
        self.out_path = out_path
        self.max_frames = int(max_frames)
        self.frames_only_count = frames_only_count
        self.capture_duration_s = float(capture_duration_s or 0.0)
        self.expected_frames = (
            int(frames_only_count) if frames_only_count not in (None, 0) else None)
        self.capture_ordinal = 0
        self.next_capture_index = 0
        self.width, self.height = width, height
        self.gate_perf_ns: int | None = None
        self.origin_posix_ns: int | None = None
        self.first_frame_seen = False
        self.complete = False
        self.frames_written = 0
        self.write_wake = threading.Event()
        self.stop_writer = threading.Event()
        self.writer_ready = threading.Event()
        self.writer_error: str | None = None
        self.size_mismatch_logged = False


class Worker:
    def __init__(self, conn, cfg: WorkerConfig) -> None:
        self.conn = conn
        self.cfg = cfg
        self.events: queue.Queue[dict] = queue.Queue()
        self.backend = None
        self.rig: GuardedRig | None = None
        self.handle = None
        self.publisher: PreviewPublisher | None = None
        self.rec: _Recording | None = None
        self.acquiring = False
        self.acq_buffers = 0
        self.stop_fetch = threading.Event()
        self.fetch_thread: threading.Thread | None = None
        self.writer_thread: threading.Thread | None = None
        self.clock_offset_ns = clockcal.posix_offset_ns()
        self.frames_seen = 0
        self.last_frame_perf = 0.0
        self.shutting_down = False
        self.advanced_originals: dict[str, Any] = {}
        self.fetch_buffer: np.ndarray | None = None
        self.preview_free_run = False

    # ---- event plumbing (threads never touch the pipe directly) ----
    def emit(self, event: dict) -> None:
        self.events.put(event)

    def _flush_events(self) -> None:
        while True:
            try:
                self.conn.send(self.events.get_nowait())
            except queue.Empty:
                return
            except (BrokenPipeError, OSError):
                return

    # ---- command handlers ----
    def do_configure(self) -> dict:
        self.backend = _make_backend(self.cfg)
        marker = Path(self.cfg.marker_path)
        if marker.exists():
            # stale marker from a crashed session: replay its restore, then proceed
            from rig_studio.safety.guarded import restore_from_marker

            try:
                restore_from_marker(self.backend,
                                    json.loads(marker.read_text(encoding="utf-8")),
                                    marker)
                self.emit({"evt": "log", "level": "warning",
                           "message": "recovered stale crash marker; camera restored"})
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(
                    f"stale crash marker could not be restored ({error}); "
                    f"inspect {marker}") from error
        self.rig = GuardedRig(
            self.backend, [self.cfg.serial], self.cfg.allowlist,
            snapshot_dir=self.cfg.snapshot_dir, marker_path=self.cfg.marker_path,
            exposure_tolerance_us=self.cfg.exposure_tolerance_us,
        )
        self.rig.__enter__()
        self.handle = self.rig.handle(self.cfg.serial)
        if self.cfg.apply_profile:
            from rig_studio.config import CameraEntry, Roi

            cam = CameraEntry(self.cfg.position, self.cfg.serial, self.cfg.label,
                              Roi(*self.cfg.roi), "", self.cfg.video_mode)
            apply_recording_profile(self.rig, cam, self.cfg.defaults, self.cfg.quirks)
        else:
            # adopt the camera's current (SpinView) imaging state as-is
            apply_infrastructure_profile(self.rig, self.cfg.serial,
                                         self.cfg.defaults, self.cfg.quirks)
        self.publisher = PreviewPublisher(
            self.cfg.shm_name, decimate=self.cfg.preview_decimate,
            min_interval_s=self.cfg.preview_min_interval_s,
        )
        state = {
            name: self.handle.node_read_nocache(name)
            for name in ("Width", "Height", "OffsetX", "OffsetY",
                         "ExposureTime", "Gain", "PixelFormat")
        }
        return {"evt": "configured", "serial": self.cfg.serial, "state": state}

    def do_preview_mode(self, free_run: bool) -> dict:
        if self.acquiring:
            raise RuntimeError("stop acquisition before changing trigger mode")
        if free_run:
            set_free_run(self.rig, self.cfg.serial, self.cfg.quirks)
        else:
            set_hw_trigger(self.rig, self.cfg.serial, self.cfg.defaults, self.cfg.quirks)
        self.preview_free_run = bool(free_run)
        return {"evt": "preview_mode", "free_run": free_run}

    def do_start_acquisition(self, buffers: int = PREVIEW_BUFFERS) -> dict:
        if self.acquiring and buffers > self.acq_buffers:
            self.do_stop_acquisition()  # restart deeper for recording
        if not self.acquiring:
            self.handle.begin_acquisition(buffers)
            width = int(self.handle.node_read_nocache("Width"))
            height = int(self.handle.node_read_nocache("Height"))
            self.fetch_buffer = np.empty((height, width), dtype=np.uint8)
            self.acq_buffers = buffers
            self.acquiring = True
            self.stop_fetch.clear()
            self.fetch_thread = threading.Thread(target=self._fetch_loop,
                                                 name="fetch", daemon=True)
            self.fetch_thread.start()
        return {"evt": "acquisition", "running": True}

    def do_stop_acquisition(self) -> dict:
        self.stop_fetch.set()
        if self.fetch_thread is not None:
            self.fetch_thread.join(timeout=2.0)
            self.fetch_thread = None
        if self.acquiring:
            self.handle.end_acquisition()
            self.acquiring = False
        self.fetch_buffer = None
        return {"evt": "acquisition", "running": False}

    def do_node_write(self, name: str, value: Any, advanced: bool = False) -> dict:
        geometry = name in ("Width", "Height", "OffsetX", "OffsetY", "PixelFormat",
                            "BinningHorizontal", "BinningVertical", "VideoMode")
        was_acquiring = False
        if geometry:
            if self.rec is not None:
                raise RuntimeError(f"{name}: stop the recording first")
            was_acquiring = self.acquiring
            if was_acquiring:  # pause preview around geometry edits, then resume
                self.do_stop_acquisition()
        try:
            if advanced:
                from rig_studio.safety.allowlist import FORBIDDEN_SUBSTRINGS

                if any(sub in name for sub in FORBIDDEN_SUBSTRINGS):
                    raise RuntimeError(f"forbidden node: {name}")
                # outside the allowlist: snapshot the original ourselves so
                # teardown can still restore exactly-as-found
                if name not in self.advanced_originals:
                    self.advanced_originals[name] = self.handle.node_read(name)
                self.handle.node_write(name, value)
            else:
                self.rig.set(self.cfg.serial, name, value)
            return {"evt": "write_result", "name": name, "ok": True,
                    "value": self.handle.node_read_nocache(name)}
        finally:
            if was_acquiring:  # resume preview even when the write failed
                self.do_start_acquisition()

    def do_read_many(self, names: list[str]) -> dict:
        values: dict[str, Any] = {}
        for name in names:
            try:
                values[name] = self.handle.node_read_nocache(name)
            except Exception:  # noqa: BLE001 — gated/absent nodes read as null
                values[name] = None
        return {"evt": "values", "values": values}

    def do_video_mode_scaled(self, target_mode: str) -> dict:
        """Switch modes atomically and preserve the same physical sensor ROI."""
        if target_mode not in ("Mode0", "Mode1", "Mode2"):
            raise ValueError(f"unsupported VideoMode: {target_mode}")
        if self.rec is not None:
            raise RuntimeError("stop the recording before changing VideoMode")
        was_acquiring = self.acquiring
        if was_acquiring:
            self.do_stop_acquisition()
        try:
            current_mode = str(self.handle.node_read_nocache("VideoMode"))
            geometry = tuple(int(self.handle.node_read_nocache(name)) for name in
                             ("Width", "Height", "OffsetX", "OffsetY"))
            desired = scale_geometry_for_video_mode(current_mode, target_mode, *geometry)
            self.rig.set(self.cfg.serial, "VideoMode", target_mode)

            # Clear offsets before enlarging the ROI; GenICam otherwise lowers
            # Width/Height maxima based on the old offset.
            for name in ("OffsetX", "OffsetY"):
                dump = self.handle.nodemap_dump()
                self.rig.set(self.cfg.serial, name, clamp_node_value(dump[name], 0))
            for name, value in zip(("Width", "Height"), desired[:2]):
                dump = self.handle.nodemap_dump()
                self.rig.set(self.cfg.serial, name, clamp_node_value(dump[name], value))
            for name, value in zip(("OffsetX", "OffsetY"), desired[2:]):
                dump = self.handle.nodemap_dump()
                self.rig.set(self.cfg.serial, name, clamp_node_value(dump[name], value))

            values = {name: self.handle.node_read_nocache(name) for name in
                      ("VideoMode", "Width", "Height", "OffsetX", "OffsetY")}
            return {"evt": "mode_result", "ok": True, "values": values}
        finally:
            if was_acquiring:
                self.do_start_acquisition()

    def do_arm(self, out_path: str, max_frames: int,
               fps_nominal: float | None = None,
               frames_only_count: int | None = None,
               capture_duration_s: float | None = None) -> None:
        if self.rec is not None:
            raise RuntimeError("already armed/recording")
        # record whatever crop is on the camera right now (SpinView state)
        pixel_format = str(self.handle.node_read_nocache("PixelFormat"))
        if pixel_format != "Mono8":
            raise RuntimeError(f"recording requires Mono8, camera is {pixel_format}")
        width = int(self.handle.node_read_nocache("Width"))
        height = int(self.handle.node_read_nocache("Height"))
        rec = _Recording(
            self.cfg, out_path, max_frames, width, height,
            self.cfg.fps_nominal if fps_nominal is None else fps_nominal,
            frames_only_count=frames_only_count,
            capture_duration_s=capture_duration_s,
        )
        self.writer_thread = threading.Thread(
            target=(self._png_writer_loop if frames_only_count is not None
                    else self._writer_loop),
            args=(rec,), name=("pngwriter" if frames_only_count is not None
                              else "h5writer"), daemon=True)
        self.writer_thread.start()
        if not rec.writer_ready.wait(WRITER_READY_S) or rec.writer_error:
            rec.stop_writer.set()
            raise RuntimeError(rec.writer_error or "H5 writer did not become ready")
        self.rec = rec  # publish to the fetch thread last
        self.do_start_acquisition(buffers=self.cfg.defaults.stream_buffer_count_max)

    def do_go(self, gate_posix_ns: int) -> None:
        rec = self.rec
        if rec is None:
            raise RuntimeError("not armed")
        rec.origin_posix_ns = int(gate_posix_ns)
        rec.gate_perf_ns = clockcal.perf_ns_at_posix(int(gate_posix_ns),
                                                     self.clock_offset_ns)

    def do_stop_recording(self) -> dict:
        rec, self.rec = self.rec, None
        if rec is None:
            return {"evt": "done", "report": None}
        rec.stop_writer.set()
        rec.write_wake.set()
        if self.writer_thread is not None:
            self.writer_thread.join(timeout=30.0)
            self.writer_thread = None
        return {"evt": "done", "report": {
            "serial": self.cfg.serial,
            "position": self.cfg.position,
            "path": rec.out_path,
            "output_kind": ("png_frames" if rec.frames_only_count is not None
                            else "hdf5"),
            "frames_written": rec.frames_written,
            "dropped": rec.ring.dropped,
            # Legacy `complete` means the preallocated file filled to max_frames;
            # a normal timed/manual stop is successful even when it stays False.
            "complete": rec.complete,
            "max_frames_reached": (rec.complete if rec.frames_only_count is None
                                   else False),
            "requested_frames": rec.frames_only_count,
            "stopped_cleanly": rec.writer_error is None,
            "writer_error": rec.writer_error,
        }}

    # ---- threads ----
    def _fetch_loop(self) -> None:
        cfg = self.cfg
        while not self.stop_fetch.is_set():
            rec = self.rec
            direct_attempted = False
            direct_slot: int | None = None
            target = self.fetch_buffer
            if target is None:
                self.emit({"evt": "fault", "where": "fetch",
                           "error": "fetch buffer is not initialized"})
                return
            if (rec is not None and rec.frames_only_count is None
                    and not rec.complete and rec.gate_perf_ns is not None
                    and time.perf_counter_ns() >= rec.gate_perf_ns):
                direct_attempted = True
                direct_slot = rec.ring.claim()
                if direct_slot is not None:
                    target = rec.ring.pool[direct_slot].reshape(rec.height, rec.width)
            try:
                frame = self.handle.grab_into(target, 0.1)
            except Exception as error:  # noqa: BLE001
                if _is_timeout(error):
                    continue
                self.emit({"evt": "fault", "where": "fetch",
                           "error": f"{type(error).__name__}: {error}"})
                return
            self.frames_seen += 1
            perf_ns = time.perf_counter_ns()
            self.last_frame_perf = perf_ns / 1e9
            if self.publisher is not None:
                self.publisher.maybe_publish(frame.image, frame.frame_id)
            if rec is None or self.rec is not rec or rec.complete:
                continue
            if not rec.first_frame_seen:
                rec.first_frame_seen = True
                self.emit({"evt": "armed", "serial": cfg.serial})
            if rec.gate_perf_ns is None or perf_ns < rec.gate_perf_ns:
                continue
            if rec.frames_only_count is not None:
                ordinal = rec.capture_ordinal
                rec.capture_ordinal += 1
                if rec.frames_only_count != 0:
                    target_s = (rec.next_capture_index * rec.capture_duration_s
                                / rec.frames_only_count)
                    elapsed_s = (perf_ns - rec.gate_perf_ns) / 1e9
                    if elapsed_s < target_s:
                        continue
                    rec.next_capture_index += 1
            image = frame.image.reshape(-1)
            if image.size != rec.ring.frame_bytes:
                if not rec.size_mismatch_logged:
                    rec.size_mismatch_logged = True
                    self.emit({"evt": "log", "level": "error",
                               "message": f"SIZE MISMATCH got {image.size} "
                                          f"expected {rec.ring.frame_bytes}; skipping"})
                continue
            if direct_attempted:
                if direct_slot is None:
                    continue
                slot = direct_slot
            else:
                slot = rec.ring.claim()
                if slot is None:
                    continue
                rec.ring.pool[slot] = image
            rec.ring.meta[slot, 0] = clockcal.t_host_s(perf_ns, self.clock_offset_ns,
                                                       rec.origin_posix_ns)
            rec.ring.meta[slot, 1] = float(frame.frame_id)
            rec.ring.meta[slot, 2] = float(
                rec.capture_ordinal - 1 if rec.frames_only_count is not None else 0)
            rec.ring.commit()
            depth = rec.ring.depth
            if depth == 1 or depth >= cfg.chunk_frames:
                rec.write_wake.set()

    def _writer_loop(self, rec: _Recording) -> None:
        try:
            writer = CamH5Writer(
                rec.out_path, camera_index=self.cfg.position,
                width=rec.width, height=rec.height, max_frames=rec.max_frames,
                chunk_frames=self.cfg.chunk_frames, zstd_level=self.cfg.zstd_level,
            )
        except Exception as error:  # noqa: BLE001
            rec.writer_error = f"H5 open failed: {error}"
            rec.writer_ready.set()
            return
        rec.writer_ready.set()
        last_flush = time.perf_counter()
        try:
            while True:
                rec.write_wake.wait(WRITER_WAKE_S)
                rec.write_wake.clear()
                stopping = rec.stop_writer.is_set()
                while True:
                    images, meta = rec.ring.peek(self.cfg.chunk_frames)
                    if len(images) == 0:
                        break
                    timed_out = time.perf_counter() - last_flush > TIMED_FLUSH_S
                    if len(images) < self.cfg.chunk_frames and not (stopping or timed_out):
                        break  # wait for a full chunk-aligned batch
                    written = writer.write_batch(images, meta[:, 0], meta[:, 1])
                    rec.ring.release(len(images))
                    rec.frames_written = writer.frames_written
                    if written < len(images) or writer.full:
                        if not rec.complete:
                            rec.complete = True
                            self.emit({"evt": "recording_complete",
                                       "serial": self.cfg.serial,
                                       "frames": writer.frames_written})
                        break
                now = time.perf_counter()
                if now - last_flush > TIMED_FLUSH_S:
                    writer.flush()
                    last_flush = now
                if rec.stop_writer.is_set() and (rec.ring.depth == 0 or rec.complete):
                    break
        except Exception as error:  # noqa: BLE001
            rec.writer_error = f"H5 write failed: {error}"
            self.emit({"evt": "fault", "where": "writer", "error": rec.writer_error})
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    def _png_writer_loop(self, rec: _Recording) -> None:
        destination = Path(rec.out_path)
        try:
            destination.mkdir(parents=True, exist_ok=False)
        except Exception as error:  # noqa: BLE001
            rec.writer_error = f"PNG output open failed: {error}"
            rec.writer_ready.set()
            return
        rec.writer_ready.set()
        try:
            while True:
                rec.write_wake.wait(WRITER_WAKE_S)
                rec.write_wake.clear()
                stopping = rec.stop_writer.is_set()
                while True:
                    images, meta = rec.ring.peek(self.cfg.chunk_frames)
                    if len(images) == 0:
                        break
                    for image, row in zip(images, meta, strict=True):
                        ordinal = int(row[2])
                        frame_id = int(row[1])
                        name = (f"frame{rec.frames_written:06d}_"
                                f"source{ordinal:06d}_fid{frame_id:010d}.png")
                        write_gray8_png(
                            destination / name, image.reshape(rec.height, rec.width))
                        rec.frames_written += 1
                    rec.ring.release(len(images))
                    if (rec.expected_frames is not None
                            and rec.frames_written >= rec.expected_frames):
                        rec.complete = True
                        self.emit({"evt": "recording_complete",
                                   "serial": self.cfg.serial,
                                   "frames": rec.frames_written})
                        break
                if rec.stop_writer.is_set() and (rec.ring.depth == 0 or rec.complete):
                    break
                if rec.complete:
                    break
        except Exception as error:  # noqa: BLE001
            rec.writer_error = f"PNG write failed: {error}"
            self.emit({"evt": "fault", "where": "writer", "error": rec.writer_error})

    # ---- main loop ----
    def status(self) -> dict:
        rec = self.rec
        return {
            "evt": "status",
            "serial": self.cfg.serial,
            "position": self.cfg.position,
            "acquiring": self.acquiring,
            "free_run": self.preview_free_run,
            "frames_seen": self.frames_seen,
            "recording": rec is not None and rec.gate_perf_ns is not None,
            "armed": rec is not None,
            "frames_written": rec.frames_written if rec else 0,
            "dropped": rec.ring.dropped if rec else 0,
            "ring_depth": rec.ring.depth if rec else 0,
            "complete": rec.complete if rec else False,
        }

    def handle_command(self, message: dict) -> None:
        cmd = message.get("cmd")
        try:
            if cmd == "configure":
                self.emit(self.do_configure())
            elif cmd == "preview_mode":
                self.emit(self.do_preview_mode(bool(message["free_run"])))
            elif cmd == "start_acquisition":
                self.emit(self.do_start_acquisition())
            elif cmd == "stop_acquisition":
                self.emit(self.do_stop_acquisition())
            elif cmd == "node_write":
                self.emit(self.do_node_write(message["name"], message["value"],
                                             bool(message.get("advanced"))))
            elif cmd == "node_read":
                self.emit({"evt": "read_result", "name": message["name"],
                           "value": self.handle.node_read_nocache(message["name"])})
            elif cmd == "nodemap_dump":
                self.emit({"evt": "nodemap", "dump": self.handle.nodemap_dump()})
            elif cmd == "read_many":
                self.emit(self.do_read_many(list(message["names"])))
            elif cmd == "video_mode_scaled":
                self.emit(self.do_video_mode_scaled(str(message["mode"])))
            elif cmd == "rate_mark":
                self.emit({"evt": "rate_mark", "frames_seen": self.frames_seen,
                           "perf_ns": time.perf_counter_ns()})
            elif cmd == "capture_state":
                self.emit({"evt": "capture_state", "acquiring": self.acquiring,
                           "free_run": self.preview_free_run,
                           "recording": self.rec is not None})
            elif cmd == "set_exposure_gain":
                configure_exposure_gain(self.rig, self.cfg.serial,
                                        message["exposure_us"], message["gain_db"])
                self.emit({"evt": "write_result", "name": "exposure_gain", "ok": True})
            elif cmd == "sim_trigger":
                if hasattr(self.backend, "trigger_running"):
                    self.backend.trigger_running = bool(message["running"])
                if message.get("fps") is not None and hasattr(self.backend, "fps"):
                    self.backend.fps = float(message["fps"])
                self.emit({"evt": "sim_trigger", "running": bool(message["running"])})
            elif cmd == "arm":
                self.do_arm(message["out_path"], int(message["max_frames"]),
                            float(message.get("fps_nominal", self.cfg.fps_nominal)),
                            (None if message.get("frames_only_count") is None else
                             int(message["frames_only_count"])),
                            (None if message.get("capture_duration_s") is None else
                             float(message["capture_duration_s"])))
            elif cmd == "go":
                self.do_go(int(message["gate_posix_ns"]))
            elif cmd == "stop_recording":
                self.emit(self.do_stop_recording())
            elif cmd == "shutdown":
                self.shutting_down = True
            else:
                self.emit({"evt": "error", "error": f"unknown command: {cmd}"})
        except Exception as error:  # noqa: BLE001
            self.emit({"evt": "error", "cmd": cmd,
                       "error": f"{type(error).__name__}: {error}",
                       "traceback": traceback.format_exc()})

    def run(self) -> None:
        _raise_priority()
        last_status = 0.0
        try:
            while not self.shutting_down:
                if self.conn.poll(0.05):
                    try:
                        message = self.conn.recv()
                    except (EOFError, OSError):
                        break  # GUI gone: clean stop path
                    self.handle_command(message)
                now = time.perf_counter()
                if now - last_status >= STATUS_INTERVAL_S:
                    last_status = now
                    self.emit(self.status())
                self._flush_events()
        finally:
            self.teardown()

    def teardown(self) -> None:
        try:
            if self.rec is not None:
                self.emit(self.do_stop_recording())
            self.do_stop_acquisition()
        except Exception:  # noqa: BLE001
            pass
        if self.publisher is not None:
            self.publisher.close()
            self.publisher = None
        if not self.cfg.apply_profile:
            # adopt mode: this GUI IS the settings tool — a clean disconnect
            # KEEPS every change made during the session (crash paths still
            # roll back via the marker, which only clears here).
            if self.rig is not None:
                self.rig.commit_no_restore()
            self.advanced_originals.clear()
        for name, original in reversed(list(self.advanced_originals.items())):
            try:
                self.handle.node_write(name, original)
            except Exception as error:  # noqa: BLE001
                self.emit({"evt": "log", "level": "error",
                           "message": f"advanced restore {name}: {error}"})
        if self.rig is not None:
            try:
                self.rig.__exit__(None, None, None)  # verified restore
            except Exception as error:  # noqa: BLE001
                self.emit({"evt": "log", "level": "error",
                           "message": f"restore failed: {error}"})
            self.rig = None
        if self.backend is not None:
            try:
                self.backend.close()
            except Exception:  # noqa: BLE001
                pass
            self.backend = None
        self._flush_events()
        try:
            self.conn.send({"evt": "exited", "serial": self.cfg.serial})
        except (BrokenPipeError, OSError):
            pass
        self.conn.close()


def child_main(conn, cfg: WorkerConfig) -> None:
    """multiprocessing spawn entry point."""
    logging.basicConfig(level=logging.INFO,
                        format=f"[cam{cfg.position} {cfg.serial}] %(message)s")
    Worker(conn, cfg).run()
