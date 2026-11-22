"""Peanuts Desktop: a desktop window around Peanuts Native's build tools.

Phase A: the project structure, the process supervision layer and the inspection half of the workflow.
The importer and the packer are never imported into this program: they are started as separate processes
(engine/process.py) and read back through the markers they already print (engine/markers.py), so the window
and the command line make the same build (peanuts/docs/BUILDER.md).

Layers, and what may import what:
    engine/   process supervision and marker parsing: no tkinter, no build logic  (importable headless)
    model/    input classification, preflight, the run plan                      (imports the tools' readers)
    ui/       the Tk window                                                      (imports model and engine)
    platform/ host details: opening a folder, finding adb                        (imported by ui)
A module under engine/ or model/ must never import tkinter: peanuts/tests/test_desktop_*.py run headless.
"""
__version__ = '0.1.0'


def repo_root():
    """The checkout this package lives in (peanuts/desktop/__init__.py -> the repository root)."""
    from pathlib import Path
    return Path(__file__).resolve().parents[2]
