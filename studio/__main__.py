"""PS3 Recomp Studio: python -m studio"""

import sys
import traceback
from pathlib import Path


def _report(exc_text: str) -> None:
    """Studio runs under pythonw, which has no console: a startup error would vanish
    with the window that never opened. Keep it in studio-crash.log and show it."""
    log = Path(__file__).resolve().parent.parent / "studio-crash.log"
    try:
        log.write_text(exc_text, encoding="utf-8")
    except OSError:
        pass
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "PS3 Recomp Studio could not start",
                             f"{exc_text[-3000:]}\n(Saved to {log})")
    except Exception:
        pass


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication(sys.argv)
        app.setApplicationName("PS3 Recomp Studio")
        from .ui.window import MainWindow   # after QApplication: widgets need it
        w = MainWindow()
        w.show()
    except Exception:
        _report(traceback.format_exc())
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
