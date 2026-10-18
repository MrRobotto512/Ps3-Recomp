"""The four pages: Game, Recompile, RPCS3 and Settings."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentIcon, IconWidget,
                            IndeterminateProgressBar, InfoBadge, InfoBar, InfoBarIcon,
                            InfoBarPosition, LineEdit, PrimaryPushButton, PushButton,
                            StrongBodyLabel, Theme, setTheme)

from .. import config, rpcs3
from ..pipeline import (FMT_ELF, FMT_SELF, GENERATORS, Project, Settings, build_steps,
                        detect_format, executable_path, find_cmake, find_param_sfo, guess_game_root,
                        launch_argv, recompile_steps, sanitize_name, Step)
from ..runner import StepRunner
from ..sfo import read_sfo
from .widgets import Card, Field, IntEdit, LogView, Page, PathPicker, button, hline


class State(QObject):
    """Settings + project shared by every page; saved whenever it changes."""

    changed = Signal()

    def __init__(self, settings: Settings, project: Project):
        super().__init__()
        self.settings, self.project = settings, project

    def touch(self) -> None:
        try:
            config.save(self.settings, self.project)
        except OSError:
            pass
        self.changed.emit()


def _left(widget: QWidget) -> QWidget:
    """Keep a narrow editor at the left of its Field instead of stretched."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(widget)
    row.addStretch(1)
    return box


def toast(parent: QWidget, kind: str, title: str, text: str) -> None:
    fn = {"ok": InfoBar.success, "warn": InfoBar.warning, "err": InfoBar.error}.get(kind, InfoBar.info)
    fn(title, text, parent=parent.window(), position=InfoBarPosition.TOP_RIGHT,
       duration=5000 if kind != "err" else 8000)


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------

