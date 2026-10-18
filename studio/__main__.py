"""PS3 Recomp Studio: python -m studio"""

import sys

from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PS3 Recomp Studio")
    from .ui.window import MainWindow   # after QApplication: widgets need it
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
