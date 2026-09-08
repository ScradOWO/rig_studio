# Vendored from racestrip/racestrip/backend/harvesters_backend.py @ 2026-08-17.
# LOCAL changes:
#   - node_read_nocache(): cache-bypassed reads (GS3 firmware serves stale values)
#   - MIN_FETCH_TIMEOUT_S floor: a timeout=0.0 fetch on an idle stream crashes
#     the Spinnaker GenTL producer natively
#   - begin_acquisition(): configure-then-verify OldestFirst instead of hard assert
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
from genicam import genapi
from harvesters.core import Harvester, ImageAcquirer

from .base import CamBackend, CamHandle, Frame, FrameGapError, Frames

MIN_FETCH_TIMEOUT_S = 0.05


class HarvestersBackend(CamBackend):
    """Spinnaker GenTL adapter; device discovery is deferred until enumerate/open."""

    def __init__(
        self,
        cti_path: str | Path,
        allowed_serials: Iterable[str],
        *,
        frame_metadata_nodes: Mapping[str, str],
        harvester_factory: Callable[[], Harvester] = Harvester,
    ) -> None:
        self._cti_path = Path(cti_path)
        if not self._cti_path.is_file():
            raise FileNotFoundError(f"GenTL producer not found: {self._cti_path}")
        self._allowed = frozenset(str(serial) for serial in allowed_serials)
        required = {"frame_id", "timestamp_ns", "exposure_us"}
        if set(frame_metadata_nodes) != required:
            raise ValueError(
                "frame_metadata_nodes must contain exactly " + ", ".join(sorted(required))
            )
        self._frame_metadata_nodes = dict(frame_metadata_nodes)
        self._factory = harvester_factory
        self._harvester: Harvester | None = None
        self._handles: list[HarvestersHandle] = []
        self._updated = False

    def _instance(self) -> Harvester:
        if self._harvester is None:
            harvester = self._factory()
            harvester.add_file(str(self._cti_path), check_existence=True, check_validity=True)
            self._harvester = harvester
        return self._harvester

    def _updated_instance(self) -> Harvester:
        # Harvester.update() DESTROYS existing ImageAcquirers — calling it per
        # open() zombified every previously opened camera. Update exactly once
        # per backend lifetime, and never while any handle is open.
        harvester = self._instance()
        if not self._updated:
            if self._handles:
                raise RuntimeError("Refusing device-list update while handles are open")
            harvester.update()
            self._updated = True
        return harvester

    def _present(self) -> set[str]:
        return {
            str(info.serial_number)
            for info in self._updated_instance().device_info_list
            if getattr(info, "serial_number", None) is not None
        }

    def enumerate(self) -> list[str]:
        return sorted(self._present() & self._allowed)

    def open(self, serial: str) -> CamHandle:
        serial = str(serial)
        if serial not in self._allowed:
            raise PermissionError(f"Serial is outside the configured rig: {serial}")
        if serial not in self._present():
            raise RuntimeError(f"Configured rig serial is missing: {serial}")
        handle = HarvestersHandle(
            serial,
            self._instance().create({"serial_number": serial}),
            self._frame_metadata_nodes,
        )
        self._handles.append(handle)
        return handle

    def close(self) -> None:
        for handle in reversed(self._handles):
            handle.close()
        self._handles.clear()
        if self._harvester is not None:
            self._harvester.reset()
            self._harvester = None
        self._updated = False


