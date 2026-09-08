"""rig-studio entry point.

  rig-studio                 launch the GUI
  rig-studio --backend sim   GUI against the simulator (no hardware)
  rig-studio probe           detect cameras, apply profiles, read back, restore
  rig-studio restore         replay crash-marker restores
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import multiprocessing
import sys
from pathlib import Path

from rig_studio.config import RigConfig, load_config

DEFAULT_CONFIG = Path(__file__).parents[1] / "configs" / "rig.yaml"
DEFAULT_STATE_DIR = Path.home() / ".rig_studio" / "state"


def _load(args) -> RigConfig:
    config = load_config(args.config)
    if args.backend:
        config = dataclasses.replace(config, backend=args.backend)
    return config


def _gui(args) -> int:
    from PySide6.QtWidgets import QApplication

    from rig_studio.gui.main_window import MainWindow

    config = _load(args)
    app = QApplication(sys.argv[:1])
    window = MainWindow(config, DEFAULT_STATE_DIR)
    window.show()
    return app.exec()


def _make_backend(config: RigConfig):
    if config.backend == "sim":
        from rig_studio.backend.sim_backend import SimBackend

        rois = {c.serial: (c.roi.width, c.roi.height, c.roi.offset_x, c.roi.offset_y)
                for c in config.cameras}
        return SimBackend(config.serials, rois)
    if config.backend == "harvesters":
        from rig_studio.backend.harvesters_backend import HarvestersBackend

        return HarvestersBackend(config.gentl_cti, config.serials,
                                 frame_metadata_nodes=config.frame_metadata_nodes)
    if config.backend == "spinnaker":
        from rig_studio.backend.spinnaker_backend import SpinnakerBackend

        return SpinnakerBackend(config.serials)
    raise ValueError(f"unknown backend: {config.backend}")


def _probe(args) -> int:
    from rig_studio.camera.profiles import apply_recording_profile
    from rig_studio.safety.allowlist import MUTABLE_ALLOWLIST
    from rig_studio.safety.guarded import GuardedRig

    config = _load(args)
    backend = _make_backend(config)
    found = backend.enumerate()
    print(f"detected {len(found)}/{len(config.serials)} configured cameras: {found}")
    if not found:
        return 1
    rig = GuardedRig(backend, found, MUTABLE_ALLOWLIST,
                     snapshot_dir=DEFAULT_STATE_DIR / "snapshots" / "probe",
                     marker_path=DEFAULT_STATE_DIR / "markers" / "ACTIVE_probe.json",
                     exposure_tolerance_us=config.safety.exposure_tolerance_us)
    with rig:
        for serial in found:
            cam = config.by_serial(serial)
            if args.apply:
                apply_recording_profile(rig, cam, config.defaults, config.quirks)
            handle = rig.handle(serial)
            row = {name: handle.node_read_nocache(name)
                   for name in ("Width", "Height", "OffsetX", "OffsetY",
                                "ExposureTime", "Gain", "PixelFormat", "GammaEnabled")}
            print(f"cam{cam.position} {cam.label} [{serial}]: {row}")
    print("restored exactly-as-found.")
    return 0


def _restore(args) -> int:
    from rig_studio.safety.guarded import restore_from_marker

    config = _load(args)
    marker_dir = DEFAULT_STATE_DIR / "markers"
    markers = sorted(marker_dir.glob("ACTIVE_*.json")) if marker_dir.is_dir() else []
    if not markers:
        print("no crash markers found.")
        return 0
    backend = _make_backend(config)
    failures = 0
    for path in markers:
        print(f"restoring from {path} …")
        try:
            restore_from_marker(backend, json.loads(path.read_text(encoding="utf-8")), path)
            print("  ok")
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"  FAILED: {error}")
    backend.close()
    return 1 if failures else 0


def main() -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog="rig-studio")
    parser.add_argument("command", nargs="?", default="gui",
                        choices=["gui", "probe", "restore"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", choices=["spinnaker", "harvesters", "sim"],
                        default=None)
    parser.add_argument("--apply", action="store_true",
                        help="probe: apply the recording profile before reading back")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(name)s: %(message)s")
    if args.command == "probe":
        return _probe(args)
    if args.command == "restore":
        return _restore(args)
    return _gui(args)


if __name__ == "__main__":
    sys.exit(main())
