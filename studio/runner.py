"""Run a list of pipeline Steps one after another with QProcess, streaming output.

With a log folder, each step's output is also saved to its own file there.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .pipeline import Step, StepLog, quote_argv, step_log_name


class StepRunner(QObject):
    stepStarted = Signal(int)            # index into the step list
    stepFinished = Signal(int, bool, float)  # index, ok, seconds
    output = Signal(str)
    finished = Signal(bool)              # True if every step succeeded

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._steps: list[Step] = []
        self._env: dict[str, str] = {}
        self._index = -1
        self._started = 0.0
        self._cancelled = False
        self._code: int | None = None
        self._log: StepLog | None = None
        self.log_dir: Path | None = None
        self.log_paths: list[Path | None] = []   # per step, once it has started

    @property
    def running(self) -> bool:
        return self._proc is not None

    def run(self, steps: list[Step], env: dict[str, str] | None = None,
            log_dir: Path | None = None) -> None:
        if self.running:
            return
        self.log_dir = log_dir
        self.log_paths = [None] * len(steps)
        self._steps, self._env, self._index, self._cancelled = list(steps), dict(env or {}), -1, False
        self._next()

    def cancel(self) -> None:
        if self._proc is not None:
            self._cancelled = True
            self._emit("\n[studio] cancelling...\n")
            self._proc.kill()

    def _next(self) -> None:
        self._index += 1
        if self._index >= len(self._steps):
            self._proc = None
            self.finished.emit(True)
            return
        step = self._steps[self._index]
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        for k, v in {**step.env, **self._env}.items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if step.cwd:
            proc.setWorkingDirectory(step.cwd)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._done)
        proc.errorOccurred.connect(self._error)
        self._proc = proc
        self._started, self._code = time.monotonic(), None
        if self.log_dir is not None:
            try:
                self._log = StepLog(self.log_dir / step_log_name(self._index, step), step)
                self.log_paths[self._index] = self._log.path
            except OSError as e:
                self.output.emit(f"[studio] could not write the log: {e}\n")
        self.stepStarted.emit(self._index)
        self.output.emit(f"\n> {step.title}\n$ {quote_argv(step.argv)}\n")
        proc.start(step.argv[0], step.argv[1:])

    def _read(self) -> None:
        if self._proc is not None:
            data = bytes(self._proc.readAllStandardOutput())
            self._emit(data.decode("utf-8", "replace"))

    def _error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart and self._proc is not None:
            reason = self._proc.errorString()
            self._emit(f"[studio] could not start {self._steps[self._index].argv[0]!r}: {reason}\n")
            if "Application Control" in reason:
                self._emit("[studio] Windows blocked this program, not the port: usually Smart App "
                           "Control, which can refuse a freshly built unsigned .exe. Try again, and "
                           "see Windows Security > Protection history for what blocked it.\n")
            self._finish_step(False)

    def _done(self, code: int, status) -> None:
        self._code = code
        ok = status == QProcess.ExitStatus.NormalExit and code == 0 and not self._cancelled
        if not ok and not self._cancelled:
            self._emit(f"[studio] {self._steps[self._index].title} failed (exit code {code})\n")
        self._finish_step(ok)

    def _finish_step(self, ok: bool) -> None:
        if self._proc is None:
            return
        self._read()
        proc, self._proc = self._proc, None
        if self._log is not None:
            self._log.close(ok, self._code, self._cancelled)
            self._log = None
        proc.deleteLater()
        self.stepFinished.emit(self._index, ok, time.monotonic() - self._started)
        if ok:
            self._proc = None
            self._next()
        else:
            self.finished.emit(False)

    def _emit(self, text: str) -> None:
        if self._log is not None:
            self._log.write(text)
        self.output.emit(text)
