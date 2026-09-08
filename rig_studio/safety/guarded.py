# Vendored from racestrip/racestrip/safety/guarded.py @ 2026-08-17.
# LOCAL: imports repointed to rig_studio; CLI hints renamed to `rig-studio restore`.
from __future__ import annotations

import atexit
import math
import os
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

from rig_studio.backend.base import CamBackend, CamHandle
from rig_studio.safety.atomic import write_json


class GuardViolation(RuntimeError):
    """A write outside the allowlist or a failed readback verification."""


class RestoreError(RuntimeError):
    """Restore could not be verified; the crash marker is kept."""


def configure_frame_chunks(
    rig: "GuardedRig", serials: list[str], config: dict[str, Any]
) -> None:
    """Enable required per-frame chunks through guarded selector-bank writes."""
    backend = config["backend"]
    controls = backend["chunk_control_nodes"]
    selectors = backend["frame_metadata_selectors"]
    metadata = backend["frame_metadata_nodes"]
    if set(selectors) != set(metadata):
        raise ValueError("frame metadata selector/node keys must match")
    for serial in serials:
        rig.set(serial, controls["active"], True)
        for key in metadata:
            rig.set_selected(
                serial,
                controls["selector"],
                selectors[key],
                controls["enable"],
                True,
            )


# Stop acquisition first; PixelFormat/Binning/ROI before exposure.
# LOCAL: GammaEnabled (the gate) must restore BEFORE Gamma (the value) — the
# value node is read-only while the gate is off (cam4 marker bug).
_RESTORE_FIRST = (
    "PixelFormat",
    "BinningHorizontal",
    "BinningVertical",
    "Width",
    "Height",
    "OffsetX",
    "OffsetY",
    "GammaEnabled",
    "Gamma",
)
# LOCAL: AcquisitionFrameRateEnabled restores after ExposureTime — the restore
# path force-lowers the gate before the exposure write to lift the GS3 ceiling.
# Trigger chain restores at the very end (TriggerMode last): while TriggerMode
# is On, the frame-rate node group is NA on the GS3, so restore begins by
# disarming both selectors and re-arms only here.
_RESTORE_LAST = ("ExposureAuto", "ExposureTime", "AcquisitionFrameRateEnabled",
                 "TriggerActivation", "TriggerSource", "TriggerMode")

_TRIGGER_DISARM_SELECTORS = ("FrameStart", "ExposureActive")


def _disarm_triggers(handle: CamHandle) -> None:
    """Best-effort TriggerMode Off on both selectors so gated nodes unlock."""
    try:
        original = handle.node_read("TriggerSelector")
    except Exception:  # noqa: BLE001 — no trigger selector on this device/fake
        return
    for selector in _TRIGGER_DISARM_SELECTORS:
        try:
            handle.node_write("TriggerSelector", selector)
            handle.node_write("TriggerMode", "Off")
        except Exception:  # noqa: BLE001
            pass
    try:
        handle.node_write("TriggerSelector", original)
    except Exception:  # noqa: BLE001
        pass


def _restore_order(names: list[str]) -> list[str]:
    def key(name: str) -> tuple[int, str]:
        if name in _RESTORE_FIRST:
            return (0, str(_RESTORE_FIRST.index(name)))
        if name in _RESTORE_LAST:
            return (2, str(_RESTORE_LAST.index(name)))
        return (1, name)

    return sorted(names, key=key)


# LOCAL: the GS3 quantizes more than ExposureTime — Gain readback of a 9.8 dB
# write is 9.8272 dB. Per-node absolute tolerances for verified writes/restores.
_FLOAT_ABS_TOL = {"Gain": 0.05, "Gamma": 0.02, "BlackLevel": 0.25, "TriggerDelay": 5.0}


def _values_equal(name: str, expected: Any, actual: Any, exposure_tolerance_us: float) -> bool:
    if isinstance(expected, float) or isinstance(actual, float):
        tolerance = (exposure_tolerance_us if name == "ExposureTime"
                     else _FLOAT_ABS_TOL.get(name, 1e-6))
        return math.isclose(float(expected), float(actual), rel_tol=0.0, abs_tol=tolerance)
    return expected == actual


# LOCAL: GenICam EAccessMode — dumps carry either names or numeric codes
# (2=WO, 4=RW). A node that was NOT writable at snapshot time (e.g. Gamma while
# GammaEnabled=False is RO=3) can never have been changed by us, and writing it
# back fails — exclude it from restore/diff targets.
_WRITABLE_ACCESS = {"RW", "WO", "2", "4"}


def _snapshot_writable(entry: dict[str, Any]) -> bool:
    access = entry.get("access")
    return access is None or str(access) in _WRITABLE_ACCESS


