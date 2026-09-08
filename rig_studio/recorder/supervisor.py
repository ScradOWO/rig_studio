"""GUI-side supervisor: spawns one worker process per camera, routes commands,
aggregates events, and runs the session state machine (configure -> arm-all ->
absolute-POSIX gate broadcast -> done collection)."""
from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time
from collections import deque
from pathlib import Path

from rig_studio.config import RigConfig
from rig_studio.recorder.previewbus import shm_name
from rig_studio.recorder.worker import WorkerConfig, child_main
from rig_studio.recording.session import max_frames, output_path
from rig_studio.safety.allowlist import MUTABLE_ALLOWLIST

log = logging.getLogger(__name__)

ARM_TIMEOUT_S = 5.0        # MEX start gate was 2 s; workers also open files here
CONFIGURE_TIMEOUT_S = 30.0


class WorkerDied(RuntimeError):
    pass


class _WorkerHandle:
    def __init__(self, position: int, serial: str, process: mp.Process, conn) -> None:
        self.position = position
        self.serial = serial
        self.process = process
        self.conn = conn
        self.last_status: dict = {}
        self.exited = False


class RecorderSupervisor:
    def __init__(self, config: RigConfig, *, backend_kind: str | None = None,
                 state_dir: str | Path = "state") -> None:
        self.config = config
        self.backend_kind = backend_kind or config.backend
        self.state_dir = Path(state_dir)
        self.workers: dict[int, _WorkerHandle] = {}
        self.events: deque[dict] = deque()
        self._ctx = mp.get_context("spawn")
        self._io_lock = threading.RLock()

    # ---- lifecycle ----
    def start(self, positions: list[int] | set[int] | tuple[int, ...] | None = None) -> None:
        rec = self.config.recording
        available = {cam.position for cam in self.config.cameras}
        selected = set(available) if positions is None else {
            int(position) for position in positions
        }
        unknown = selected - available
        if unknown:
            raise ValueError(f"unknown camera positions: {sorted(unknown)}")
        if not selected:
            raise ValueError("at least one camera must be selected")
        for cam in sorted(self.config.cameras, key=lambda c: c.position):
            if cam.position not in selected:
                continue
            cfg = WorkerConfig(
                serial=cam.serial,
                position=cam.position,
                label=cam.label,
                backend_kind=self.backend_kind,
                cti=self.config.gentl_cti,
                frame_metadata_nodes=self.config.frame_metadata_nodes,
                roi=(cam.roi.width, cam.roi.height, cam.roi.offset_x, cam.roi.offset_y),
                video_mode=cam.video_mode,
                defaults=self.config.defaults,
                quirks=self.config.quirks,
                allowlist=MUTABLE_ALLOWLIST,
                snapshot_dir=str(self.state_dir / "snapshots" / cam.serial),
                marker_path=str(self.state_dir / "markers" / f"ACTIVE_{cam.serial}.json"),
                exposure_tolerance_us=self.config.safety.exposure_tolerance_us,
                pool_seconds=rec.pool_seconds,
                fps_nominal=rec.fps_nominal,
                queue_cap=rec.queue_cap,
                chunk_frames=rec.chunk_frames,
                drop_log_every=rec.drop_log_every,
                zstd_level=rec.compression.zstd_level if rec.compression.enabled else None,
                shm_name=shm_name(self.config.name, cam.position),
                preview_decimate=self.config.gui.preview_decimate,
                preview_min_interval_s=1.0 / self.config.gui.preview_hz,
                apply_profile=self.config.apply_profile_on_connect,
            )
            parent_conn, child_conn = self._ctx.Pipe(duplex=True)
            process = self._ctx.Process(target=child_main, args=(child_conn, cfg),
                                        name=f"cam{cam.position}", daemon=False)
            process.start()
            child_conn.close()
            self.workers[cam.position] = _WorkerHandle(cam.position, cam.serial,
                                                       process, parent_conn)

    def shutdown(self, timeout_s: float = 60.0) -> None:
        # GenTL buffer revocation + verified restore can take several seconds
        # per camera; a hard terminate skips restore and leaves crash markers.
        self.broadcast({"cmd": "shutdown"})
        deadline = time.monotonic() + timeout_s
        for worker in self.workers.values():
            budget = max(10.0, deadline - time.monotonic())
            worker.process.join(timeout=budget)
            if worker.process.is_alive():
                log.error("worker cam%d did not exit; terminating", worker.position)
                worker.process.terminate()
        self.workers.clear()

    # ---- messaging ----
    def send(self, position: int, message: dict) -> None:
        worker = self.workers[position]
        try:
            worker.conn.send(message)
        except (BrokenPipeError, OSError) as error:
            raise WorkerDied(f"worker cam{position} pipe closed") from error

    def broadcast(self, message: dict, positions: set[int] | None = None) -> None:
        targets = set(self.workers) if positions is None else set(positions)
        unknown = targets - set(self.workers)
        if unknown:
            raise KeyError(f"camera workers are not active: {sorted(unknown)}")
        for position in sorted(targets):
            try:
                self.send(position, message)
            except WorkerDied:
                pass

    def poll_events(self) -> list[dict]:
        """Drain all pending worker events (non-blocking); also feeds self.events."""
        with self._io_lock:
            fresh: list[dict] = []
            for worker in self.workers.values():
                while True:
                    try:
                        if not worker.conn.poll(0):
                            break
                        event = worker.conn.recv()
                    except (EOFError, OSError):
                        if not worker.exited:
                            worker.exited = True
                            fresh.append({"evt": "worker_lost", "position": worker.position,
                                          "serial": worker.serial})
                        break
                    event.setdefault("position", worker.position)
                    if event.get("evt") == "status":
                        worker.last_status = event
                    if event.get("evt") == "exited":
                        worker.exited = True
                    fresh.append(event)
            self.events.extend(fresh)
            return fresh

    def take_events(self) -> list[dict]:
        if not self._io_lock.acquire(blocking=False):
            return []
        try:
            self.poll_events()
            drained = list(self.events)
            self.events.clear()
            return drained
        finally:
            self._io_lock.release()

    def wait_for(self, evt: str, positions: set[int], timeout_s: float) -> dict[int, dict]:
        """Block until every position reported `evt` (or errored); raises on timeout."""
        got: dict[int, dict] = {}
        deadline = time.monotonic() + timeout_s
        with self._io_lock:
            while time.monotonic() < deadline:
                for event in self.poll_events():
                    position = event.get("position")
                    if event.get("evt") == evt and position in positions:
                        got[position] = event
                    elif event.get("evt") in ("error", "fault") and position in positions:
                        raise RuntimeError(f"cam{position}: {event.get('error')}")
                if set(got) >= positions:
                    return got
                time.sleep(0.01)
        missing = sorted(positions - set(got))
        raise TimeoutError(f"no '{evt}' from cam{missing} within {timeout_s}s")

    # ---- high-level operations ----
    def configure_all(self) -> dict[int, dict]:
        self.broadcast({"cmd": "configure"})
        return self.wait_for("configured", set(self.workers), CONFIGURE_TIMEOUT_S)

    def set_preview_mode(self, free_run: bool) -> None:
        self.broadcast({"cmd": "preview_mode", "free_run": free_run})
        self.wait_for("preview_mode", set(self.workers), 10.0)

    def start_preview(self) -> None:
        self.broadcast({"cmd": "start_acquisition"})

    def stop_preview(self) -> None:
        self.broadcast({"cmd": "stop_acquisition"})

    def set_sim_trigger(self, running: bool, fps: float | None = None) -> None:
        message = {"cmd": "sim_trigger", "running": running}
        if fps is not None:
            message["fps"] = float(fps)
        self.broadcast(message)

    def arm_all(self, base: str, duration_s: float, *, wait: bool = True,
                positions: set[int] | None = None,
                fps_nominal: float | None = None,
                frames_only_count: int | None = None) -> dict[int, str]:
        """Open files + start acquisition on selected workers; returns output paths.
        With wait=True, blocks until every camera has seen >= 1 frame (the MEX
        start gate) — that requires the trigger clock to already be running."""
        rec = self.config.recording
        fps_nominal = rec.fps_nominal if fps_nominal is None else float(fps_nominal)
        frames = max_frames(duration_s, fps_nominal, rec.max_frames_overhead)
        paths: dict[int, str] = {}
        targets = set(self.workers) if positions is None else set(positions)
        if not targets:
            raise ValueError("at least one recording camera must be selected")
        unknown = targets - set(self.workers)
        if unknown:
            raise ValueError(f"recording cameras are not active: {sorted(unknown)}")
        for position in sorted(targets):
            cam = self.config.by_position(position)
            path = output_path(cam.output_root, rec.filename_template, base, position)
            if frames_only_count is not None:
                path = path.with_name(f"{path.stem}_png")
            paths[position] = str(path)
            self.send(position, {"cmd": "arm", "out_path": str(path),
                                 "max_frames": frames, "fps_nominal": fps_nominal,
                                 "frames_only_count": frames_only_count,
                                 "capture_duration_s": float(duration_s)})
        if wait:
            self.wait_armed(positions=targets)
        return paths

    def wait_armed(self, timeout_s: float = ARM_TIMEOUT_S,
                   positions: set[int] | None = None) -> None:
        """Start gate: every armed camera must produce >= 1 frame."""
        targets = set(self.workers) if positions is None else set(positions)
        self.wait_for("armed", targets, timeout_s)

    def open_gate(self, gate_posix_ns: int | None = None,
                  positions: set[int] | None = None) -> int:
        """Broadcast the absolute-POSIX write gate; returns the gate time used."""
        if gate_posix_ns is None:
            gate_posix_ns = time.time_ns() + int(self.config.recording.gate_delay_s * 1e9)
        self.broadcast({"cmd": "go", "gate_posix_ns": int(gate_posix_ns)}, positions)
        return gate_posix_ns

    def stop_recording_all(self, timeout_s: float = 60.0,
                           positions: set[int] | None = None) -> dict[int, dict]:
        targets = set(self.workers) if positions is None else set(positions)
        self.broadcast({"cmd": "stop_recording"}, targets)
        done = self.wait_for("done", targets, timeout_s)
        return {position: event.get("report") for position, event in done.items()}

    def all_complete(self, positions: set[int] | None = None) -> bool:
        self.poll_events()
        targets = set(self.workers) if positions is None else set(positions)
        statuses = [self.workers[position].last_status for position in targets
                    if self.workers[position].last_status]
        return len(statuses) == len(targets) and all(
            s.get("complete") for s in statuses)

    def measure_rates(self, duration_s: float = 3.0,
                      positions: set[int] | None = None) -> dict[int, float]:
        """Measure delivered simultaneous frame rates without creating files."""
        targets = set(self.workers) if positions is None else set(positions)
        if not targets:
            raise ValueError("at least one camera is required for a rate test")
        self.broadcast({"cmd": "rate_mark"}, targets)
        start = self.wait_for("rate_mark", targets, 5.0)
        time.sleep(max(0.25, float(duration_s)))
        self.broadcast({"cmd": "rate_mark"}, targets)
        end = self.wait_for("rate_mark", targets, 5.0)
        result: dict[int, float] = {}
        for position in sorted(targets):
            elapsed = (end[position]["perf_ns"] - start[position]["perf_ns"]) / 1e9
            frames = end[position]["frames_seen"] - start[position]["frames_seen"]
            result[position] = frames / elapsed if elapsed > 0 else 0.0
        return result
