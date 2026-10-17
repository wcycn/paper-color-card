#!/usr/bin/env python3
"""Start the GUI independently of the terminal/chat that launched it."""

import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "paper-color-card"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "application.log").open("a", encoding="utf-8") as log:
        subprocess.Popen([sys.executable, str(root / "paper_color_card.py")],
                         cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)


if __name__ == "__main__":
    main()
