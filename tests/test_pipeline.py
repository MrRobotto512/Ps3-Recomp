"""Tests for the Qt-free parts of PS3 Recomp Studio. Run: python -m pytest tests"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio import pipeline as p  # noqa: E402
from studio.sfo import read_sfo  # noqa: E402


def make_sfo(path: Path, items: dict[str, str]) -> None:
    keys = data = idx = b""
    for k, v in items.items():
        vb = v.encode() + b"\0"
        size = (len(vb) + 3) // 4 * 4
        idx += struct.pack("<HHIII", len(keys), 0x0204, len(vb), size, len(data))
        keys += k.encode() + b"\0"
        data += vb.ljust(size, b"\0")
    keys = keys.ljust((len(keys) + 3) // 4 * 4, b"\0")
    kt = 20 + len(idx)
    path.write_bytes(b"\0PSF" + struct.pack("<IIII", 0x101, kt, kt + len(keys), len(items))
                     + idx + keys + data)


def make_game(tmp_path: Path, magic: bytes) -> Path:
    usrdir = tmp_path / "BLUS12345" / "PS3_GAME" / "USRDIR"
    usrdir.mkdir(parents=True)
    eboot = usrdir / ("EBOOT.elf" if magic == b"\x7fELF" else "EBOOT.BIN")
    eboot.write_bytes(magic + b"\0" * 60)
    make_sfo(usrdir.parent / "PARAM.SFO", {"TITLE": "My Great Game", "TITLE_ID": "BLUS12345"})
    return eboot


def test_detect_format(tmp_path):
    assert p.detect_format(make_game(tmp_path / "a", b"\x7fELF")) == p.FMT_ELF
    assert p.detect_format(make_game(tmp_path / "b", b"SCE\0")) == p.FMT_SELF
    junk = tmp_path / "junk"
    junk.write_bytes(b"nope")
    assert p.detect_format(junk) == p.FMT_UNKNOWN
    assert p.detect_format(tmp_path / "missing") == p.FMT_UNKNOWN


def test_game_root_and_sfo(tmp_path):
    eboot = make_game(tmp_path, b"\x7fELF")
    assert Path(p.guess_game_root(eboot)) == (tmp_path / "BLUS12345").resolve()
    meta = read_sfo(p.find_param_sfo(eboot))
    assert meta["TITLE"] == "My Great Game" and meta["TITLE_ID"] == "BLUS12345"
    assert p.sanitize_name(meta["TITLE"]) == "MyGreatGame"
    assert p.sanitize_name("2 Fast!") == "Port2Fast"


def test_elf_steps_skip_unwrap(tmp_path):
    eboot = make_game(tmp_path, b"\x7fELF")
    s = p.Settings(work_dir=str(tmp_path / "w"), chunks=8)
    steps = p.recompile_steps(p.Project(eboot=str(eboot)), s)
    assert [x.key for x in steps] == ["load", "lift"]
    lift = steps[1].argv
    assert lift[lift.index("--functions") + 1].endswith("EBOOT.functions.json")
    assert lift[lift.index("--chunks") + 1] == "8"
    assert lift[lift.index("--output") + 1] == str(s.recompiled_dir)


def test_self_steps_unwrap_into_out(tmp_path):
    eboot = make_game(tmp_path, b"SCE\0")
    s = p.Settings(work_dir=str(tmp_path / "w"))
    project = p.Project(eboot=str(eboot))
    steps = p.recompile_steps(project, s)
    assert [x.key for x in steps] == ["unwrap", "load", "lift"]
    elf = p.elf_for(project, s)
    assert elf == s.out_dir / "EBOOT.elf"
    assert steps[0].argv[-1] == str(elf) and steps[1].argv[2] == str(elf)


def test_build_steps_and_exe_path(tmp_path):
    project = p.Project(eboot="x", port_name="MyGame")
    vs = p.Settings(work_dir=str(tmp_path), generator="vs2022-clangcl", build_type="Release")
    configure, build = p.build_steps(project, vs)
    assert "ClangCL" in configure.argv and "-DPORT_NAME=MyGame" in configure.argv
    assert "-DPORT_SPU_INPUTS=x" in configure.argv
    project.lift_spu = False
    assert "-DPORT_SPU_INPUTS=" in p.build_steps(project, vs)[0].argv   # cleared, not stale
    project.lift_spu = True
    assert not any(a.startswith("-DCMAKE_BUILD_TYPE") for a in configure.argv)
    assert build.argv[build.argv.index("--config") + 1] == "Release"
    assert "/p:UseMultiToolTask=true" in build.argv[build.argv.index("--"):]
    assert not any(a.startswith("/p:CL_MPCount") for a in build.argv)
    vs.jobs = 6
    build = p.build_steps(project, vs)[1].argv
    assert build[build.index("--parallel") + 1] == "6" and "/p:CL_MPCount=6" in build
    assert p.executable_path(project, vs).parent == tmp_path / "build" / "Release"

    ninja = p.Settings(work_dir=str(tmp_path), generator="ninja", build_type="Debug")
    configure, build = p.build_steps(project, ninja)
    assert "-DCMAKE_BUILD_TYPE=Debug" in configure.argv and "--config" not in build.argv
    assert "--" not in build.argv
    assert p.executable_path(project, ninja).parent == tmp_path / "build"


def test_build_progress_counts_fresh_lifted_objects(tmp_path):
    import os
    s = p.Settings(work_dir=str(tmp_path), generator="vs2022-clangcl")
    project = p.Project(eboot="x", port_name="MyGame")
    s.recompiled_dir.mkdir()
    for name in ("ppu_recomp_000.cpp", "ppu_recomp_001.cpp", "ppu_recomp_002.cpp"):
        (s.recompiled_dir / name).write_text("")
    objs = s.build_dir / "MyGame.dir" / "Release"
    objs.mkdir(parents=True)
    (objs / "ppu_recomp_000.obj").write_text("")
    (objs / "ppu_recomp_001.obj").write_text("")
    (objs / "main.obj").write_text("")                     # not a lifted file
    stale = objs / "ppu_recomp_002.obj"
    stale.write_text("")
    os.utime(stale, (1000, 1000))                          # from an earlier build
    other = s.build_dir / "ps3recomp_sdk" / "ppu_recomp_002.obj"   # another target
    other.parent.mkdir(parents=True)
    other.write_text("")
    assert p.build_progress(project, s, since=2000) == (2, 3)


def test_launch_sets_vfs_root(tmp_path):
    project = p.Project(eboot=str(tmp_path / "EBOOT.elf"), game_root=str(tmp_path), port_name="G")
    argv, env = p.launch_argv(project, p.Settings(work_dir=str(tmp_path), generator="ninja"))
    assert argv[1] == str(tmp_path / "EBOOT.elf")
    assert env == {"PS3_VFS_ROOT": str(tmp_path)}


def test_find_cmake_inside_visual_studio(tmp_path, monkeypatch):
    vs_cmake = (tmp_path / "PF" / "Microsoft Visual Studio" / "2022" / "Community" / "Common7"
                / "IDE" / "CommonExtensions" / "Microsoft" / "CMake" / "CMake" / "bin" / "cmake.exe")
    vs_cmake.parent.mkdir(parents=True)
    vs_cmake.write_text("")
    monkeypatch.setattr(p, "_IS_WINDOWS", True)
    monkeypatch.setattr(p.shutil, "which", lambda *_: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "PF"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "PF86"))
    assert p.find_cmake("cmake") == str(vs_cmake)
    # An explicit path in Settings wins, and the build steps use what was found.
    assert p.find_cmake(str(vs_cmake)) == str(vs_cmake)
    steps = p.build_steps(p.Project(eboot="x"), p.Settings(work_dir=str(tmp_path)))
    assert steps[0].argv[0] == str(vs_cmake) and steps[1].argv[0] == str(vs_cmake)


def test_find_cmake_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "_IS_WINDOWS", True)
    monkeypatch.setattr(p.shutil, "which", lambda *_: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "none"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "none86"))
    assert p.find_cmake("cmake") is None


def test_step_logs_and_zip(tmp_path):
    import zipfile
    s = p.Settings(work_dir=str(tmp_path))
    first, second = p.new_log_dir(s, "MyGame"), p.new_log_dir(s, "MyGame")
    assert first.parent == s.logs_dir and first != second   # same second, still unique
    step = p.Step("lift", "Lift PPU code", "", ["python", "lift me.py"])
    log = p.StepLog(first / p.step_log_name(1, step), step)
    log.write("lifting...\n")
    log.close(False, 3)
    log.close(True)   # a second close is a no-op
    assert log.path.name == "02-lift.log"
    text = log.path.read_text(encoding="utf-8")
    assert '# command  python "lift me.py"' in text and "lifting..." in text
    assert "FAILED (exit code 3)" in text
    step2 = p.StepLog(second / "cancelled.log", step)
    step2.close(False, 62097, cancelled=True)
    assert ": cancelled" in step2.path.read_text(encoding="utf-8")
    with zipfile.ZipFile(p.zip_logs(first, tmp_path / "logs.zip")) as z:
        assert z.namelist() == [f"{first.name}/02-lift.log"]
