"""Small building blocks shared by the pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QIntValidator, QTextCursor
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon, IconWidget, LineEdit,
                            PlainTextEdit, PushButton, ScrollArea, SimpleCardWidget,
                            StrongBodyLabel, TitleLabel, ToolButton)


class Page(ScrollArea):
    """A scrolling page with a title, a subtitle and a vertical stack of cards."""

    def __init__(self, object_name: str, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.layout_ = QVBoxLayout(self.view)
        self.layout_.setContentsMargins(36, 28, 36, 28)
        self.layout_.setSpacing(14)
        self.layout_.addWidget(TitleLabel(title, self.view))
        sub = BodyLabel(subtitle, self.view)
        sub.setWordWrap(True)
        sub.setTextColor("#606060", "#a0a0a0")
        self.layout_.addWidget(sub)
        self.layout_.addSpacing(6)
        self.setWidget(self.view)
        # Let the Fluent window's background show through.
        self.setStyleSheet("QScrollArea{border:none;background:transparent}")
        self.view.setStyleSheet("QWidget#view{background:transparent}")

    def add(self, widget: QWidget) -> QWidget:
        self.layout_.addWidget(widget)
        return widget

    def finish(self) -> None:
        self.layout_.addStretch(1)


class Card(SimpleCardWidget):
    """A titled card; put content in `.body`."""

    def __init__(self, title: str, icon: FluentIcon | None = None, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(10)
        if icon is not None:
            iw = IconWidget(icon, self)
            iw.setFixedSize(18, 18)
            head.addWidget(iw)
        head.addWidget(StrongBodyLabel(title, self))
        head.addStretch(1)
        self.header = head
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)


class Field(QWidget):
    """Label on the left, an editor (or any widget) on the right."""

    def __init__(self, label: str, editor: QWidget, hint: str = "", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(BodyLabel(label, self))
        if hint:
            h = CaptionLabel(hint, self)
            h.setTextColor("#707070", "#9a9a9a")
            h.setWordWrap(True)
            left.addWidget(h)
        lw = QWidget(self)
        lw.setLayout(left)
        lw.setFixedWidth(230)
        row.addWidget(lw)
        row.addWidget(editor, 1)


class PathPicker(QWidget):
    """A line edit with a browse button. mode: 'file', 'dir' or 'save'."""

    changed = Signal(str)

    def __init__(self, mode: str = "file", file_filter: str = "All files (*)",
                 placeholder: str = "", parent=None):
        super().__init__(parent)
        self.mode, self.file_filter = mode, file_filter
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.edit = LineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.text()))
        btn = ToolButton(FluentIcon.FOLDER, self)
        btn.setToolTip("Browse...")
        btn.clicked.connect(self._browse)
        row.addWidget(self.edit, 1)
        row.addWidget(btn)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def _browse(self) -> None:
        start = self.text()
        if self.mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose file", start, self.file_filter)
        if path:
            self.setText(path)
            self.changed.emit(path)


class IntEdit(LineEdit):
    """A number box. (qfluentwidgets' SpinBox draws no text on some Qt 6 builds.)"""

    valueChanged = Signal(int)

    def __init__(self, lo: int, hi: int, value: int, suffix: str = "", parent=None):
        super().__init__(parent)
        self.lo, self.hi = lo, hi
        self.setValidator(QIntValidator(lo, hi, self))
        self.setFixedWidth(140)
        if suffix:
            self.setPlaceholderText(suffix)
            self.setToolTip(suffix)
        self.setText(str(value))
        self.editingFinished.connect(lambda: self.valueChanged.emit(self.value()))

    def value(self) -> int:
        try:
            return max(self.lo, min(self.hi, int(self.text())))
        except ValueError:
            return self.lo


class LogView(PlainTextEdit):
    """Read-only console output."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(20000)
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "monospace"])
        font.setPointSize(9)
        self.setFont(font)
        self.setMinimumHeight(260)

    def append_text(self, text: str) -> None:
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.insertPlainText(text)
        self.moveCursor(QTextCursor.MoveOperation.End)


def hline(parent=None) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(128,128,128,0.25)")
    return line


def button(text: str, icon: FluentIcon, slot, parent=None, cls=PushButton):
    b = cls(icon, text, parent)
    b.clicked.connect(slot)
    return b