class GamePage(Page):
    def __init__(self, state: State, parent=None):
        super().__init__("game", "Game",
                         "Choose the PS3 executable to recompile. Use your own game: "
                         "a decrypted EBOOT.elf, or an EBOOT.BIN from a debug build.", parent)
        self.state = state

        card = self.add(Card("Executable", FluentIcon.GAME, self.view))
        self.eboot = PathPicker("file", "PS3 executables (*.BIN *.bin *.elf *.ELF *.self *.SELF);;All files (*)",
                                "…/PS3_GAME/USRDIR/EBOOT.elf")
        self.eboot.changed.connect(self._eboot_changed)
        card.body.addWidget(Field("EBOOT", self.eboot, "EBOOT.elf or EBOOT.BIN"))
        self.fmt_badge = InfoBadge.custom("No file", "#8a8a8a", "#8a8a8a", parent=card)
        card.header.addWidget(self.fmt_badge)
        self.fmt_text = CaptionLabel("", card)
        self.fmt_text.setWordWrap(True)
        card.body.addWidget(self.fmt_text)

        info = self.add(Card("Details", FluentIcon.INFO, self.view))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.meta = {}
        for i, (key, label) in enumerate((("TITLE", "Title"), ("TITLE_ID", "Title ID"),
                                          ("APP_VER", "Version"), ("CATEGORY", "Category"))):
            grid.addWidget(CaptionLabel(label, info), i, 0)
            v = BodyLabel("—", info)
            grid.addWidget(v, i, 1)
            self.meta[key] = v
        grid.setColumnStretch(1, 1)
        info.body.addLayout(grid)
        info.body.addWidget(hline(info))
        self.root = PathPicker("dir", placeholder="Folder that contains PS3_GAME/")
        self.root.changed.connect(self._root_changed)
        info.body.addWidget(Field("Game folder", self.root,
                                  "Where the port reads its data files (/dev_hdd0, USRDIR...)"))
        self.name = LineEdit(info)
        self.name.setPlaceholderText("PS3Port")
        self.name.editingFinished.connect(self._name_changed)
        info.body.addWidget(Field("Port name", self.name, "Name of the .exe that gets built"))

        self.finish()
        self.refresh()

    def refresh(self) -> None:
        p = self.state.project
        self.eboot.setText(p.eboot)
        self.root.setText(p.game_root)
        self.name.setText(p.port_name)
        self._show_format()
        self._show_meta()

    def _show_format(self) -> None:
        path = self.state.project.eboot
        if not path:
            self._badge("No file", "#8a8a8a")
            self.fmt_text.setText("")
            return
        if not os.path.isfile(path):
            self._badge("Missing", "#c42b1c")
            self.fmt_text.setText("That file does not exist.")
            return
        fmt = detect_format(path)
        if fmt == FMT_ELF:
            self._badge("ELF", "#0f7b0f")
            self.fmt_text.setText("Decrypted ELF — ready to recompile.")
        elif fmt == FMT_SELF:
            self._badge("SELF", "#9d5d00")
            self.fmt_text.setText(
                "A SELF. Debug/prototype fSELFs are unwrapped automatically. A retail EBOOT "
                "is encrypted: decrypt it on the RPCS3 page first, then pick the EBOOT.elf.")
        else:
            self._badge("Unknown", "#c42b1c")
            self.fmt_text.setText("Not an ELF or SELF file.")

    def _badge(self, text: str, color: str) -> None:
        self.fmt_badge.setText(text)
        self.fmt_badge.setCustomBackgroundColor(color, color)

    def _show_meta(self) -> None:
        values = {}
        if self.state.project.eboot:
            sfo = find_param_sfo(self.state.project.eboot)
            if sfo:
                try:
                    values = read_sfo(sfo)
                except (OSError, ValueError):
                    pass
        for key, label in self.meta.items():
            label.setText(str(values.get(key, "—")))
        return values

    def _eboot_changed(self, path: str) -> None:
        p = self.state.project
        if path == p.eboot:
            return
        p.eboot = path
        if path and os.path.isfile(path):
            p.game_root = guess_game_root(path)
            sfo = find_param_sfo(path)
            try:
                meta = read_sfo(sfo) if sfo else {}
            except (OSError, ValueError):
                meta = {}
            if meta.get("TITLE"):
                p.port_name = sanitize_name(str(meta["TITLE"]))
        self.state.touch()
        self.refresh()

    def _root_changed(self, path: str) -> None:
        self.state.project.game_root = path
        self.state.touch()

    def _name_changed(self) -> None:
        name = sanitize_name(self.name.text()) if self.name.text().strip() else "PS3Port"
        self.name.setText(name)
        if name != self.state.project.port_name:
            self.state.project.port_name = name
            self.state.touch()


# ---------------------------------------------------------------------------
# Recompile
# ---------------------------------------------------------------------------

