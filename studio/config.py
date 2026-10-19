"""Persist Settings and the current Project between runs.

Settings must survive the checkout moving, being cloned again elsewhere, or a
config written on another PC or by a newer Studio: paths inside the checkout are
saved relative to it, and on load every value is checked -- a folder or program
that is not there, or a value of the wrong type, falls back to this machine's
default instead of breaking a step later. load() lists what it replaced.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, fields
from pathlib import Path

from .pipeline import GENERATORS, ROOT, Project, Settings

# Saved relative to the checkout when they lie inside it.
_PATH_SETTINGS = ("toolkit_dir", "work_dir", "python", "cmake", "reference_port", "address_map")


def config_path() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "PS3RecompStudio" / "config.json"


def _to_saved(value: str) -> str:
    if not value:
        return value
    try:
        rel = Path(value).resolve().relative_to(ROOT)
    except (ValueError, OSError):
        return value
    return rel.as_posix() or "."


def _from_saved(attr: str, value: str) -> str:
    """Relative paths were saved relative to the checkout; a bare python or cmake is a
    command found on PATH."""
    if not value or Path(value).is_absolute():
        return value
    if attr in ("python", "cmake") and "/" not in value and "\\" not in value:
        return value
    return str((ROOT / value).resolve())


def _program_ok(value: str) -> bool:
    return bool(value) and (Path(value).is_file() or shutil.which(value) is not None)


def _fill(cls, raw, notes: list[str]):
    default = cls()
    kept = {}
    for f in fields(cls):
        if f.name not in (raw or {}):
            continue
        v, want = raw[f.name], getattr(default, f.name)
        if type(v) is not type(want) and not (isinstance(want, float) and isinstance(v, int)):
            notes.append(f"{f.name}: {v!r} is not a {type(want).__name__}; using the default")
            continue
        kept[f.name] = v
    return cls(**kept)


def _check(s: Settings, notes: list[str]) -> None:
    d = Settings()
    def reset(attr: str, why: str) -> None:
        notes.append(f"{attr}: {getattr(s, attr)!r} {why}; using {getattr(d, attr)!r}")
        setattr(s, attr, getattr(d, attr))
    if not (Path(s.toolkit_dir) / "tools" / "ppu_lifter.py").is_file():
        reset("toolkit_dir", "has no tools/ppu_lifter.py")
    if not Path(s.work_dir).is_dir():
        reset("work_dir", "does not exist")
    if not _program_ok(s.python):
        reset("python", "was not found")
    if s.cmake != "cmake" and not _program_ok(s.cmake):
        reset("cmake", "was not found")
    if s.generator not in GENERATORS:
        reset("generator", "is not a compiler this Studio knows")
    for attr in ("reference_port", "address_map"):
        if getattr(s, attr) and not Path(getattr(s, attr)).exists():
            notes.append(f"{attr}: {getattr(s, attr)!r} does not exist; cleared")
            setattr(s, attr, "")


def load(notes: list[str] | None = None) -> tuple[Settings, Project]:
    notes = notes if notes is not None else []
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as e:
        if config_path().exists():
            notes.append(f"{config_path()} could not be read ({e}); starting from defaults")
        return Settings(), Project()
    saved = dict(raw.get("settings") or {})
    for attr in _PATH_SETTINGS:
        if isinstance(saved.get(attr), str):
            saved[attr] = _from_saved(attr, saved[attr])
    s = _fill(Settings, saved, notes)
    _check(s, notes)
    return s, _fill(Project, raw.get("project"), notes)


def save(settings: Settings, project: Project) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    for attr in _PATH_SETTINGS:
        data[attr] = _to_saved(data[attr])
    p.write_text(json.dumps({"settings": data, "project": asdict(project)}, indent=2),
                 encoding="utf-8")
