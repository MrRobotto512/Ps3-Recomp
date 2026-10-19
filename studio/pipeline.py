"""The recompile pipeline as plain data: which commands to run, in what order.

Nothing here imports Qt, so the CLI (scripts/recomp.py), the GUI and the tests
all build exactly the same command lines.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path



def _find_root() -> Path:
    """The repository: holds CMakeLists.txt and the ps3recomp submodule.

    A PyInstaller build lives in dist/ inside the repository, so search upward
    from the executable instead of trusting the bundled package location.
    """
    if getattr(sys, "frozen", False):
        for d in Path(sys.executable).resolve().parents:
            if (d / "CMakeLists.txt").is_file() and (d / "ps3recomp").is_dir():
                return d
    return Path(__file__).resolve().parent.parent


ROOT = _find_root()

# Generators offered in Settings. On Windows the Visual Studio generator finds
# the compiler itself, so no "x64 Native Tools" prompt is needed; ClangCL is
# what the ps3recomp ports are built with.
GENERATORS = {
    "vs2022-clangcl": ("Visual Studio 2022 (clang-cl)",
                       ["-G", "Visual Studio 17 2022", "-A", "x64", "-T", "ClangCL"]),
    "vs2022-msvc": ("Visual Studio 2022 (MSVC)",
                    ["-G", "Visual Studio 17 2022", "-A", "x64"]),
    "ninja": ("Ninja (compiler from PATH)", ["-G", "Ninja"]),
}
DEFAULT_GENERATOR = "vs2022-clangcl" if os.name == "nt" else "ninja"

_IS_WINDOWS = os.name == "nt"

# Format names shown in the UI.
FMT_ELF, FMT_SELF, FMT_UNKNOWN = "ELF", "SELF", "unknown"


def default_python() -> str:
    """The interpreter that runs the ps3recomp tools.

    Inside a PyInstaller build sys.executable is the app itself, so fall back
    to whatever Python is on PATH.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("py") or shutil.which("python") or shutil.which("python3") or "python"


def find_cmake(configured: str = "cmake") -> str | None:
    """Resolve the CMake to run.

    Visual Studio installs its own CMake but only puts it on PATH inside a
    Developer Prompt, so a plain "cmake" usually fails from a normal shell.
    Look there and in the standalone installer's folder too.
    """
    if configured and os.path.isfile(configured):
        return configured
    if found := shutil.which(configured or "cmake"):
        return found
    if not _IS_WINDOWS:
        return None
    candidates = []
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(pf86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        import subprocess
        try:
            out = subprocess.run([str(vswhere), "-all", "-products", "*", "-property",
                                  "installationPath"], capture_output=True, text=True,
                                 timeout=15).stdout
            candidates += [Path(line.strip()) for line in out.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), pf86):
        candidates += sorted((Path(base) / "Microsoft Visual Studio").glob("*/*"), reverse=True)
    for vs in candidates:
        exe = vs / "Common7" / "IDE" / "CommonExtensions" / "Microsoft" / "CMake" / "CMake" / "bin" / "cmake.exe"
        if exe.is_file():
            return str(exe)
    exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "CMake" / "bin" / "cmake.exe"
    return str(exe) if exe.is_file() else None


@dataclass
class Settings:
    toolkit_dir: str = str(ROOT / "ps3recomp")
    work_dir: str = str(ROOT)          # out/, recompiled/ and build/ live here
    python: str = field(default_factory=default_python)
    cmake: str = "cmake"
    generator: str = DEFAULT_GENERATOR
    build_type: str = "Release"
    chunks: int = 64
    jobs: int = 0                      # parallel compiles; 0 = one per CPU thread
    rpcs3: str = ""                    # path to rpcs3.exe
    theme: str = "auto"                # auto | light | dark
    # The toolkit's boot runner (hang watchdog, crash filter, RSX_LIVE_DRAW)
    # instead of the minimal main.cpp.
    instrumented_runner: bool = True
    launch_env: str = ""               # extra KEY=VALUE pairs for the port, e.g. RSX_LIVE_DRAW=1

    @property
    def tools(self) -> Path:
        return Path(self.toolkit_dir) / "tools"

    @property
    def out_dir(self) -> Path:
        return Path(self.work_dir) / "out"

    @property
    def recompiled_dir(self) -> Path:
        return Path(self.work_dir) / "recompiled"

    @property
    def build_dir(self) -> Path:
        return Path(self.work_dir) / "build"

    @property
    def prx_dir(self) -> Path:
        return Path(self.work_dir) / "prx"

    @property
    def logs_dir(self) -> Path:
        return Path(self.work_dir) / "logs"

    @property
    def multi_config(self) -> bool:
        return self.generator.startswith("vs")


