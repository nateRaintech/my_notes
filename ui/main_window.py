"""The application's main window.

The minimal app shell: a titled, sized window with a placeholder body and a
status bar, so ``python app.py`` opens something from a fresh checkout. Later
milestones grow this into the 3-pane notebooks/notes/editor layout (ROADMAP.md
M3) and wire in the unlock flow (M4).

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow

WINDOW_TITLE = "my_notes"
DEFAULT_SIZE = (1000, 700)


class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*DEFAULT_SIZE)

        placeholder = QLabel("my_notes\n\nYour encrypted notes will live here.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(placeholder)

        self.statusBar().showMessage("Ready")
