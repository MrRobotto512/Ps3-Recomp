# Ps3-Recomp

Recompile PlayStation 3 games into native Windows programs.

This repository is a **port workspace** built on
[ps3recomp](https://github.com/sp00nznet/ps3recomp), the open-source PS3 static
recompilation toolkit (included as a git submodule). It adds **PS3 Recomp
Studio**, a Windows 11–style app that runs the entire pipeline for you:
decrypt → lift → build → launch.

> You must supply your own legally obtained game files. Nothing from a game,
> and no Sony keys or firmware, is in this repository. The `.gitignore` keeps
> EBOOTs, ELFs and the generated code out of git.

## How it works

A PS3 game's code is PowerPC (the Cell's PPU) plus small SPU programs. Instead
of emulating them like RPCS3, ps3recomp **translates them ahead of time**:

| Step | Tool | Result |
|------|------|--------|
| Decrypt | RPCS3 (*Utilities › Decrypt PS3 Binaries*) | `EBOOT.elf` |
| Load | `ps3recomp/tools/ppu_loader.py` | function list, firmware imports, TOC |
| Lift | `ps3recomp/tools/ppu_lifter.py` | `recompiled/` — the game as C++ |
| Build | CMake + Visual Studio | `build/Release/<Game>.exe`, linked with the ps3recomp runtime (LV2 kernel, ~90 system libraries, RSX → Direct3D 12) |

Getting a lift to *compile* is the easy part. Getting a game to *run* usually
means fixing whatever the runtime does not handle yet for that title: a missing
HLE function, an SPU job, a graphics feature. Read ps3recomp's
[Game Porting Guide](ps3recomp/docs/GAME_PORTING_GUIDE.md); two games are
currently in-game with it (see its README).

## Setup (Windows)

Install:

1. [Git](https://git-scm.com/download/win) and [Python 3.10+](https://www.python.org/downloads/) (tick *Add to PATH*).
2. [Visual Studio 2022](https://visualstudio.microsoft.com/) (Community is fine) with
   **Desktop development with C++** and the **C++ Clang tools for Windows** component.
   CMake comes with it; a standalone [CMake 3.20+](https://cmake.org/download/) also works.
3. [RPCS3](https://rpcs3.net/) and the PS3 firmware, for decrypting your games.

Then:

```bat
git clone --recursive https://github.com/MrRobotto512/Ps3-Recomp.git
cd Ps3-Recomp
PS3RecompStudio.bat
```

The first run creates a `.venv` and installs the dependencies.

## Using PS3 Recomp Studio

1. **RPCS3 page:** set `rpcs3.exe`, decrypt your `EBOOT.BIN` there, then click
   *Use decrypted EBOOT.elf…*.
2. **Game page:** the title and ID are read from `PARAM.SFO`. Check the game
   folder (the one containing `PS3_GAME/`) and the port name. *Lift the SPU
   programs* (on by default) lifts the SPU code embedded in the EBOOT during
   Configure; games that use SPURS hang without it.
3. **Recompile page:** *Recompile & build*, then *Launch*. All tool output
   streams into the log.
4. **Settings:** compiler (clang-cl or MSVC), Release/RelWithDebInfo/Debug,
   parallel compiles (lower it if a build runs out of memory),
   theme.

### SPURS games: lifting libsre

Games that schedule their work with SPURS (inFamous is one) hang on
ps3recomp's built-in SPURS. They need Sony's real one, `libsre`, lifted to C++
the same way as the game ([ps3recomp/docs/FIRMWARE_LLE.md](ps3recomp/docs/FIRMWARE_LLE.md)):

1. With the PS3 firmware installed in RPCS3, decrypt
   `dev_flash/sys/external/libsre.sprx` (*Utilities › Decrypt PS3 Binaries*).
   That gives `libsre.prx`.
2. Pick it in **libsre.prx** on the Game page. Recompile then gets a *Lift
   libsre* step, which writes `prx/`, and the build includes it.
3. The log should show `[prx] libsre: 440 lifted functions registered`. Set
   `PS3_NO_LIBSRE=1` in *Launch environment* to compare with the built-in SPURS.

`libsre` is firmware. `prx/` and `*.prx` are git-ignored; keep them that way,
and don't share a build that contains them.

*Settings › Running the port* also has the toolkit's **diagnostic runner** (a
watchdog that prints every guest thread when the game stops making progress)
and **Launch environment**, e.g. `RSX_LIVE_DRAW=1` for the live D3D12
renderer.

### Logs

Every run saves each step's output to its own file, with the command, start
time, duration and exit code: `logs/<date-time>-<port name>/01-load.log`,
`02-lift.log`, `03-configure.log` and so on (launches and RPCS3 snapshots get
their own folders too). These are complete, unlike the on-screen log, which
keeps only the last 20,000 lines.

- The **save** button at the end of each step row exports that step's log.
- **Export logs** on the Output card saves the whole run as one `.zip`, which
  you can attach to a bug report. **Logs folder** opens `logs/`.
- On the RPCS3 page, **Export log** saves the last snapshot's output.

The RPCS3 page can also boot the original game for comparison and take a
**memory snapshot** (registers, call stack, memory → JSON) through
`ps3recomp/tools/rpcs3_probe`. That requires an RPCS3 build with its GDB server
enabled, which is a compile-time option.

### Packaging the app as an .exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\package_windows.ps1
```

This produces `dist\PS3 Recomp Studio\PS3 Recomp Studio.exe`. Keep it inside
this repository: it finds the toolkit by searching upward, and it still uses
Python, CMake and Visual Studio from the PC.

## Command line

The same pipeline without the GUI:

```bat
python scripts\recomp.py path\to\PS3_GAME\USRDIR\EBOOT.elf --build --name MyGame
build\Release\MyGame.exe path\to\PS3_GAME\USRDIR\EBOOT.elf
```

The CLI writes the same per-step logs and prints where they are. Add
`--zip-logs run.zip` to pack them into one file as well.

## Repository layout

| Path | What |
|------|------|
| `ps3recomp/` | the toolkit (submodule, pinned) |
| `studio/` | PS3 Recomp Studio. `pipeline.py` holds the commands and has no Qt; `ui/` is the interface. |
| `scripts/recomp.py` | CLI for the same pipeline |
| `CMakeLists.txt`, `main.cpp`, `stubs.cpp`, `config.toml` | the port project, from ps3recomp's `templates/project`. Per-game fixes go in `stubs.cpp`. |
| `prx_boot.cpp`, `scripts/lift_libsre.py` | lifted-libsre support (see above) |
| `tests/` | `python -m pytest tests` |

Update the toolkit with `git submodule update --remote ps3recomp`.

## Licences

ps3recomp is MIT. The app's UI uses
[PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets), which
is GPLv3 for non-commercial use. If you distribute the packaged app, GPLv3
applies to it.