@dataclass
class Project:
    eboot: str = ""          # EBOOT.BIN or a decrypted EBOOT.elf
    game_root: str = ""      # folder holding PS3_GAME/ (the guest's disc root)
    port_name: str = "PS3Port"
    # Lift the SPU programs embedded in the executable at configure time, so
    # SPURS workloads, tasks and jobs have native code to run. Without them a
    # SPURS game boots and then waits forever on work no SPU ever does.
    lift_spu: bool = True
    # Your decrypted libsre.prx. When set, Sony's real SPURS library is lifted
    # and run instead of ps3recomp's HLE one (scripts/lift_libsre.py).
    libsre: str = ""


@dataclass
class Step:
    key: str
    title: str
    description: str
    argv: list[str]
    cwd: str | None = None


def detect_format(path: str | os.PathLike) -> str:
    try:
        with open(path, "rb") as f:
            m = f.read(4)
    except OSError:
        return FMT_UNKNOWN
    if m == b"\x7fELF":
        return FMT_ELF
    if m == b"SCE\0":
        return FMT_SELF
    return FMT_UNKNOWN


def guess_game_root(eboot: str | os.PathLike) -> str:
    """<root>/PS3_GAME/USRDIR/EBOOT.BIN -> <root>; otherwise the EBOOT's folder."""
    p = Path(eboot).resolve()
    for parent in p.parents:
        if parent.name.upper() == "PS3_GAME":
            return str(parent.parent)
    return str(p.parent)


def find_param_sfo(eboot: str | os.PathLike) -> Path | None:
    p = Path(eboot).resolve()
    for d in (p.parent, *p.parents[:3]):
        for cand in (d / "PARAM.SFO", d / "PS3_GAME" / "PARAM.SFO"):
            if cand.is_file():
                return cand
    return None


def sanitize_name(text: str) -> str:
    """A CMake target / file name from a game title."""
    name = re.sub(r"[^A-Za-z0-9]+", "", text.title())
    if not name or not name[0].isalpha():
        name = "Port" + name
    return name[:40]


def elf_for(project: Project, s: Settings) -> Path:
    """The ELF the loader and lifter read: the input itself, or the unwrapped SELF."""
    src = Path(project.eboot)
    if detect_format(src) == FMT_SELF:
        return s.out_dir / (src.stem + ".elf")
    return src


def recompile_steps(project: Project, s: Settings) -> list[Step]:
    py, tools = s.python, s.tools
    src = Path(project.eboot)
    elf = elf_for(project, s)
    steps = []
    if detect_format(src) == FMT_SELF:
        steps.append(Step(
            "unwrap", "Unwrap SELF",
            "Rebuild the ELF from a debug fSELF. Retail EBOOTs need RPCS3 instead.",
            [py, str(tools / "unfself.py"), str(src), "--output", str(elf)]))
    steps.append(Step(
        "load", "Load ELF",
        "Find functions (OPD table), the TOC and firmware imports.",
        [py, str(tools / "ppu_loader.py"), str(elf), "--output", str(s.out_dir)]))
    steps.append(Step(
        "lift", "Lift PPU code",
        "Translate every PowerPC function into C++.",
        [py, str(tools / "ppu_lifter.py"), str(elf),
         "--functions", str(s.out_dir / f"{elf.stem}.functions.json"),
         "--hle-stubs", str(s.out_dir / f"{elf.stem}.imports.json"),
         "--chunks", str(s.chunks),
         "--output", str(s.recompiled_dir)]))
    if project.libsre:
        steps.append(Step(
            "libsre", "Lift libsre",
            "Lift Sony's SPURS library so the game runs it instead of the HLE one.",
            [py, str(ROOT / "scripts" / "lift_libsre.py"), project.libsre,
             "--output", str(s.prx_dir), "--toolkit", s.toolkit_dir]))
    return steps


