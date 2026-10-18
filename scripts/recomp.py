#!/usr/bin/env python3
"""Run the ps3recomp pipeline on one PS3 executable, without the GUI.

    python scripts/recomp.py game/PS3_GAME/USRDIR/EBOOT.elf [--build]

Steps, all using the tools in the ps3recomp submodule (the same command lines
PS3 Recomp Studio runs; see studio/pipeline.py):

  0. If the input is a SELF (EBOOT.BIN), rebuild the ELF with unfself.py.
     That only works for debug/prototype fSELFs. A retail EBOOT is encrypted:
     decrypt it with RPCS3 first (Utilities > Decrypt PS3 Binaries) and pass
     the resulting EBOOT.elf instead.
  1. ppu_loader.py  -> out/EBOOT.{functions,imports,image,loader}.json
  2. ppu_lifter.py  -> recompiled/ppu_recomp.h + lifted C++ sources
  3. with --build: CMake configure + build -> build/<port name>
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio.pipeline import (FMT_SELF, FMT_UNKNOWN, GENERATORS, Project, Settings,  # noqa: E402
                             build_steps, detect_format, executable_path, guess_game_root,
                             launch_argv, recompile_steps)


def main():
    s = Settings()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="decrypted EBOOT.elf, or a debug fSELF EBOOT.BIN")
    ap.add_argument("--work", default=s.work_dir,
                    help="where out/, recompiled/ and build/ go (default: repository root)")
    ap.add_argument("--chunks", type=int, default=s.chunks,
                    help="split the lift into N files by guest address, so a relift "
                         "only rewrites what changed (default: %(default)s; 0 = by line count)")
    ap.add_argument("--clean", action="store_true", help="delete the previous lift first")
    ap.add_argument("--build", action="store_true", help="also configure and build the port")
    ap.add_argument("--name", default="PS3Port", help="executable name (default: %(default)s)")
    ap.add_argument("--generator", choices=list(GENERATORS), default=s.generator)
    ap.add_argument("--config", default=s.build_type, help="build type (default: %(default)s)")
    args = ap.parse_args()

    s.work_dir, s.chunks, s.generator, s.build_type = str(args.work), args.chunks, args.generator, args.config
    if not (s.tools / "ppu_lifter.py").exists():
        sys.exit("ps3recomp submodule is missing: run `git submodule update --init`")
    if not args.input.is_file():
        sys.exit(f"no such file: {args.input}")
    if detect_format(args.input) == FMT_UNKNOWN:
        sys.exit(f"{args.input} is neither an ELF nor a SELF")

    project = Project(eboot=str(args.input.resolve()), game_root=guess_game_root(args.input),
                      port_name=args.name)
    if args.clean and s.recompiled_dir.exists():
        shutil.rmtree(s.recompiled_dir)
    s.out_dir.mkdir(parents=True, exist_ok=True)

    steps = recompile_steps(project, s) + (build_steps(project, s) if args.build else [])
    for step in steps:
        print(f"\n> {step.title}\n+ {' '.join(step.argv)}", flush=True)
        if subprocess.run(step.argv, cwd=step.cwd).returncode != 0:
            if step.key == "unwrap" and detect_format(args.input) == FMT_SELF:
                sys.exit("\nThis SELF could not be unwrapped without keys, so it is probably a\n"
                         "retail (encrypted) EBOOT. Decrypt it with RPCS3: Utilities > Decrypt\n"
                         "PS3 Binaries, then run this script on the EBOOT.elf it writes.")
            sys.exit(f"\n{step.title} failed; see the output above.")

    if args.build:
        argv, env = launch_argv(project, s)
        print(f"\nBuilt {executable_path(project, s)}. Run it with:\n  {' '.join(argv)}"
              + (f"\n  (with PS3_VFS_ROOT={env['PS3_VFS_ROOT']})" if env else ""))
    else:
        print(f"\nLifted into {s.recompiled_dir}. Build with --build, or open PS3 Recomp Studio.")


if __name__ == "__main__":
    main()
