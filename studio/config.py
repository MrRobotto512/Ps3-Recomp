"""Persist Settings and the current Project between runs."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path

from .pipeline import Project, Settings


def config_path() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "PS3RecompStudio" / "config.json"


def _fill(cls, raw):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (raw or {}).items() if k in names})


def load() -> tuple[Settings, Project]:
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings(), Project()
    try:
        return _fill(Settings, raw.get("settings")), _fill(Project, raw.get("project"))
    except TypeError:
        return Settings(), Project()


def save(settings: Settings, project: Project) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"settings": asdict(settings), "project": asdict(project)}, indent=2),
                 encoding="utf-8")
