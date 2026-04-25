#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
from pathlib import Path
import sys


DEFAULT_LIB_DIR = Path(os.environ.get("HIKVISION_LIB_DIR", Path.home() / ".local/lib/hikvision"))


def ensure_runtime_environment(lib_dir: Path | None = None) -> bool:
    base = Path(lib_dir or DEFAULT_LIB_DIR)
    changed = False

    desired_ld_paths = [str(base), str(base / "HCNetSDKCom")]
    current_ld = os.environ.get("LD_LIBRARY_PATH", "")
    current_parts = [part for part in current_ld.split(":") if part]
    for path in reversed(desired_ld_paths):
        if path not in current_parts:
            current_parts.insert(0, path)
            changed = True

    new_ld = ":".join(current_parts)
    if new_ld != current_ld:
        os.environ["LD_LIBRARY_PATH"] = new_ld

    if os.environ.get("QT_QPA_PLATFORM") != "xcb":
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        changed = True

    return changed


def restart_if_needed(lib_dir: Path | None = None) -> None:
    if ensure_runtime_environment(lib_dir=lib_dir):
        os.execv(sys.executable, [sys.executable] + sys.argv)


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