class StepRow(QWidget):
    ICONS = {"idle": FluentIcon.REMOVE, "run": FluentIcon.SYNC, "ok": InfoBarIcon.SUCCESS,
             "fail": InfoBarIcon.ERROR, "skip": FluentIcon.REMOVE}

    def __init__(self, step: Step, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(14)
        self.icon = IconWidget(FluentIcon.REMOVE, self)
        self.icon.setFixedSize(18, 18)
        row.addWidget(self.icon)
        text = QWidget(self)
        col = QHBoxLayout(text)
        col.setContentsMargins(0, 0, 0, 0)
        title = StrongBodyLabel(step.title, text)
        title.setFixedWidth(150)
        col.addWidget(title)
        desc = CaptionLabel(step.description, text)
        desc.setTextColor("#606060", "#a0a0a0")
        col.addWidget(desc, 1)
        row.addWidget(text, 1)
        self.status = CaptionLabel("", self)
        row.addWidget(self.status)

    def set_state(self, state: str, note: str = "") -> None:
        self.icon.setIcon(self.ICONS[state])
        self.status.setText(note)


class RecompilePage(Page):
    def __init__(self, state: State, parent=None):
        super().__init__("recompile", "Recompile",
                         "Translate the game's PowerPC code to C++, compile it with the "
                         "ps3recomp runtime, and run it as a native Windows program.", parent)
        self.state = state
        self.runner = StepRunner(self)
        self.runner.stepStarted.connect(self._step_started)
        self.runner.stepFinished.connect(self._step_finished)
        self.runner.output.connect(lambda t: self.log.append_text(t))
        self.runner.finished.connect(self._finished)
        self.rows: list[StepRow] = []
        self.active: list[int] = []   # row index for each running step

        actions = QWidget(self.view)
        ar = QHBoxLayout(actions)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(8)
        self.btn_all = button("Recompile && build", FluentIcon.PLAY, self.run_all, actions, PrimaryPushButton)
        self.btn_lift = button("Lift only", FluentIcon.CODE, self.run_lift, actions)
        self.btn_build = button("Build only", FluentIcon.DEVELOPER_TOOLS, self.run_build, actions)
        self.btn_launch = button("Launch", FluentIcon.GAME, self.launch, actions)
        self.btn_cancel = button("Cancel", FluentIcon.CLOSE, self.runner.cancel, actions)
        for b in (self.btn_all, self.btn_lift, self.btn_build, self.btn_launch):
            ar.addWidget(b)
        ar.addStretch(1)
        ar.addWidget(self.btn_cancel)
        self.add(actions)

        self.steps_card = self.add(Card("Pipeline", FluentIcon.IOT, self.view))
        self.progress = IndeterminateProgressBar(self.steps_card, start=False)
        self.progress.setVisible(False)
        self.steps_card.body.addWidget(self.progress)
        self.steps_box = QWidget(self.steps_card)
        self.steps_layout = QVBoxLayout(self.steps_box)
        self.steps_layout.setContentsMargins(0, 0, 0, 0)
        self.steps_layout.setSpacing(0)
        self.steps_card.body.addWidget(self.steps_box)

        log_card = self.add(Card("Output", FluentIcon.COMMAND_PROMPT, self.view))
        log_card.header.addWidget(button("Copy", FluentIcon.COPY, self._copy_log, log_card))
        log_card.header.addWidget(button("Clear", FluentIcon.DELETE, lambda: self.log.clear(), log_card))
        self.log = LogView(log_card)
        log_card.body.addWidget(self.log)
        self.finish()

        state.changed.connect(self.rebuild_rows)
        self.rebuild_rows()
        self._set_busy(False)

    # -- step list ---------------------------------------------------------

    def _all_steps(self) -> list[Step]:
        p, s = self.state.project, self.state.settings
        return recompile_steps(p, s) + build_steps(p, s)

    def rebuild_rows(self) -> None:
        if self.runner.running:
            return
        for r in self.rows:
            r.deleteLater()
        self.rows = []
        for step in self._all_steps():
            row = StepRow(step, self.steps_box)
            self.steps_layout.addWidget(row)
            self.rows.append(row)
        self._update_launch()

    def _update_launch(self) -> None:
        exists = executable_path(self.state.project, self.state.settings).is_file()
        self.btn_launch.setEnabled(exists and not self.runner.running)
        self.btn_launch.setToolTip("" if exists else "Build the port first")

    # -- actions -----------------------------------------------------------

    def _check_ready(self) -> bool:
        p, s = self.state.project, self.state.settings
        if not p.eboot or not os.path.isfile(p.eboot):
            toast(self, "warn", "No game selected", "Pick an EBOOT on the Game page first.")
            return False
        if not (s.tools / "ppu_lifter.py").is_file():
            toast(self, "err", "ps3recomp not found",
                  f"No toolkit at {s.toolkit_dir}. Run `git submodule update --init` "
                  "or fix the path in Settings.")
            return False
        return True

    def run_all(self) -> None:
        self._start(range(len(self.rows)))

    def run_lift(self) -> None:
        self._start(range(len(recompile_steps(self.state.project, self.state.settings))))

    def run_build(self) -> None:
        n = len(recompile_steps(self.state.project, self.state.settings))
        if not (self.state.settings.recompiled_dir / "ppu_recomp.h").is_file():
            toast(self, "warn", "Nothing lifted yet", "Run “Lift only” or “Recompile & build” first.")
            return
        self._start(range(n, len(self.rows)))

    def _start(self, indices) -> None:
        if self.runner.running or not self._check_ready():
            return
        n_lift = len(recompile_steps(self.state.project, self.state.settings))
        if max(indices, default=-1) >= n_lift and not find_cmake(self.state.settings.cmake):
            toast(self, "err", "CMake not found",
                  "Install Visual Studio 2022 with “Desktop development with C++” (it includes "
                  "CMake), or install CMake from cmake.org, or set its path in Settings.")
            return
        self.rebuild_rows()
        steps = self._all_steps()
        self.active = list(indices)
        for i, r in enumerate(self.rows):
            r.set_state("idle" if i in self.active else "skip", "" if i in self.active else "skipped")
        self.log.append_text(f"\n=== {self.state.project.port_name} ===\n")
        self._set_busy(True)
        self.runner.run([steps[i] for i in self.active])

    def launch(self) -> None:
        if self.runner.running:
            return
        argv, env = launch_argv(self.state.project, self.state.settings)
        self.active = []
        self._set_busy(True)
        self.log.append_text("\n=== launch ===\n")
        self.runner.run([Step("launch", "Launch", "", argv, cwd=str(Path(argv[0]).parent))], env)

    # -- runner callbacks --------------------------------------------------

    def _row(self, i: int) -> StepRow | None:
        if i < len(self.active):
            return self.rows[self.active[i]]
        return None

    def _step_started(self, i: int) -> None:
        if (r := self._row(i)):
            r.set_state("run", "running…")

    def _step_finished(self, i: int, ok: bool, secs: float) -> None:
        if (r := self._row(i)):
            r.set_state("ok" if ok else "fail", f"{secs:.1f} s")

    def _finished(self, ok: bool) -> None:
        self._set_busy(False)
        if not self.active:
            return
        if ok:
            toast(self, "ok", "Done", f"{self.state.project.port_name} is ready — press Launch.")
        else:
            toast(self, "err", "A step failed", "See the output below for details.")

    def _set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.progress.start() if busy else self.progress.stop()
        for b in (self.btn_all, self.btn_lift, self.btn_build):
            b.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self._update_launch()

    def _copy_log(self) -> None:
        QGuiApplication.clipboard().setText(self.log.toPlainText())
        toast(self, "info", "Copied", "Output copied to the clipboard.")


# ---------------------------------------------------------------------------
# RPCS3
# ---------------------------------------------------------------------------

class Rpcs3Page(Page):
    def __init__(self, state: State, parent=None):
        super().__init__("rpcs3", "RPCS3",
                         "RPCS3 is the reference emulator. Use it to decrypt your game, "
                         "to see how the original behaves, and to snapshot its memory.", parent)
        self.state = state

        where = self.add(Card("RPCS3 location", FluentIcon.APPLICATION, self.view))
        self.path = PathPicker("file", "RPCS3 (rpcs3.exe rpcs3);;All files (*)", "C:/RPCS3/rpcs3.exe")
        self.path.setText(state.settings.rpcs3)
        self.path.changed.connect(self._path_changed)
        where.body.addWidget(Field("rpcs3.exe", self.path, "Get it from rpcs3.net"))

        dec = self.add(Card("Decrypt a retail EBOOT", FluentIcon.FINGERPRINT, self.view))
        steps = BodyLabel(
            "1.  Install the PS3 firmware in RPCS3 (File › Install Firmware).\n"
            "2.  In RPCS3 choose Utilities › Decrypt PS3 Binaries and pick your EBOOT.BIN.\n"
            "     PSN titles also need their .rap license file in dev_hdd0/home/00000001/exdata.\n"
            "3.  RPCS3 writes EBOOT.elf next to it. Select that file below.", dec)
        steps.setWordWrap(True)
        dec.body.addWidget(steps)
        row = QHBoxLayout()
        row.addWidget(button("Open RPCS3", FluentIcon.LINK, self._open_rpcs3, dec))
        row.addWidget(button("Use decrypted EBOOT.elf…", FluentIcon.DOCUMENT, self._pick_elf, dec,
                             PrimaryPushButton))
        row.addStretch(1)
        dec.body.addLayout(row)

        cmp_ = self.add(Card("Compare with the original", FluentIcon.GAME, self.view))
        cmp_.body.addWidget(CaptionLabel("Boots the selected EBOOT in RPCS3 so you can check what the "
                                         "port should look like.", cmp_))
        row = QHBoxLayout()
        row.addWidget(button("Boot in RPCS3", FluentIcon.PLAY, self._boot, cmp_))
        row.addStretch(1)
        cmp_.body.addLayout(row)

        snap = self.add(Card("Memory snapshot", FluentIcon.ZOOM, self.view))
        note = CaptionLabel(
            "Boots the game in RPCS3, pauses it after the delay and saves registers, the call stack "
            f"and memory to JSON in out/. Needs an RPCS3 build with its GDB server on port "
            f"{rpcs3.GDB_PORT} — that is a build option, so you may have to compile RPCS3 yourself.", snap)
        note.setWordWrap(True)
        snap.body.addWidget(note)
        self.delay = IntEdit(1, 600, 12, "seconds", snap)
        snap.body.addWidget(Field("Pause after (seconds)", _left(self.delay)))
        row = QHBoxLayout()
        self.btn_snap = button("Capture snapshot", FluentIcon.CAMERA, self._snapshot, snap, PrimaryPushButton)
        row.addWidget(self.btn_snap)
        row.addWidget(button("Open folder", FluentIcon.FOLDER, self._open_out, snap))
        row.addStretch(1)
        snap.body.addLayout(row)
        self.log = LogView(snap)
        self.log.setMinimumHeight(160)
        snap.body.addWidget(self.log)

        self.runner = StepRunner(self)
        self.runner.output.connect(lambda t: self.log.append_text(t))
        self.runner.finished.connect(self._snap_done)
        self._snap_out: Path | None = None
        self.finish()

    def _path_changed(self, path: str) -> None:
        self.state.settings.rpcs3 = path
        self.state.touch()

    def _have_rpcs3(self) -> bool:
        if self.state.settings.rpcs3 and os.path.isfile(self.state.settings.rpcs3):
            return True
        toast(self, "warn", "RPCS3 not set", "Choose rpcs3.exe first.")
        return False

    def _have_game(self) -> bool:
        if self.state.project.eboot and os.path.isfile(self.state.project.eboot):
            return True
        toast(self, "warn", "No game selected", "Pick an EBOOT on the Game page first.")
        return False

    def _open_rpcs3(self) -> None:
        if self._have_rpcs3():
            QProcess.startDetached(self.state.settings.rpcs3, [])

    def _boot(self) -> None:
        if self._have_rpcs3() and self._have_game():
            argv = rpcs3.boot_argv(self.state.project, self.state.settings)
            QProcess.startDetached(argv[0], argv[1:])

    def _pick_elf(self) -> None:
        start = os.path.dirname(self.state.project.eboot) if self.state.project.eboot else ""
        path, _ = QFileDialog.getOpenFileName(self, "Decrypted EBOOT", start,
                                              "ELF (*.elf *.ELF);;All files (*)")
        if path:
            window = self.window()
            window.game_page._eboot_changed(path)
            toast(self, "ok", "Game selected", os.path.basename(path))

    def _snapshot(self) -> None:
        if self.runner.running or not (self._have_rpcs3() and self._have_game()):
            return
        s = self.state.settings
        s.out_dir.mkdir(parents=True, exist_ok=True)
        argv, self._snap_out = rpcs3.snapshot_argv(self.state.project, s, self.delay.value())
        self.btn_snap.setEnabled(False)
        self.runner.run([Step("snapshot", "Snapshot", "", argv)])

    def _snap_done(self, ok: bool) -> None:
        self.btn_snap.setEnabled(True)
        if ok and self._snap_out:
            toast(self, "ok", "Snapshot saved", self._snap_out.name)
        else:
            toast(self, "err", "Snapshot failed", "See the log. Is RPCS3's GDB server enabled?")

    def _open_out(self) -> None:
        d = self.state.settings.out_dir
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsPage(Page):
    THEMES = {"auto": ("Follow Windows", Theme.AUTO), "light": ("Light", Theme.LIGHT),
              "dark": ("Dark", Theme.DARK)}

    def __init__(self, state: State, parent=None):
        super().__init__("settings", "Settings", "Where the tools live and how the port is built.", parent)
        self.state = state
        s = state.settings

        tk = self.add(Card("Toolkit", FluentIcon.FOLDER, self.view))
        self.toolkit = self._path(tk, "ps3recomp folder", "dir", s.toolkit_dir, "toolkit_dir",
                                  "The ps3recomp submodule")
        self.work = self._path(tk, "Work folder", "dir", s.work_dir, "work_dir",
                               "out/, recompiled/ and build/ go here")
        self.python = self._path(tk, "Python", "file", s.python, "python", "Runs the lifter (3.10+)")
        self.cmake = self._path(tk, "CMake", "file", s.cmake, "cmake", "3.20+")

        bd = self.add(Card("Build", FluentIcon.DEVELOPER_TOOLS, self.view))
        self.gen = ComboBox(bd)
        for key, (label, _) in GENERATORS.items():
            self.gen.addItem(label, userData=key)
        self.gen.setCurrentIndex(list(GENERATORS).index(s.generator) if s.generator in GENERATORS else 0)
        self.gen.currentIndexChanged.connect(lambda _: self._set("generator", self.gen.currentData()))
        bd.body.addWidget(Field("Compiler", self.gen, "clang-cl needs the “C++ Clang tools” VS component"))
        self.btype = ComboBox(bd)
        self.btype.addItems(["Release", "RelWithDebInfo", "Debug"])
        self.btype.setCurrentText(s.build_type)
        self.btype.currentTextChanged.connect(lambda t: self._set("build_type", t))
        bd.body.addWidget(Field("Configuration", self.btype, "RelWithDebInfo is best for debugging crashes"))
        self.chunks = IntEdit(0, 1024, s.chunks, parent=bd)
        self.chunks.valueChanged.connect(lambda v: self._set("chunks", v))
        bd.body.addWidget(Field("Source chunks", _left(self.chunks),
                                "Lifted code is split into this many files; more = faster rebuilds"))

        ap = self.add(Card("Appearance", FluentIcon.PALETTE, self.view))
        self.theme = ComboBox(ap)
        for key, (label, _) in self.THEMES.items():
            self.theme.addItem(label, userData=key)
        self.theme.setCurrentIndex(list(self.THEMES).index(s.theme) if s.theme in self.THEMES else 0)
        self.theme.currentIndexChanged.connect(self._theme_changed)
        ap.body.addWidget(Field("Theme", self.theme))
        self.finish()

    def _path(self, card, label, mode, value, attr, hint):
        picker = PathPicker(mode)
        picker.setText(value)
        picker.changed.connect(lambda v: self._set(attr, v))
        card.body.addWidget(Field(label, picker, hint))
        return picker

    def _set(self, attr: str, value) -> None:
        setattr(self.state.settings, attr, value)
        self.state.touch()

    def _theme_changed(self, _=None) -> None:
        key = self.theme.currentData()
        self._set("theme", key)
        setTheme(self.THEMES[key][1])
