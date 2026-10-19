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

Each step's output is also saved to logs/<date-time>-<port name>/NN-<step>.log
(pass --zip-logs to pack them into one .zip, e.g. for a bug report).
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio.pipeline import (FMT_SELF, FMT_UNKNOWN, GENERATORS, Project, Settings,  # noqa: E402
                             StepLog, build_steps, detect_format, executable_path,
                             guess_game_root, launch_argv, new_log_dir, quote_argv,
                             recompile_steps, reference_port_files, step_log_name, zip_logs)


def run_logged(step, log_path: Path) -> int:
    """Run a step, echoing its output to the console and to its log file."""
    log = StepLog(log_path, step)
    env = dict(os.environ, **step.resolved_env(), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.Popen(step.argv, cwd=step.cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        log.write(f"could not start {step.argv[0]!r}: {e}\n")
        log.close(False)
        print(f"could not start {step.argv[0]!r}: {e}", file=sys.stderr)
        return 127
    out = sys.stdout.buffer
    while chunk := proc.stdout.read1(65536):
        out.write(chunk)
        out.flush()
        log.write(chunk.decode("utf-8", "replace"))
    code = proc.wait()
    log.close(code == 0, code)
    return code


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
    ap.add_argument("--libsre", type=Path, metavar="libsre.prx",
                    help="your decrypted libsre.prx: lift Sony's SPURS library and run it "
                         "instead of the HLE one (see scripts/lift_libsre.py)")
    ap.add_argument("--raw-spu", default="", metavar="[NAME=]VADDR:SIZE[@BASE[/ENTRY]],...",
                    help="raw SPU policy modules to lift (see scripts/lift_spu.py); "
                         "inFamous (US): pm_wwsjob=0x69C880:0x2D80@0xA00/0x2BF0")
    ap.add_argument("--reference-port", default="", metavar="DIR",
                    help="a finished port of the game (e.g. an inFAMOUSreborn clone): use its "
                         "toolkit, patches and job-module entry points")
    ap.add_argument("--address-map", default="", metavar="FILE",
                    help="the reference port's PPU addresses -> this game's (see "
                         "scripts/apply_port_patches.py)")
    ap.add_argument("--no-spu", action="store_true",
                    help="don't lift the SPU programs embedded in the executable")
    ap.add_argument("--generator", choices=list(GENERATORS), default=s.generator)
    ap.add_argument("--config", default=s.build_type, help="build type (default: %(default)s)")
    ap.add_argument("--jobs", type=int, default=s.jobs,
                    help="parallel compiles (default: one per CPU thread); lower it if "
                         "the build runs out of memory")
    ap.add_argument("--zip-logs", type=Path, metavar="FILE.zip",
                    help="also pack this run's step logs into FILE.zip")
    args = ap.parse_args()

    s.work_dir, s.chunks, s.generator, s.build_type = str(args.work), args.chunks, args.generator, args.config
    s.jobs = args.jobs
    if args.reference_port:
        s.reference_port = str(Path(args.reference_port).resolve())
        s.toolkit_dir = reference_port_files(s.reference_port).get("toolkit", s.toolkit_dir)
    if args.address_map:
        s.address_map = str(Path(args.address_map).resolve())
    if not (s.tools / "ppu_lifter.py").exists():
        sys.exit("ps3recomp submodule is missing: run `git submodule update --init`")
    if not args.input.is_file():
        sys.exit(f"no such file: {args.input}")
    if detect_format(args.input) == FMT_UNKNOWN:
        sys.exit(f"{args.input} is neither an ELF nor a SELF")

    project = Project(eboot=str(args.input.resolve()), game_root=guess_game_root(args.input),
                      port_name=args.name, lift_spu=not args.no_spu,
                      libsre=str(args.libsre.resolve()) if args.libsre else "",
                      raw_spu=args.raw_spu)
    if args.clean and s.recompiled_dir.exists():
        shutil.rmtree(s.recompiled_dir)
    s.out_dir.mkdir(parents=True, exist_ok=True)

    steps = recompile_steps(project, s) + (build_steps(project, s) if args.build else [])
    log_dir = new_log_dir(s, args.name)
    print(f"Logs: {log_dir}")
    if args.zip_logs:
        import atexit
        atexit.register(lambda: print(f"Logs packed into {zip_logs(log_dir, args.zip_logs)}"))
    for i, step in enumerate(steps):
        print(f"\n> {step.title}\n+ {quote_argv(step.argv)}", flush=True)
        if run_logged(step, log_dir / step_log_name(i, step)) != 0:
            if step.key == "unwrap" and detect_format(args.input) == FMT_SELF:
                sys.exit("\nThis SELF could not be unwrapped without keys, so it is probably a\n"
                         "retail (encrypted) EBOOT. Decrypt it with RPCS3: Utilities > Decrypt\n"
                         "PS3 Binaries, then run this script on the EBOOT.elf it writes.")
            sys.exit(f"\n{step.title} failed; see the output above or {log_dir / step_log_name(i, step)}.")

    if args.build:
        argv, env = launch_argv(project, s)
        print(f"\nBuilt {executable_path(project, s)}. Run it with:\n  {' '.join(argv)}"
              + (f"\n  (with PS3_VFS_ROOT={env['PS3_VFS_ROOT']})" if env else ""))
    else:
        print(f"\nLifted into {s.recompiled_dir}. Build with --build, or open PS3 Recomp Studio.")


if __name__ == "__main__":
    main()