def build_steps(project: Project, s: Settings) -> list[Step]:
    cmake = find_cmake(s.cmake) or s.cmake
    configure = [cmake, "-S", str(ROOT), "-B", str(s.build_dir),
                 *GENERATORS[s.generator][1],
                 f"-DPS3RECOMP_DIR={s.toolkit_dir}",
                 f"-DRECOMP_DIR={s.recompiled_dir}",
                 f"-DPORT_NAME={project.port_name}",
                 f"-DPORT_SPU_INPUTS={elf_for(project, s) if project.lift_spu else ''}",
                 f"-DPORT_PRX_DIR={s.prx_dir if project.libsre else ''}",
                 f"-DPORT_INSTRUMENTED_RUNNER={'ON' if s.instrumented_runner else 'OFF'}"]
    if not s.multi_config:
        configure.append(f"-DCMAKE_BUILD_TYPE={s.build_type}")
    build = [cmake, "--build", str(s.build_dir), "--parallel", *([str(s.jobs)] if s.jobs > 0 else [])]
    if s.multi_config:
        build += ["--config", s.build_type]
        # --parallel only lets MSBuild build several projects at once. The game
        # is one project of large lifted files, which it would then compile one
        # after another on a single core; the multi-tool task runs them side by side.
        build += ["--", "/p:UseMultiToolTask=true", "/p:EnforceProcessCountAcrossBuilds=true"]
        if s.jobs > 0:
            build.append(f"/p:CL_MPCount={s.jobs}")
    return [
        Step("configure", "Configure",
             "Generate the build with CMake" + (" and lift the SPU programs." if project.lift_spu else "."),
             configure),
        Step("build", "Build", "Compile the lifted code and the ps3recomp runtime.", build),
    ]


def build_progress(project: Project, s: Settings, since: float) -> tuple[int, int]:
    """(lifted files compiled since `since`, lifted files in total), counted from
    the object files in the build tree. The compiler prints nothing per file, so
    this is the only sign of how far a long build has got."""
    lifted = {f.stem for pat in ("*.c", "*.cpp") for f in s.recompiled_dir.glob(pat)}
    done = set()
    target_dir = f"{project.port_name}.dir"
    for pat in ("*.obj", "*.o"):
        for obj in s.build_dir.rglob(pat):
            stem = obj.name.split(".")[0]
            if stem in lifted and target_dir in obj.parts:
                try:
                    if obj.stat().st_mtime >= since:
                        done.add(stem)
                except OSError:
                    pass
    return len(done), len(lifted)


def executable_path(project: Project, s: Settings) -> Path:
    exe = project.port_name + (".exe" if os.name == "nt" else "")
    if s.multi_config:
        return s.build_dir / s.build_type / exe
    return s.build_dir / exe


def launch_argv(project: Project, s: Settings) -> tuple[list[str], dict[str, str]]:
    """Command and extra environment that start the built port."""
    argv = [str(executable_path(project, s)), str(elf_for(project, s))]
    env = parse_env(s.launch_env)
    if project.game_root:
        env["PS3_VFS_ROOT"] = project.game_root
    return argv, env


def parse_env(text: str) -> dict[str, str]:
    """'A=1 B=two; C=3' -> {'A': '1', 'B': 'two', 'C': '3'}. Words without '=' are ignored."""
    env = {}
    for item in re.split(r"[\s;]+", text or ""):
        key, sep, value = item.partition("=")
        if sep and key:
            env[key] = value
    return env


# ---------------------------------------------------------------------------
# Logs: every run gets a folder under logs/, with one file per step.
# ---------------------------------------------------------------------------

def new_log_dir(s: Settings, label: str) -> Path:
    """logs/<date-time>-<label>/, made unique if two runs start in the same second."""
    base = s.logs_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    d, n = base, 1
    while d.exists():
        n += 1
        d = base.with_name(f"{base.name}-{n}")
    d.mkdir(parents=True)
    return d


def step_log_name(index: int, step: Step) -> str:
    return f"{index + 1:02d}-{step.key}.log"


def quote_argv(argv: list[str]) -> str:
    return " ".join(f'"{a}"' if " " in a else a for a in argv)


class StepLog:
    """One step's output file: a header with the command, the output, then the result."""

    def __init__(self, path: Path, step: Step):
        self.path = path
        self._f = open(path, "w", encoding="utf-8", newline="")
        self._started = time.monotonic()
        self.write(f"# {step.title}\n"
                   f"# started  {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                   f"# cwd      {step.cwd or os.getcwd()}\n"
                   f"# command  {quote_argv(step.argv)}\n\n")

    def write(self, text: str) -> None:
        if self._f is not None:
            self._f.write(text)
            self._f.flush()

    def close(self, ok: bool, code: int | None = None, cancelled: bool = False) -> None:
        if self._f is None:
            return
        result = ("cancelled" if cancelled else "ok" if ok
                  else "FAILED" + (f" (exit code {code})" if code is not None else ""))
        self.write(f"\n\n# finished {time.strftime('%Y-%m-%d %H:%M:%S')}, "
                   f"{time.monotonic() - self._started:.1f} s: {result}\n")
        self._f.close()
        self._f = None


def zip_logs(log_dir: Path, dest: Path) -> Path:
    """Pack a run's log folder into one .zip, e.g. to attach to a bug report."""
    dest = Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(Path(log_dir).iterdir()):
            if f.is_file():
                z.write(f, f"{Path(log_dir).name}/{f.name}")
    return dest