def _allowlisted_snapshot_values(
    snapshot: dict[str, dict[str, Any]], allowlist: frozenset[str]
) -> dict[str, Any]:
    return {
        name: entry["value"]
        for name, entry in snapshot.items()
        if name in allowlist and "value" in entry and entry["value"] is not None
        and _snapshot_writable(entry)
    }


def diff_against_snapshot(
    current: dict[str, dict[str, Any]],
    snapshot: dict[str, dict[str, Any]],
    allowlist: frozenset[str],
    *,
    exposure_tolerance_us: float,
) -> dict[str, dict[str, Any]]:
    """Allowlisted differences between two nodemap dumps; empty = exactly-as-found."""
    targets = _allowlisted_snapshot_values(snapshot, allowlist)
    diff: dict[str, dict[str, Any]] = {}
    for name, expected in targets.items():
        actual = current.get(name, {}).get("value")
        if not _values_equal(name, expected, actual, exposure_tolerance_us):
            diff[name] = {"snapshot": expected, "current": actual}
    return diff


def restore_handle(
    handle: CamHandle,
    snapshot: dict[str, dict[str, Any]],
    allowlist: frozenset[str],
    *,
    exposure_tolerance_us: float,
) -> list[str]:
    """Restore every allowlisted snapshot node; return verification failures."""
    handle.end_acquisition()
    _disarm_triggers(handle)  # unlock trigger-gated nodes; trigger restores last
    targets = _allowlisted_snapshot_values(snapshot, allowlist)
    failures: list[str] = []
    restored_geometry: set[str] = set()
    geometry_names = {
        "PixelFormat",
        "BinningHorizontal",
        "BinningVertical",
        "BinningCols_Int",
        "BinningRows_Int",
        "Width",
        "Height",
        "OffsetX",
        "OffsetY",
    }
    if {"Width", "Height", "OffsetX", "OffsetY"} <= targets.keys():
        try:
            node_map = handle.nodemap_dump()
            width_min = node_map["Width"]["min"]
            height_min = node_map["Height"]["min"]
            offset_x_min = node_map["OffsetX"]["min"]
            offset_y_min = node_map["OffsetY"]["min"]
            # Make the ROI valid for either binning state, restore format and
            # binning, then rebuild the exact snapshot ROI from zero offset.
            horizontal_control = (
                "BinningCols_Int" if "BinningCols_Int" in targets else "BinningHorizontal"
            )
            vertical_control = (
                "BinningRows_Int" if "BinningRows_Int" in targets else "BinningVertical"
            )
            for name, value in (
                ("Width", width_min),
                ("Height", height_min),
                ("OffsetX", offset_x_min),
                ("OffsetY", offset_y_min),
                ("PixelFormat", targets.get("PixelFormat")),
                (vertical_control, targets.get(vertical_control)),
                # GS3 row control may re-couple both axes; columns must be last
                # to reconstruct an asymmetric 1x2 snapshot.
                (horizontal_control, targets.get(horizontal_control)),
                ("Width", targets["Width"]),
                ("Height", targets["Height"]),
                ("OffsetX", targets["OffsetX"]),
                ("OffsetY", targets["OffsetY"]),
            ):
                if value is not None and not _values_equal(
                    name,
                    value,
                    handle.node_read(name),
                    exposure_tolerance_us,
                ):
                    handle.node_write(name, value)
            for name in geometry_names & targets.keys():
                actual = handle.node_read(name)
                if not _values_equal(name, targets[name], actual, exposure_tolerance_us):
                    failures.append(
                        f"{name}: wrote {targets[name]!r}, read back {actual!r}"
                    )
            restored_geometry = geometry_names & targets.keys()
        except (KeyError, TypeError):
            # Older/fake nodemaps without numeric metadata use the legacy
            # per-node path; real dumps must expose min/max/inc metadata.
            restored_geometry.clear()
        except Exception as error:  # noqa: BLE001
            failures.append(f"ROI/format restore transaction: {error}")
            restored_geometry = geometry_names & targets.keys()
    for name in _restore_order([name for name in targets if name not in restored_geometry]):
        expected = targets[name]
        if name == "ExposureTime":
            # LOCAL: a live frame-rate gate caps ExposureTime max (GS3); lift it
            # before restoring the value. The gate itself, if it was writable in
            # the snapshot, is restored by its own entry.
            try:
                handle.node_write("AcquisitionFrameRateEnabled", False)
            except Exception:  # noqa: BLE001 — gate absent or NA under trigger
                pass
        try:
            current = handle.node_read(name)
            if _values_equal(name, expected, current, exposure_tolerance_us):
                continue
            handle.node_write(name, expected)
            readback = handle.node_read(name)
            if not _values_equal(name, expected, readback, exposure_tolerance_us):
                failures.append(f"{name}: wrote {expected!r}, read back {readback!r}")
        except Exception as error:  # noqa: BLE001 — restore must attempt every node
            # LOCAL: GS3 nodes gate each other in BOTH directions (Gamma needs
            # GammaEnabled on; TriggerOverlap needs TriggerMode ON; the rate
            # group needs it OFF). A node inaccessible at restore time cannot be
            # restored or verified — its value follows the gate nodes we do
            # restore — so it is skipped, not failed.
            text = str(error)
            if "not readable" in text or "not writable" in text:
                continue
            failures.append(f"{name}: {error}")
    return failures


