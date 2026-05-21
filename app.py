"""my_notes — application entry point.

Run ``python app.py`` to open the main window. The Qt application is created
here in :func:`main`; all window logic lives in the ``ui`` package.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    """Create the Qt application, show the main window, and run the event loop."""
    app = QApplication(sys.argv)
    app.setApplicationName("my_notes")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
