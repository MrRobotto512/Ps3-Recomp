#!/usr/bin/env python3
"""Cut raw SPU programs out of a PPU executable and wrap each one in an SPU ELF.

    python scripts/wrap_spu_raw.py EBOOT.elf --program 0x69C880:11648 --out-dir out/raw_spu

ps3recomp's SPU lifting (PORT_SPU_INPUTS) finds the SPU ELFs embedded in a
file. A SPURS policy module is often not an ELF but bare code the game hands
to cellSpursAddWorkload -- inFamous's Wws_Job job manager is 11648 bytes at
0x69C880 -- so it is never found, and the runtime reports

    [cellSpurs] wid=0 PM NOT LIFTED (fp=0x6FFFB30E41EE17BC size=11648)

(the HLE log's AddWorkload line gives the pm= address and size). The SPURS
kernel copies a policy module to local store 0xA00 and starts it there, so
each program is wrapped at that base by default. The ELF's code segment is
exactly the program's bytes, so the lift is registered under the fingerprint
the runtime computes from the guest's copy.

PROGRAM is VADDR:SIZE or VADDR:SIZE@BASE. The output is game code: keep it
out of git (out/ is ignored).
"""

import argparse
import struct
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "ps3recomp" / "tools"
POLICY_MODULE_BASE = 0xA00


def parse_program(text: str) -> tuple[int, int, int]:
    spec, _, base = text.partition("@")
    vaddr, _, size = spec.partition(":")
    if not size:
        raise ValueError(f"{text!r}: expected VADDR:SIZE[@BASE]")
    return int(vaddr, 0), int(size, 0), int(base, 0) if base else POLICY_MODULE_BASE


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


def output_name(vaddr: int) -> str:
    return f"spu_raw_{vaddr:08X}.elf"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("elf", type=Path, help="the decrypted EBOOT.elf")
    ap.add_argument("--program", action="append", required=True, metavar="VADDR:SIZE[@BASE]")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--toolkit", type=Path, default=TOOLS.parent)
    args = ap.parse_args()

    sys.path.insert(0, str(args.toolkit / "tools"))
    from wrap_spu_elf import wrap               # noqa: E402  (the toolkit's)
    from build_spu_workloads import fnv1a64      # noqa: E402

    elf = args.elf.read_bytes()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for old in args.out_dir.glob("spu_raw_*.elf"):   # drop programs no longer listed
        old.unlink()
    for text in args.program:
        try:
            vaddr, size, base = parse_program(text)
            code = read_vaddr(elf, vaddr, size)
        except ValueError as e:
            sys.exit(f"--program {e}")
        out = args.out_dir / output_name(vaddr)
        out.write_bytes(wrap(code, base=base, entry=base))
        print(f"0x{vaddr:08X} {size} bytes -> {out.name} at LS 0x{base:X}, fp=0x{fnv1a64(code):016X}")


if __name__ == "__main__":
    main()
