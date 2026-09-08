"""Find the maximum concurrent hardware-triggered rate for all six cameras at
their CURRENT (frozen) settings. Drives the Digilent clock through a staircase
of rates; at each rate, all cameras stream simultaneously while we count
delivered frames. A camera that can't keep up shows a delivered rate below the
commanded one.

    python bench_rate.py                      # default staircase 20..90 Hz
    python bench_rate.py --rates 20 40 60 80 --seconds 6

Requirements: rig-studio GUI disconnected (cameras free), WaveForms closed
(the script needs to own the Digilent). Camera settings are NOT modified.
"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from rig_studio.backend.harvesters_backend import HarvestersBackend  # noqa: E402
from rig_studio.config import load_config  # noqa: E402
from rig_studio.trigger.digilent import DigilentClock  # noqa: E402

CONFIG = Path(__file__).parents[1] / "configs" / "rig.yaml"
SETTLE_S = 1.5
TOLERANCE = 0.98  # delivered/commanded ratio to count as "keeping up"


class Counter(threading.Thread):
    def __init__(self, handle, label: str) -> None:
        super().__init__(daemon=True)
        self.handle = handle
        self.label = label
        self.count = 0
        self.timeouts = 0
        self.counting = False
        self.stop_flag = False

    def run(self) -> None:
        while not self.stop_flag:
            try:
                with self.handle.fetch_raw(0.25):
                    pass
                if self.counting:
                    self.count += 1
            except Exception:  # noqa: BLE001 — timeout between edges
                if self.counting:
                    self.timeouts += 1

    def reset(self) -> None:
        self.count = 0
        self.timeouts = 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rates", type=float, nargs="+",
                        default=[20, 30, 40, 50, 60, 70, 80, 90])
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    config = load_config(CONFIG)
    backend = HarvestersBackend(config.gentl_cti, config.serials,
                                frame_metadata_nodes=config.frame_metadata_nodes)
    present = backend.enumerate()
    missing = set(config.serials) - set(present)
    if missing:
        print(f"cameras busy or missing: {sorted(missing)} — disconnect the "
              "rig-studio GUI first.")
        return 1
    clock = DigilentClock(config.trigger)
    try:
        clock.open()
    except RuntimeError as error:
        print(f"cannot own the Digilent ({error}) — close WaveForms and retry.")
        return 1

    handles, counters = [], []
    try:
        for cam in sorted(config.cameras, key=lambda c: c.position):
            handle = backend.open(cam.serial)
            handle.begin_acquisition(64)
            handles.append(handle)
            counter = Counter(handle, f"cam{cam.position}")
            counter.start()
            counters.append(counter)

        results = {}
        print(f"{'rate':>6} | " + " | ".join(f"{c.label:>7}" for c in counters)
              + " | verdict")
        best_ok = None
        for rate in args.rates:
            plan = clock.configure(rate, 0.5)
            clock.start()
            time.sleep(SETTLE_S)
            for counter in counters:
                counter.reset()
                counter.counting = True
            t0 = time.perf_counter()
            time.sleep(args.seconds)
            elapsed = time.perf_counter() - t0
            for counter in counters:
                counter.counting = False
            # the honest falling-behind signal: frames piling up in the stream
            backlogs = []
            for handle in handles:
                try:
                    stream = handle._acquirer.data_streams[0].module
                    backlogs.append(int(stream.num_awaiting_delivery))
                except Exception:  # noqa: BLE001
                    backlogs.append(-1)
            clock.stop()
            fps = [counter.count / elapsed for counter in counters]
            # keeping up = counted rate within boundary jitter AND no backlog growth
            ok = all(f >= plan.achieved_fps - 0.6 for f in fps) and \
                 all(b <= 8 for b in backlogs if b >= 0)
            results[rate] = (fps, ok)
            if ok:
                best_ok = plan.achieved_fps
            print(f"{plan.achieved_fps:6.1f} | "
                  + " | ".join(f"{f:7.2f}" for f in fps)
                  + f" | backlog {max(backlogs)} | "
                  + ("ALL KEEP UP" if ok else "FALLING BEHIND"))
            if not ok:
                break  # no point going higher
        print()
        if best_ok:
            rec = int(best_ok * 0.8)
            print(f"max verified concurrent rate: {best_ok:.1f} Hz")
            print(f"RECOMMENDED experiment rate (20% headroom): ~{rec} Hz")
        else:
            print("even the lowest tested rate fell behind — check USB topology")
        return 0
    finally:
        for counter in counters:
            counter.stop_flag = True
        time.sleep(0.4)
        for handle in handles:
            try:
                handle.end_acquisition()
                handle.close()
            except Exception:  # noqa: BLE001
                pass
        backend.close()
        clock.close()


if __name__ == "__main__":
    raise SystemExit(main())
