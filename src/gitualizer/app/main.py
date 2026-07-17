from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from gitualizer.git.runner import install_terminal_interrupt_protection
from gitualizer.ui.main_window import MainWindow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open the Gitualizer read-only repository inspector.")
    parser.add_argument("repository", nargs="?", type=Path, help="Path inside a Git repository.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    app = QApplication(sys.argv[:1])
    install_terminal_interrupt_protection(app.quit)
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)
    window = MainWindow(args.repository)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
