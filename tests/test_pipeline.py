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
    assert [x.key for x in steps] == ["load", "lift", "spu"]
    assert [x.key for x in p.recompile_steps(p.Project(eboot=str(eboot), lift_spu=False), s)] == ["load", "lift"]
    lift = steps[1].argv
    assert lift[lift.index("--functions") + 1].endswith("EBOOT.functions.json")
    assert lift[lift.index("--chunks") + 1] == "8"
    assert lift[lift.index("--output") + 1] == str(s.recompiled_dir)


def test_self_steps_unwrap_into_out(tmp_path):
    eboot = make_game(tmp_path, b"SCE\0")
    s = p.Settings(work_dir=str(tmp_path / "w"))
    project = p.Project(eboot=str(eboot))
    steps = p.recompile_steps(project, s)
    assert [x.key for x in steps] == ["unwrap", "load", "lift", "spu"]
    elf = p.elf_for(project, s)
    assert elf == s.out_dir / "EBOOT.elf"
    assert steps[0].argv[-1] == str(elf) and steps[1].argv[2] == str(elf)


def test_build_steps_and_exe_path(tmp_path):
    project = p.Project(eboot="x", port_name="MyGame")
    vs = p.Settings(work_dir=str(tmp_path), generator="vs2022-clangcl", build_type="Release")
    configure, build = p.build_steps(project, vs)
    assert "ClangCL" in configure.argv and "-DPORT_NAME=MyGame" in configure.argv
    assert "-DPORT_SPU_INPUTS=" in configure.argv and f"-DSPU_DIR={vs.spu_dir}" in configure.argv
    assert "-DPORT_AVX2=ON" in configure.argv
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
    assert p.executable_path(project, ninja).parent == tmp_path / "build-ninja"


def test_ninja_clangcl_builds_in_the_visual_studio_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "vs_env", lambda: {"PATH": r"C:\VS\Llvm\x64\bin;C:\x", "INCLUDE": "inc"})
    project = p.Project(eboot="x", port_name="G")
    s = p.Settings(work_dir=str(tmp_path), generator="ninja-clangcl", build_type="Release")
    configure, build = p.build_steps(project, s)
    assert configure.argv[configure.argv.index("-G") + 1] == "Ninja"
    assert "-DCMAKE_CXX_COMPILER=clang-cl" in configure.argv and "-DCMAKE_BUILD_TYPE=Release" in configure.argv
    assert configure.env["INCLUDE"] == "inc" and build.env == configure.env
    assert s.build_dir == tmp_path / "build-ninja-clangcl"   # never the Visual Studio generator's folder
    assert p.executable_path(project, s) .parent == s.build_dir
    assert p.build_steps(project, p.Settings(work_dir=str(tmp_path), generator="ninja"))[0].env == {}


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


def make_ppu_elf(path: Path, vaddr: int, payload: bytes) -> None:
    """A minimal 64-bit big-endian ELF with one PT_LOAD holding payload at vaddr."""
    ehdr = b"\x7fELF\x02\x02\x01" + b"\0" * 9 + struct.pack(">HHIQQQIHHHHHH", 2, 21, 1, 0, 0x40,
                                                                0, 0, 64, 56, 1, 0, 0, 0)
    phdr = struct.pack(">IIQQQQQQ", 1, 5, 0x78, vaddr, vaddr, len(payload), len(payload), 0x10000)
    path.write_bytes(ehdr + phdr + payload)