def restore_selected_states(
    handle: CamHandle,
    states: list[dict[str, Any]],
    *,
    exposure_tolerance_us: float,
) -> list[str]:
    """Restore selector-dependent values captured before guarded writes."""
    failures: list[str] = []
    handle.end_acquisition()
    for state in reversed(states):
        selector_node = state["selector_node"]
        value_node = state["value_node"]
        try:
            handle.node_write(selector_node, state["selector_value"])
            handle.node_write(value_node, state["original_value"])
            actual = handle.node_read(value_node)
            if not _values_equal(
                value_node,
                state["original_value"],
                actual,
                exposure_tolerance_us,
            ):
                failures.append(
                    f"{selector_node}={state['selector_value']}:{value_node}: "
                    f"wrote {state['original_value']!r}, read back {actual!r}"
                )
        except Exception as error:  # noqa: BLE001 — restore must attempt every bank
            failures.append(
                f"{selector_node}={state['selector_value']}:{value_node}: {error}"
            )
        finally:
            try:
                handle.node_write(selector_node, state["original_selector"])
            except Exception as error:  # noqa: BLE001
                failures.append(f"{selector_node}: restore selector: {error}")
    return failures


class GuardedRig:
    """Snapshot → allowlisted-write → verify → guaranteed-restore."""

    def __init__(
        self,
        backend: CamBackend,
        serials: list[str],
        allowlist: frozenset[str] | set[str],
        *,
        snapshot_dir: str | Path,
        marker_path: str | Path,
        exposure_tolerance_us: float,
    ) -> None:
        self._backend = backend
        self._serials = [str(serial) for serial in serials]
        self._allowlist = frozenset(allowlist)
        self._snapshot_dir = Path(snapshot_dir)
        self._marker_path = Path(marker_path)
        self._tolerance = float(exposure_tolerance_us)
        self._handles: dict[str, CamHandle] = {}
        self._snapshots: dict[str, dict[str, dict[str, Any]]] = {}
        self.write_log: list[dict[str, Any]] = []
        self._selected_states: dict[str, list[dict[str, Any]]] = {
            serial: [] for serial in self._serials
        }
        self._marker_payload: dict[str, Any] = {}
        self._restored = False
        self._previous_sigint: Any = None

    def __enter__(self) -> "GuardedRig":
        if self._marker_path.exists():
            raise RestoreError(
                f"Crash marker already present: {self._marker_path}. "
                "Run `rig-studio restore --from-marker` before opening a new session."
            )
        snapshot_paths: dict[str, str] = {}
        for serial in self._serials:
            handle = self._backend.open(serial)
            self._handles[serial] = handle
            snapshot = handle.nodemap_dump()
            self._snapshots[serial] = snapshot
            path = self._snapshot_dir / f"{serial}.json"
            write_json(path, snapshot)
            snapshot_paths[serial] = str(path)
        self._marker_payload = {
            "schema_version": 1,
            "pid": os.getpid(),
            "timestamp": datetime.now().isoformat(),
            "allowlist": sorted(self._allowlist),
            "exposure_verify_tolerance_us": self._tolerance,
            "snapshots": snapshot_paths,
            "selected_states": self._selected_states,
        }
        write_json(self._marker_path, self._marker_payload)
        self._previous_sigint = signal.signal(signal.SIGINT, self._on_sigint)
        atexit.register(self._atexit_restore)
        return self

    def _on_sigint(self, signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    def _atexit_restore(self) -> None:
        if not self._restored:
            try:
                self._restore()
            except RestoreError:
                pass

    def handle(self, serial: str) -> CamHandle:
        return self._handles[str(serial)]

    def snapshot(self, serial: str) -> dict[str, dict[str, Any]]:
        return self._snapshots[str(serial)]

    def set(self, serial: str, name: str, value: Any) -> None:
        if name not in self._allowlist:
            raise GuardViolation(f"Node is not allowlisted: {name}")
        handle = self._handles[str(serial)]
        handle.node_write(name, value)
        readback = handle.node_read(name)
        if not _values_equal(name, value, readback, self._tolerance):
            raise GuardViolation(
                f"Readback mismatch on {serial}:{name}: wrote {value!r}, read {readback!r}"
            )
        self.write_log.append({"serial": str(serial), "name": name, "value": value})

    def set_selected(
        self,
        serial: str,
        selector_node: str,
        selector_value: Any,
        value_node: str,
        value: Any,
    ) -> None:
        """Guard a selector-bank write and persist enough state for crash restore."""
        serial = str(serial)
        if selector_node not in self._allowlist or value_node not in self._allowlist:
            raise GuardViolation(
                f"Selector write nodes must be allowlisted: {selector_node}, {value_node}"
            )
        handle = self._handles[serial]
        original_selector = handle.node_read(selector_node)
        states = self._selected_states[serial]
        existing = next(
            (
                state
                for state in states
                if state["selector_node"] == selector_node
                and state["selector_value"] == selector_value
                and state["value_node"] == value_node
            ),
            None,
        )
        try:
            self.set(serial, selector_node, selector_value)
            if existing is None:
                states.append(
                    {
                        "selector_node": selector_node,
                        "selector_value": selector_value,
                        "value_node": value_node,
                        "original_value": handle.node_read(value_node),
                        "original_selector": original_selector,
                    }
                )
                write_json(self._marker_path, self._marker_payload)
            self.set(serial, value_node, value)
        finally:
            self.set(serial, selector_node, original_selector)

    def allowlisted_diff(self, serial: str) -> dict[str, dict[str, Any]]:
        """Current allowlisted values vs snapshot; empty dict = exactly-as-found."""
        handle = self._handles[str(serial)]
        targets = _allowlisted_snapshot_values(self._snapshots[str(serial)], self._allowlist)
        diff: dict[str, dict[str, Any]] = {}
        for name, expected in targets.items():
            current = handle.node_read(name)
            if not _values_equal(name, expected, current, self._tolerance):
                diff[name] = {"snapshot": expected, "current": current}
        return diff

    def recommended_deviations(self, serial: str, recommended: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Snapshot values differing from the recommended profile — reported, never changed."""
        snapshot = _allowlisted_snapshot_values(self._snapshots[str(serial)], self._allowlist)
        return {
            name: {"snapshot": snapshot[name], "recommended": value}
            for name, value in recommended.items()
            if name in snapshot
            and not _values_equal(name, value, snapshot[name], self._tolerance)
        }

    def commit_no_restore(self) -> None:
        """LOCAL: adopt-and-keep semantics for the settings GUI — a clean close
        keeps every change made during the session (this IS the settings tool);
        the crash marker is removed. Crash paths never reach here, so a dirty
        exit still restores the connect-time snapshot via the marker."""
        self._restored = True
        self._marker_path.unlink(missing_ok=True)

    def _restore(self) -> None:
        failures: list[str] = []
        for serial, handle in self._handles.items():
            failures.extend(
                f"{serial}:{failure}"
                for failure in restore_selected_states(
                    handle,
                    self._selected_states[serial],
                    exposure_tolerance_us=self._tolerance,
                )
            )
            failures.extend(
                f"{serial}:{failure}"
                for failure in restore_handle(
                    handle,
                    self._snapshots[serial],
                    self._allowlist,
                    exposure_tolerance_us=self._tolerance,
                )
            )
        if failures:
            raise RestoreError(
                "Restore verification failed; crash marker kept at "
                f"{self._marker_path}. Run `rig-studio restore --from-marker` "
                "after resolving:\n" + "\n".join(failures)
            )
        self._restored = True
        self._marker_path.unlink(missing_ok=True)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._previous_sigint is not None:
            signal.signal(signal.SIGINT, self._previous_sigint)
            self._previous_sigint = None
        atexit.unregister(self._atexit_restore)
        try:
            if not self._restored:
                self._restore()
        finally:
            for handle in self._handles.values():
                handle.close()


def restore_from_marker(
    backend: CamBackend,
    marker: dict[str, Any],
    marker_path: str | Path,
    *,
    recovery_allowlist: frozenset[str] | None = None,
) -> None:
    """Replay restore from a crash marker's snapshots, even after a reboot."""
    import json

    allowlist = frozenset(marker["allowlist"]) | (recovery_allowlist or frozenset())
    tolerance = float(marker["exposure_verify_tolerance_us"])
    failures: list[str] = []
    for serial, snapshot_path in marker["snapshots"].items():
        snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
        handle = backend.open(serial)
        try:
            failures.extend(
                f"{serial}:{failure}"
                for failure in restore_selected_states(
                    handle,
                    marker.get("selected_states", {}).get(serial, []),
                    exposure_tolerance_us=tolerance,
                )
            )
            failures.extend(
                f"{serial}:{failure}"
                for failure in restore_handle(
                    handle, snapshot, allowlist, exposure_tolerance_us=tolerance
                )
            )
        finally:
            handle.close()
    if failures:
        raise RestoreError(
            "Marker restore verification failed:\n" + "\n".join(failures)
        )
    Path(marker_path).unlink(missing_ok=True)
