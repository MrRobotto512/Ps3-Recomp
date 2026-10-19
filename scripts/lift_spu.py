#!/usr/bin/env python3
"""Lift every SPU program a port needs into spu/, the folder CMakeLists.txt builds.

    python scripts/lift_spu.py EBOOT.elf --out spu [--raw pm_wwsjob=0x69C880:0x2D80@0xA00/0x2BF0]

The same route ps3recomp ports that ship (inFamous among them) take, step by step:

  1. extract_spu_images.py  the SPU ELFs embedded in the executable (and in a
                            lifted libsre: the SPURS kernel itself)
  2. --raw                  programs that are bare code rather than an ELF -- a SPURS
                            policy module the game hands to cellSpursAddWorkload --
                            cut out of the executable and wrapped as an SPU ELF
  3. build_spu_workloads.py lift them all and write spu_workloads.c, which registers
                            each by fingerprint (sequential image ids)
  4. WWS job modules        the jobs a WWS job manager streams into LS 0x4000: every
                            C0DEC0DE module in the executable, lifted by lift_jobmods.py
  5. speed-up passes        spu_regcache.py, plug_lift.py, spu_ibfast.py, spu_v200.py,
                            each only if the toolkit has it (inFAMOUSreborn's does)

RAW is [NAME=]VADDR:SIZE[@BASE[/ENTRY]]. A policy module loads at LS 0xA00 (the
default base); its entry can lie further in -- inFamous's Wws_Job module starts at
0x2BF0. The NAME becomes the lifted folder's name (default spu_raw_<vaddr>).

Everything written here is game code: keep it out of git (spu/ and out/ are ignored).
"""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_MODULE_BASE = 0xA00
JOBMOD_MAGIC = b"\xc0\xde\xc0\xde"


def run(argv: list, env: dict | None = None) -> None:
    print("+ " + " ".join(str(a) for a in argv), flush=True)
    proc = subprocess.run([str(a) for a in argv], env={**os.environ, **(env or {})})
    if proc.returncode != 0:
        sys.exit(f"{Path(str(argv[1])).name} failed (exit code {proc.returncode})")


def parse_raw(text: str) -> tuple[str | None, int, int, int, int]:
    """[NAME=]VADDR:SIZE[@BASE[/ENTRY]] -> (name, vaddr, size, base, entry)."""
    name, _, spec = text.rpartition("=")
    spec, _, where = spec.partition("@")
    vaddr, _, size = spec.partition(":")
    if not size:
        raise ValueError(f"{text!r}: expected [NAME=]VADDR:SIZE[@BASE[/ENTRY]]")
    base, _, entry = where.partition("/")
    base = int(base, 0) if base else POLICY_MODULE_BASE
    return name or None, int(vaddr, 0), int(size, 0), base, int(entry, 0) if entry else base


def read_vaddr(elf: bytes, vaddr: int, size: int) -> bytes:
    """Bytes at a guest address of a 64-bit big-endian (PPU) ELF's PT_LOADs."""
    if elf[:4] != b"\x7fELF" or elf[4] != 2 or elf[5] != 2:
        raise ValueError("not a 64-bit big-endian ELF (a decrypted EBOOT.elf is needed)")
    phoff = struct.unpack_from(">Q", elf, 0x20)[0]
    phentsize, phnum = struct.unpack_from(">HH", elf, 0x36)
    for i in range(phnum):
        p_type, _, p_offset, p_vaddr, _, p_filesz = struct.unpack_from(">IIQQQQ", elf, phoff + i * phentsize)
        if p_type == 1 and p_vaddr <= vaddr and vaddr + size <= p_vaddr + p_filesz:
            start = p_offset + vaddr - p_vaddr
            return elf[start:start + size]
    raise ValueError(f"0x{vaddr:X}+{size} is not inside one loaded segment")