class HarvestersHandle(CamHandle):
    def __init__(
        self,
        serial: str,
        acquirer: ImageAcquirer,
        frame_metadata_nodes: Mapping[str, str],
    ) -> None:
        self.serial = serial
        self._acquirer = acquirer
        self._acquiring = False
        self._closed = False
        self._frame_metadata_nodes = dict(frame_metadata_nodes)

    def _nodemap(self, name: str) -> tuple[Any, str]:
        scope, separator, node_name = name.partition(":")
        if separator:
            if scope != "TLStream":
                raise KeyError(f"Unsupported nodemap scope: {scope}")
            streams = self._acquirer.data_streams
            if len(streams) != 1:
                raise RuntimeError(f"Expected one data stream, found {len(streams)}")
            return streams[0].node_map, node_name.strip()
        return self._acquirer.remote_device.node_map, name

    def node_read(self, name: str) -> Any:
        node_map, node_name = self._nodemap(name)
        node = node_map.get_node(node_name)
        if node is None or not genapi.is_readable(node):
            raise RuntimeError(f"Node is not readable: {name}")
        return getattr(node_map, node_name).value

    def node_read_nocache(self, name: str) -> Any:
        node_map, node_name = self._nodemap(name)
        node = node_map.get_node(node_name)
        if node is None or not genapi.is_readable(node):
            raise RuntimeError(f"Node is not readable: {name}")
        typed = getattr(node_map, node_name)
        try:
            return typed.get_value(False, True)  # verify=False, ignore_cache=True
        except (TypeError, AttributeError):
            return typed.value

    def node_write(self, name: str, value: Any) -> None:
        node_map, node_name = self._nodemap(name)
        node = node_map.get_node(node_name)
        if node is None or not genapi.is_writable(node):
            raise RuntimeError(f"Node is not writable: {name}")
        getattr(node_map, node_name).value = value

    def nodemap_dump(self) -> dict[str, dict[str, Any]]:
        result = self._dump_map(self._acquirer.remote_device.node_map, "")
        for stream in self._acquirer.data_streams:
            result.update(self._dump_map(stream.node_map, "TLStream:"))
        return result

    @staticmethod
    def _dump_map(node_map: Any, prefix: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in node_map.nodes:
            # Typed interfaces (ICategory, IInteger, ...) wrap the raw node;
            # generic metadata (name/access/type) lives on `.node`. Spinnaker's
            # producer exposes malformed stream nodes whose metadata calls can
            # throw GenICam exceptions — the forensic dump must survive them.
            info = getattr(item, "node", item)
            try:
                name = str(info.name)
            except Exception:  # noqa: BLE001
                continue
            entry: dict[str, Any] = {}
            try:
                entry["access"] = str(info.get_access_mode()).rsplit(".", 1)[-1]
                entry["type"] = str(info.principal_interface_type).rsplit(".", 1)[-1]
                for attribute in ("min", "max", "inc"):
                    try:
                        metadata_value = getattr(item, attribute)
                    except Exception:  # noqa: BLE001 — not every node is numeric
                        continue
                    if isinstance(metadata_value, (int, float)):
                        entry[attribute] = metadata_value
                if genapi.is_readable(item):
                    value = getattr(node_map, name).value
                    if not isinstance(value, (str, int, float, bool, type(None))):
                        value = repr(value)  # keep the snapshot JSON-serializable
                    entry["value"] = value
            except Exception as error:  # noqa: BLE001
                entry.setdefault("access", "ERROR")
                entry["error"] = str(error)
            result[prefix + name] = entry
        return result

    def begin_acquisition(self, buffers: int) -> None:
        if self._acquiring:
            raise RuntimeError("Acquisition already active")
        if buffers < self._acquirer.min_num_buffers:
            raise ValueError(
                f"buffers must be >= producer minimum {self._acquirer.min_num_buffers}"
            )
        if self.node_read("TLStream:StreamBufferHandlingMode") != "OldestFirst":
            self.node_write("TLStream:StreamBufferHandlingMode", "OldestFirst")
        mode = self.node_read("TLStream:StreamBufferHandlingMode")
        if mode != "OldestFirst":
            raise RuntimeError(f"Recording requires OldestFirst, found {mode}")
        self._acquirer.num_buffers = buffers
        self._acquirer.start(run_as_thread=False)
        self._acquiring = True

    def grab(self, n: int, timeout_s: float) -> Frames:
        if not self._acquiring:
            raise RuntimeError("Acquisition is not active")
        if n < 1 or timeout_s <= 0:
            raise ValueError("n and timeout_s must be positive")
        frames = tuple(self._fetch_one(timeout_s) for _ in range(n))
        frame_ids = [frame.frame_id for frame in frames]
        if any(current != previous + 1 for previous, current in zip(frame_ids, frame_ids[1:])):
            raise FrameGapError(f"Non-contiguous frames for {self.serial}: {frame_ids}")
        return frames

    def discard_pending(self, timeout_s: float) -> int:
        if not self._acquiring:
            raise RuntimeError("Acquisition is not active")
        stream = self._acquirer.data_streams[0].module
        count = int(stream.num_awaiting_delivery)
        for _ in range(count):
            self._fetch_one(timeout_s)
        return count

    def fetch_raw(self, timeout_s: float):
        """Context-managed raw buffer fetch for the recorder's zero-extra-copy path."""
        if not self._acquiring:
            raise RuntimeError("Acquisition is not active")
        return self._acquirer.fetch(timeout=max(float(timeout_s), MIN_FETCH_TIMEOUT_S))

    @property
    def chunk_node_map(self) -> Any:
        return self._acquirer.remote_device.node_map

    def _fetch_one(self, timeout_s: float) -> Frame:
        timeout_s = max(float(timeout_s), MIN_FETCH_TIMEOUT_S)
        with self._acquirer.fetch(timeout=timeout_s) as buffer:
            components = buffer.payload.components
            if len(components) != 1:
                raise RuntimeError(f"Expected one image component, found {len(components)}")
            component = components[0]
            image = np.asarray(component.data).reshape(component.height, component.width).copy()
            # Harvesters' Buffer wrapper has no node map. Its automatic chunk
            # adapter updates the remote-device node map before fetch returns.
            chunk_map = self._acquirer.remote_device.node_map
            return Frame(
                image=image,
                frame_id=self._chunk_value(
                    chunk_map, self._frame_metadata_nodes["frame_id"], int
                ),
                timestamp_ns=self._chunk_value(
                    chunk_map, self._frame_metadata_nodes["timestamp_ns"], int
                ),
                exposure_us=self._chunk_value(
                    chunk_map, self._frame_metadata_nodes["exposure_us"], float
                ),
            )

    @staticmethod
    def _chunk_value(node_map: Any, name: str, value_type: type) -> Any:
        if node_map is None:
            raise RuntimeError("Harvesters remote-device node map is unavailable")
        node = node_map.get_node(name)
        if node is None or not genapi.is_readable(node):
            raise RuntimeError(f"Required chunk node is unavailable: {name}")
        return value_type(getattr(node_map, name).value)

    def end_acquisition(self) -> None:
        if self._acquiring:
            self._acquirer.stop()
            self._acquiring = False

    def close(self) -> None:
        if self._closed:
            return
        self.end_acquisition()
        self._acquirer.destroy()
        self._closed = True
