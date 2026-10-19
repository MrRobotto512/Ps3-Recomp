#!/usr/bin/env python3
"""Lift Sony's SPURS library (libsre) so a port runs it instead of ps3recomp's HLE.

    python scripts/lift_libsre.py path/to/libsre.prx --output prx/

SPURS titles (inFamous among them) hang on the HLE cellSpurs: its scheduler is a
reimplementation, and a workload whose policy module it cannot run never runs.
The alternative, described in ps3recomp/docs/FIRMWARE_LLE.md, is to lift the
real module. This script runs that pipeline with the toolkit's own tools:

  1. prx_relocate.py   -> libsre.linked.bin, the module linked at BASE
  2. lift_prx.py       -> function seeds, and libsre's own import stubs
  3. ppu_lifter.py     -> libsre_recomp*.cpp, its functions as C++ (libsre_ prefix);
                          the import stubs become ps3_hle_call(nid), so libsre's
                          calls into sysPrxForUser reach the HLE, and --code-end
                          keeps the data segment from being lifted as code
  4. prx_exports.py    -> libsre_exports.c, NID -> address for prx_resolve_export

prx_boot.cpp at the repository root loads the result at startup. The input is
your own decrypted dev_flash/sys/external/libsre.sprx (RPCS3: Utilities >
Decrypt PS3 Binaries). It and everything made from it is firmware: keep it out
of git (prx/ is ignored) and do not distribute builds that contain it.
"""

import argparse
import json
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = 0x30000000      # must match kLibsreBase in prx_boot.cpp


def code_end(prx: bytes) -> int:
    """BASE + the end of the PRX's executable PT_LOAD. Past it is the data segment
    (import slots, TOC, OPDs), which the lifter must not take for code."""
    phoff = struct.unpack_from(">Q", prx, 0x20)[0]
    phentsize, phnum = struct.unpack_from(">HH", prx, 0x36)
    for i in range(phnum):
        p_type, p_flags, _, p_vaddr, _, p_filesz = struct.unpack_from(">IIQQQQ", prx, phoff + i * phentsize)
        if p_type == 1 and p_flags & 1:   # PT_LOAD, PF_X
            return BASE + p_vaddr + p_filesz
    sys.exit("the PRX has no executable segment")


def run(argv: list) -> str:
    print("+ " + " ".join(str(a) for a in argv), flush=True)
    proc = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    print(proc.stdout, end="", flush=True)
    if proc.returncode != 0:
        sys.exit(f"{Path(str(argv[1])).name} failed (exit code {proc.returncode})")
    return proc.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prx", type=Path, help="decrypted libsre.prx (ELF, not the encrypted .sprx)")
    ap.add_argument("--output", type=Path, required=True, help="the port's prx/ folder")
    ap.add_argument("--toolkit", type=Path, default=ROOT / "ps3recomp")
    args = ap.parse_args()

    tools = args.toolkit / "tools"
    data = args.prx.read_bytes()
    if data[:4] != b"\x7fELF":
        sys.exit(f"{args.prx} is not a decrypted PRX. Decrypt libsre.sprx with RPCS3 first "
                 "(Utilities > Decrypt PS3 Binaries).")
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("libsre_recomp*"):   # a relift must not leave stale chunks behind
        old.unlink()

    py = sys.executable
    run([py, tools / "prx_relocate.py", args.prx, "--base", hex(BASE),
         "--out", out / "libsre.linked.bin"])

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        summary = run([py, tools / "lift_prx.py", args.prx, "--base", hex(BASE), "--output", tmp])
        m = re.search(r"TOC (0x[0-9A-Fa-f]+)", summary)
        if not m:
            sys.exit("lift_prx.py did not report the module's TOC")
        # lift_prx.py names its outputs after the input file, whatever it is called.
        stem = args.prx.stem
        # It lists each import by the address of its .sceStub trampoline, which
        # is what the lifter's --hle-stubs wants.
        imports = json.loads((tmp / f"{stem}_imports.json").read_text())
        stubs = [{"library": lib, "nid": nid, "stub": stub}
                 for lib, entries in imports.items() for nid, stub in entries.items()]
        (tmp / "hle_stubs.json").write_text(json.dumps(stubs, indent=1))

        run([py, tools / "ppu_lifter.py", out / "libsre.linked.bin", "--raw",
             "--base", hex(BASE), "--toc", m.group(1), "--symbol-prefix", "libsre_",
             "--functions", tmp / f"{stem}_functions.json", "--hle-stubs", tmp / "hle_stubs.json",
             "--code-end", hex(code_end(data)),
             "--header-name", "libsre_recomp.h", "--source-name", "libsre_recomp.cpp",
             "--output", out])

    run([py, tools / "prx_exports.py", args.prx, "--symbol", "libsre",
         "--out", out / "libsre_exports.c"])
    print(f"\nlibsre lifted into {out}. Configure and build the port to use it.")


if __name__ == "__main__":
    main()