def find_jobmods(data: bytes) -> list[tuple[str, bytes]]:
    """WWS job modules: four `ila` words, the entry offset at +0x10, the image size at
    +0x14 and 0xC0DEC0DE at +0x20. The image minus its fixed 32-byte bss tail is the
    module, as tools/extract_jobmods.py reads it; named the way lift_jobmods.py and
    ports name them, jobmod_<sha1[:12]>_e<entry>."""
    mods, seen = [], set()
    at = data.find(JOBMOD_MAGIC)
    while at != -1:
        start = at - 0x20
        if start >= 0 and start % 4 == 0 and all(data[start + 4 * k] in (0x42, 0x43) for k in range(4)):
            entry, size = struct.unpack_from(">II", data, start + 0x10)
            body = data[start:start + size - 32] if 0x30 < size <= 0x40000 else b""
            if len(body) == size - 32:
                name = f"jobmod_{hashlib.sha1(body).hexdigest()[:12]}_e{entry:04X}"
                if name not in seen:
                    seen.add(name)
                    mods.append((name, body))
        at = data.find(JOBMOD_MAGIC, at + 4)
    return mods


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("elf", type=Path, help="the decrypted EBOOT.elf")
    ap.add_argument("--out", type=Path, required=True, help="the port's spu/ folder")
    ap.add_argument("--work", type=Path, help="scratch folder (default: <out>/../out/spu_images)")
    ap.add_argument("--raw", action="append", default=[], metavar="[NAME=]VADDR:SIZE[@BASE[/ENTRY]]")
    ap.add_argument("--libsre-image", type=Path, help="prx/libsre.linked.bin: also lift the SPURS kernel")
    ap.add_argument("--jobmod-prefix", default="port", help="symbol prefix of the lifted job modules")
    ap.add_argument("--jobmod-extra-map", type=Path,
                    help="extra job-module entry points, {module: [addr, ...]} (JOBMOD_EXTRA_MAP)")
    ap.add_argument("--toolkit", type=Path, default=ROOT / "ps3recomp")
    args = ap.parse_args()

    tools = args.toolkit / "tools"
    py = sys.executable
    elf = args.elf.read_bytes()
    images = args.work or args.out.parent / "out" / "spu_images"
    for d in (images, args.out):      # a relift starts clean: no image of an older lift survives
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    # 1. embedded SPU ELFs
    run([py, tools / "extract_spu_images.py", args.elf, "--output", images])
    if args.libsre_image:
        sre = images / "_libsre"
        run([py, tools / "extract_spu_images.py", args.libsre_image, "--output", sre])
        for f in sorted(sre.glob("*.elf")):            # spu_0000_at_00020380 -> libsre_spu_0_at_30020380
            n, at = f.stem.split("_")[1], int(f.stem.rsplit("_", 1)[1], 16)
            f.rename(images / f"libsre_spu_{int(n)}_at_{0x30000000 + at:08X}.elf")
        sre.rmdir()

    # 2. raw programs
    sys.path.insert(0, str(tools))
    from wrap_spu_elf import wrap                       # noqa: E402  (the toolkit's)
    from build_spu_workloads import fnv1a64             # noqa: E402
    for text in args.raw:
        try:
            name, vaddr, size, base, entry = parse_raw(text)
            code = read_vaddr(elf, vaddr, size)
        except ValueError as e:
            sys.exit(f"--raw {e}")
        name = name or f"spu_raw_{vaddr:08X}"
        (images / f"{name}.elf").write_bytes(wrap(code, base=base, entry=entry))
        print(f"{name}: 0x{vaddr:08X} {size} bytes at LS 0x{base:X}, entry 0x{entry:X}, "
              f"fp=0x{fnv1a64(code):016X}", flush=True)

    # 3. lift and register
    run([py, tools / "build_spu_workloads.py", "--images", images, "--lifted", args.out,
         "--out", args.out / "spu_workloads.c", "--constructor"])

    # 4. WWS job modules
    mods = find_jobmods(elf)
    if mods:
        jm = args.out / "jobmods"
        jm.mkdir()
        for name, body in mods:
            (jm / f"{name}.bin").write_bytes(body)
        print(f"{len(mods)} WWS job module(s) in the executable", flush=True)
        env = {"JOBMOD_EXTRA_MAP": str(args.jobmod_extra_map)} if args.jobmod_extra_map else None
        run([py, tools / "lift_jobmods.py", "--jobmods", jm, "--out", args.out,
             "--register", args.out / "jobmods_register.c", "--symbol-prefix", args.jobmod_prefix], env)

    # 5. the extended toolkit's passes, in the order its ports run them
    if (tools / "spu_regcache.py").is_file():
        run([py, tools / "spu_regcache.py", args.out])
    if mods and (tools / "plug_lift.py").is_file() and (tools / "plug_obs.json").is_file():
        # Its observations come from one release of one game; where they do not fit,
        # go on without the plugins rather than build a half-written folder.
        cmd = [py, tools / "plug_lift.py", args.elf, args.out / "wwsplug", tools / "plug_obs.json"]
        print("+ " + " ".join(map(str, cmd)), flush=True)
        if subprocess.run([str(a) for a in cmd]).returncode != 0:
            print("plug_lift.py failed: continuing without the WWS plugin blobs", flush=True)
            shutil.rmtree(args.out / "wwsplug", ignore_errors=True)
    if mods and (tools / "spu_ibfast.py").is_file():
        run([py, tools / "spu_ibfast.py", args.out])
    if (tools / "spu_v200.py").is_file():
        for rc in sorted(args.out.glob("jobmod_*/spu_recomp_rc.c")) + sorted(args.out.glob("wwsplug/spu_recomp_rc.c")):
            run([py, tools / "spu_v200.py", rc])
    print(f"\nSPU programs lifted into {args.out}.")


if __name__ == "__main__":
    main()
