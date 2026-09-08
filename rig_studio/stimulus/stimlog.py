"""Per-frame stimulus log, saved as a MATLAB-compatible .mat with the same
field names the PTB script wrote (stim_log_<base>.mat, variable stimLog)."""
from __future__ import annotations

from pathlib import Path

import numpy as np


class StimLog:
    def __init__(self, capacity: int) -> None:
        self.vbl = np.zeros(capacity)
        self.tStim = np.zeros(capacity)
        self.dt = np.zeros(capacity)
        self.missed = np.zeros(capacity)
        self.yoffset = np.zeros(capacity)
        self.phaseDeg = np.zeros(capacity)
        self.circleScale = np.zeros(capacity)
        self.count = 0

    def append(self, vbl: float, t_stim: float, dt: float, missed: float,
               yoffset: float, phase_deg: float,
               circle_scale: float = 0.0) -> None:
        i = self.count
        if i >= len(self.vbl):
            return
        self.vbl[i] = vbl
        self.tStim[i] = t_stim
        self.dt[i] = dt
        self.missed[i] = missed
        self.yoffset[i] = yoffset
        self.phaseDeg[i] = phase_deg
        self.circleScale[i] = circle_scale
        self.count += 1


def save_mat(path: str | Path, log: StimLog, metadata: dict) -> None:
    from scipy.io import savemat

    n = log.count
    stim_log = {
        "vbl": log.vbl[:n], "tStim": log.tStim[:n], "dt": log.dt[:n],
        "missed": log.missed[:n], "yoffset": log.yoffset[:n],
        "phaseDeg": log.phaseDeg[:n], "circleScale": log.circleScale[:n],
    }
    stim_log.update(metadata)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    savemat(str(path), {"stimLog": stim_log})
