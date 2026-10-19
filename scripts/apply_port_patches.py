#!/usr/bin/env python3
"""Apply a reference port's patches to this port's generated code, where they fit.

    python scripts/apply_port_patches.py <inFAMOUSreborn>/installer/patches --work .

A port that ships patches it applies over freshly generated code (inFAMOUSreborn:
installer/patches/infamous-recomp/{recompiled,spu}/...) keeps fixes there that its
toolkit depends on -- ppu_recomp.h gains what that toolkit's runtime expects. Each
patch names its file under the port's own folder; that first path component is
dropped and the rest is looked up in --work (recompiled/..., spu/...). Embedded SPU
images are named after their address in the executable (spu_0000_at_005F6500), which
differs between releases of a game, so those are matched by their number instead.

Line endings are made to agree first: a patch made on Windows carries CRLF, and
whether a freshly generated file does depends on how the lifter wrote it.

A patch is applied only if it applies cleanly. The rest are listed, not forced:
code generated from a different game release or lifter differs, and a patch over
code it was not made for would break it. Uses `git apply`, which works outside a
repository.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


def locate(work: Path, target: Path) -> Path | None:
    if (work / target).is_file():
        return target
    m = re.fullmatch(r"(spu_\d{4})_at_[0-9A-Fa-f]+", target.parts[1]) if len(target.parts) > 2 else None
    if target.parts[0] == "spu" and m:
        for d in sorted((work / "spu").glob(f"{m.group(1)}_at_*")):
            alt = Path("spu", d.name, *target.parts[2:])
            if (work / alt).is_file():
                return alt
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patches", type=Path, help="the reference port's patch folder")
    ap.add_argument("--work", type=Path, required=True, help="the folder holding recompiled/ and spu/")
    args = ap.parse_args()

    git = shutil.which("git")
    if not git:
        sys.exit("git is needed to apply patches (git-scm.com)")
    patches = sorted(args.patches.rglob("*.patch"))
    if not patches:
        sys.exit(f"no .patch files under {args.patches}")

    applied, already, skipped, missing = [], [], [], []
    for p in patches:
        rel = p.relative_to(args.patches).with_suffix("")      # infamous-recomp/recompiled/x.cpp
        target = Path(*rel.parts[1:])                           # recompiled/x.cpp
        here = locate(args.work, target)
        if here is None:
            missing.append(target)
            continue
        # Both sides in LF while patching; the file gets its own line endings back after.
        text = p.read_bytes().replace(b"\r\n", b"\n")
        if here != target:   # same image under this release's address: point the patch at it
            text = text.replace(target.parent.as_posix().encode(), here.parent.as_posix().encode())
            target = here
        tmp = (args.work / "out" / "_port.patch").resolve()   # git runs in --work: name it absolutely
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_bytes(text)
        file = args.work / target
        original = file.read_bytes()
        crlf = b"\r\n" in original
        if crlf:
            file.write_bytes(original.replace(b"\r\n", b"\n"))
        cmd = [git, "apply", "-p2", "--whitespace=nowarn", str(tmp)]
        if subprocess.run(cmd + ["--check"], cwd=args.work, capture_output=True).returncode == 0:
            subprocess.run(cmd, cwd=args.work, check=True)
            applied.append(target)
            if crlf:
                file.write_bytes(file.read_bytes().replace(b"\n", b"\r\n"))
        else:
            file.write_bytes(original)
            if subprocess.run(cmd + ["--check", "--reverse"], cwd=args.work,
                              capture_output=True).returncode == 0:
                already.append(target)
            else:
                skipped.append(target)
        tmp.unlink()

    for title, items in (("applied", applied), ("already applied", already),
                         ("did not fit (left as generated)", skipped), ("no such file here", missing)):
        if items:
            print(f"{title}: {len(items)}")
            for t in items:
                print(f"    {t.as_posix()}")
    print(f"\n{len(applied) + len(already)} of {len(patches)} patches in place.")


if __name__ == "__main__":
    main()