def lift_spu_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("lift_spu", p.ROOT / "scripts" / "lift_spu.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lift_spu_raw_programs_and_job_modules():
    ls = lift_spu_module()
    assert ls.parse_raw("pm_wwsjob=0x69C880:0x2D80@0xA00/0x2BF0") == ("pm_wwsjob", 0x69C880, 0x2D80, 0xA00, 0x2BF0)
    assert ls.parse_raw("0x10100:1024") == (None, 0x10100, 1024, 0xA00, 0xA00)

    code = bytes(range(256)) * 4
    # A WWS job module: four ila words, entry 0x30, image size, magic at +0x20.
    jm = struct.pack(">4I", 0x42000000, 0x42000081, 0x42000102, 0x42000183)
    jm += struct.pack(">5I", 0x30, len(code) + 0x30 + 32, 0, 0, 0xC0DEC0DE) + b"\0" * 12 + code
    mods = ls.find_jobmods(b"\x11" * 8 + jm + b"\0" * 64)
    assert len(mods) == 1 and mods[0][0].endswith("_e0030") and len(mods[0][1]) == len(code) + 0x30


def test_raw_program_is_cut_from_its_load_segment(tmp_path):
    ls = lift_spu_module()
    code = bytes(range(256)) * 4
    elf = tmp_path / "EBOOT.elf"
    make_ppu_elf(elf, 0x10000, b"\0" * 0x100 + code + b"\0" * 0x40)
    assert ls.read_vaddr(elf.read_bytes(), 0x10100, 1024) == code


def test_raw_spu_and_reference_port_steps(tmp_path):
    eboot = make_game(tmp_path, b"\x7fELF")
    ref = tmp_path / "inFAMOUSreborn"
    (ref / "ps3recomp" / "tools").mkdir(parents=True)
    (ref / "ps3recomp" / "tools" / "ppu_lifter.py").write_text("")   # no --chunks
    (ref / "installer" / "patches").mkdir(parents=True)
    (ref / "infamous-recomp" / "spu").mkdir(parents=True)
    (ref / "infamous-recomp" / "spu" / "jobmods_extra_map.json").write_text("{}")
    (ref / "installer" / "infamous.toml").write_text('[launch]\nenv = { A = "1", B = "x" }\n')
    found = p.reference_port_files(str(ref))
    assert found["toolkit"] == str(ref / "ps3recomp") and found["launch_env"] == "A=1 B=x"

    s = p.Settings(work_dir=str(tmp_path / "w"), toolkit_dir=found["toolkit"], reference_port=str(ref))
    project = p.Project(eboot=str(eboot), port_name="Infamous",
                        raw_spu="pm_wwsjob=0x69C880:0x2D80@0xA00/0x2BF0")
    steps = p.recompile_steps(project, s)
    assert [x.key for x in steps] == ["load", "lift", "spu", "patches"]
    assert "--chunks" not in steps[1].argv
    spu = steps[2].argv
    assert spu[spu.index("--raw") + 1] == "pm_wwsjob=0x69C880:0x2D80@0xA00/0x2BF0"
    assert spu[spu.index("--jobmod-prefix") + 1] == "infamous"
    assert spu[spu.index("--jobmod-extra-map") + 1] == found["jobmod_map"]
    assert steps[3].argv[2] == found["patches"] and "--address-map" not in steps[3].argv
    s.address_map = str(tmp_path / "eu_to_us.txt")
    patches = p.recompile_steps(project, s)[3].argv
    assert patches[patches.index("--address-map") + 1] == s.address_map


def apply_patches_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("app", p.ROOT / "scripts" / "apply_port_patches.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_address_map_formats_and_translation(tmp_path):
    app = apply_patches_module()
    pairs = tmp_path / "pairs.txt"
    pairs.write_text("# EU US\n0x00010000 0x00010000\n0x00200000 0x00200010\n0x00718748 0x00718760\n")
    ranges = tmp_path / "ranges.json"
    ranges.write_text('{"ranges": [["0x10000", "0x1FFFFF", 0], ["0x200000", "0x718748", 16]]}')
    for f in (pairs, ranges):
        m = app.AddressMap.load(f)
        assert m(0x100000) == 0x100000 and m(0x300000) == 0x300010
        line = "ctx->lr = 0x0039D98C; func_00353A80(ctx); goto loc_000CA540; x = 0x40000010;"
        assert m.translate(line) == ("ctx->lr = 0x0039D99C; func_00353A90(ctx); goto loc_000CA540; "
                                     "x = 0x40000010;")   # outside the map: left alone


def test_patch_hunks_follow_labels_and_skip_decoys(tmp_path):
    app = apply_patches_module()
    patches = tmp_path / "patches" / "port" / "recompiled"
    patches.mkdir(parents=True)
    (patches / "ppu_recomp_000.cpp.patch").write_text(
        "--- a/port/recompiled/ppu_recomp_000.cpp\n+++ b/port/recompiled/ppu_recomp_000.cpp\n"
        "@@ -10,3 +10,3 @@\n         x = 1;\n-        memcpy(a);\n+        stvx(a);\n         goto loc_00020000;\n")
    work = tmp_path / "work" / "recompiled"
    work.mkdir(parents=True)
    block = ["        x = 1;", "        memcpy(a);", "        goto loc_00020100;"]
    (work / "ppu_recomp_003.cpp").write_text("\n".join(          # chunks need not split alike
        block + ["        /* elsewhere */"] * 50 + ["loc_00020100:", "        y = 2;"] + block) + "\n")
    (tmp_path / "map.txt").write_text("0x00010000 0x00010100\n0x00718748 0x00718848\n")
    import subprocess
    subprocess.run([sys.executable, str(p.ROOT / "scripts" / "apply_port_patches.py"), str(tmp_path / "patches"),
                    "--work", str(tmp_path / "work"), "--address-map", str(tmp_path / "map.txt")], check=True)
    lines = (work / "ppu_recomp_003.cpp").read_text().split("\n")
    assert lines[1] == "        memcpy(a);"            # the decoy far from loc_00020100 is untouched
    assert lines[56] == "        stvx(a);"              # the copy beside its label is patched


def test_launch_sets_vfs_root(tmp_path):
    project = p.Project(eboot=str(tmp_path / "EBOOT.elf"), game_root=str(tmp_path), port_name="G")
    argv, env = p.launch_argv(project, p.Settings(work_dir=str(tmp_path), generator="ninja"))
    assert argv[1] == str(tmp_path / "EBOOT.elf")
    assert env == {"PS3_VFS_ROOT": str(tmp_path)}
    s = p.Settings(work_dir=str(tmp_path), generator="ninja", launch_env=" RSX_LIVE_DRAW=10;X=a=b junk")
    assert p.launch_argv(project, s)[1] == {"RSX_LIVE_DRAW": "10", "X": "a=b",
                                            "PS3_VFS_ROOT": str(tmp_path)}


def test_libsre_step_and_configure(tmp_path):
    eboot = make_game(tmp_path, b"\x7fELF")
    s = p.Settings(work_dir=str(tmp_path / "w"), generator="ninja")
    project = p.Project(eboot=str(eboot))
    assert "libsre" not in [x.key for x in p.recompile_steps(project, s)]
    configure = p.build_steps(project, s)[0].argv
    assert "-DPORT_PRX_DIR=" in configure and "-DPORT_INSTRUMENTED_RUNNER=ON" in configure

    project.libsre = str(tmp_path / "libsre.prx")
    step = next(x for x in p.recompile_steps(project, s) if x.key == "libsre")
    assert step.argv[2] == project.libsre
    assert step.argv[step.argv.index("--output") + 1] == str(s.prx_dir)
    configure = p.build_steps(project, s)[0].argv
    assert f"-DPORT_PRX_DIR={s.prx_dir}" in configure
    spu = next(x for x in p.recompile_steps(project, s) if x.key == "spu").argv
    assert spu[spu.index("--libsre-image") + 1] == str(s.prx_dir / "libsre.linked.bin")


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
