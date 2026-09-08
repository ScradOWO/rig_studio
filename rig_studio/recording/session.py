"""Session naming and sizing, matching the MATLAB/MEX conventions."""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path


def session_tag(fmt: str = "%Y%m%d_%H%M%S", now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime(fmt)


def max_frames(duration_s: float, fps_nominal: float, overhead: int) -> int:
    return math.ceil(duration_s * fps_nominal) + int(overhead)


def output_path(root: str | Path, template: str, base: str, position: int) -> Path:
    return Path(root) / template.format(base=base, position=position)
