"""Showing a folder in the desktop's file manager, on every platform this window runs on.

scripts/pack_gui.py calls `open` on any non-Windows system, which is macOS's file manager and does not exist on
Linux: "Show pack" fails there with a FileNotFoundError. This module is the corrected version, and it is the only
place the platform is asked.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_folder(path):
    """Open `path` in the file manager. True when a program was started; False when it could not be."""
    p = Path(path).expanduser()
    try:
        if not p.is_dir():
            return False
    except OSError:
        return False

    if os.name == 'nt':
        try:
            os.startfile(str(p))   # pyright: ignore[reportAttributeAccessIssue]  # Windows-only API
            return True
        except OSError:
            return False

    command = ['open', str(p)] if sys.platform == 'darwin' else ['xdg-open', str(p)]
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False
