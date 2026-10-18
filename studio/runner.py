"""Run a list of pipeline Steps one after another with QProcess, streaming output."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .pipeline import Step


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

    @property
    def running(self) -> bool:
        return self._proc is not None

    def run(self, steps: list[Step], env: dict[str, str] | None = None) -> None:
        if self.running:
            return
        self._steps, self._env, self._index, self._cancelled = list(steps), dict(env or {}), -1, False
        self._next()

    def cancel(self) -> None:
        if self._proc is not None:
            self._cancelled = True
            self.output.emit("\n[studio] cancelling...\n")
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
        for k, v in self._env.items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if step.cwd:
            proc.setWorkingDirectory(step.cwd)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._done)
        proc.errorOccurred.connect(self._error)
        self._proc = proc
        self._started = time.monotonic()
        self.stepStarted.emit(self._index)
        self.output.emit(f"\n> {step.title}\n$ {' '.join(_quote(a) for a in step.argv)}\n")
        proc.start(step.argv[0], step.argv[1:])

    def _read(self) -> None:
        if self._proc is not None:
            data = bytes(self._proc.readAllStandardOutput())
            self.output.emit(data.decode("utf-8", "replace"))

    def _error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart and self._proc is not None:
            self.output.emit(f"[studio] could not start {self._steps[self._index].argv[0]!r}: "
                             f"{self._proc.errorString()}\n")
            self._finish_step(False)

    def _done(self, code: int, status) -> None:
        ok = status == QProcess.ExitStatus.NormalExit and code == 0 and not self._cancelled
        if not ok and not self._cancelled:
            self.output.emit(f"[studio] {self._steps[self._index].title} failed (exit code {code})\n")
        self._finish_step(ok)

    def _finish_step(self, ok: bool) -> None:
        if self._proc is None:
            return
        self._read()
        proc, self._proc = self._proc, None
        proc.deleteLater()
        self.stepFinished.emit(self._index, ok, time.monotonic() - self._started)
        if ok:
            self._proc = None
            self._next()
        else:
            self.finished.emit(False)


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg
