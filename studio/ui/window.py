"""The main window: Fluent navigation sidebar with the four pages."""

from __future__ import annotations

from PySide6.QtGui import QIcon
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition, Theme, setTheme, setThemeColor

from .. import config
from .pages import GamePage, RecompilePage, Rpcs3Page, SettingsPage, State

ACCENT = "#0067c0"   # Windows 11 default accent


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        settings, project = config.load()
        self.state = State(settings, project)
        setTheme({"light": Theme.LIGHT, "dark": Theme.DARK}.get(settings.theme, Theme.AUTO))
        setThemeColor(ACCENT)

        self.game_page = GamePage(self.state, self)
        self.recompile_page = RecompilePage(self.state, self)
        self.rpcs3_page = Rpcs3Page(self.state, self)
        self.settings_page = SettingsPage(self.state, self)

        self.addSubInterface(self.game_page, FluentIcon.GAME, "Game")
        self.addSubInterface(self.recompile_page, FluentIcon.DEVELOPER_TOOLS, "Recompile")
        self.addSubInterface(self.rpcs3_page, FluentIcon.APPLICATION, "RPCS3")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "Settings",
                             NavigationItemPosition.BOTTOM)

        self.setWindowTitle("PS3 Recomp Studio")
        self.setWindowIcon(QIcon(FluentIcon.GAME.path()))
        self.resize(1080, 760)
        self.setMinimumSize(820, 600)

    def closeEvent(self, event):
        for runner in (self.recompile_page.runner, self.rpcs3_page.runner):
            runner.cancel()
        try:
            config.save(self.state.settings, self.state.project)
        except OSError:
            pass
        super().closeEvent(event)
