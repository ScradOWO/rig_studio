"""Named stimulus profiles: the stimulus panel's full state (grating +
circles + durations) saved by name in one YAML next to rig.yaml."""
from __future__ import annotations

import os
from pathlib import Path

import yaml


def load_all(path: str | Path) -> dict[str, dict]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data or {}


def save(path: str | Path, name: str, settings: dict) -> None:
    data = load_all(path)
    data[name] = settings
    _write(path, data)


def delete(path: str | Path, name: str) -> None:
    data = load_all(path)
    data.pop(name, None)
    _write(path, data)


def _write(path: str | Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
                   encoding="utf-8")
    os.replace(tmp, path)
