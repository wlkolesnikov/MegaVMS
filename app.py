#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
QT_ROOT = REPO_ROOT / "sdk-hik-QT"
if str(QT_ROOT) not in sys.path:
    sys.path.insert(0, str(QT_ROOT))

from hikvision_player.config import restart_if_needed  # type: ignore  # noqa: E402


def main() -> int:
    restart_if_needed()

    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    from core import ApplicationCore
    from ui import MainWindow

    logging.basicConfig(
        level=getattr(logging, os.environ.get("HIK_PLAYER_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    core = ApplicationCore()
    window = MainWindow(core)
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
