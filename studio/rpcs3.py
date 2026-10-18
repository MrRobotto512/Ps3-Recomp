"""Command lines for working alongside RPCS3.

RPCS3 is the reference the port is checked against: it decrypts retail EBOOTs
(Utilities > Decrypt PS3 Binaries, which needs the firmware installed), and its
GDB stub lets ps3recomp's probe read registers and memory from a running game.
"""

from __future__ import annotations

import time
from pathlib import Path

from .pipeline import Project, Settings

# probe.py connects here. The GDB server is not in every RPCS3 build: it is a
# compile-time option (WITH_GDB_DEBUGGER), so snapshots may need an RPCS3 you
# built yourself, with its GDB server address set to 127.0.0.1:2345.
GDB_PORT = 2345


def boot_argv(project: Project, s: Settings) -> list[str]:
    """Boot the original game in RPCS3, for side-by-side comparison."""
    return [s.rpcs3, project.eboot]


def snapshot_argv(project: Project, s: Settings, after_s: float) -> tuple[list[str], Path]:
    """Boot in RPCS3, pause after `after_s` seconds and dump registers, the
    call stack and memory around them to JSON."""
    out = s.out_dir / f"rpcs3_snapshot_{time.strftime('%Y%m%d_%H%M%S')}.json"
    argv = [s.python, str(s.tools / "rpcs3_probe" / "probe.py"),
            "--rpcs3", s.rpcs3, "--title", project.eboot,
            "--boot-only-snapshot", f"{after_s:g}",
            "--port", str(GDB_PORT), "--out", str(out)]
    return argv, out
