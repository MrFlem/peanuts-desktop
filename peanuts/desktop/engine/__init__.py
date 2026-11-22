"""What the window needs to run a tool and understand it: never tkinter, never build logic.

The build itself stays in scripts/pack_android.py and peanuts/scripts/peanuts_build.py. This package only starts
those programs, reads the lines they print, and stops them. Keeping it free of tkinter is what lets
peanuts/tests/test_desktop_engine.py run on a machine with no display (and in CI, which has none).
"""
from .events import Done, Event, Exited, Line, Progress, Stage
from .markers import parse_line, parse_stream
from .process import AlreadyRunning, SpawnError, SupervisedProcess, python_argv
from .redact import SERIAL_PLACEHOLDER, SERIALS, protect_serial, redact, redact_lines, redact_serials

__all__ = ['Done', 'Event', 'Exited', 'Line', 'Progress', 'Stage', 'parse_line', 'parse_stream',
           'AlreadyRunning', 'SpawnError', 'SupervisedProcess', 'python_argv',
           'redact', 'redact_lines', 'SERIALS', 'SERIAL_PLACEHOLDER', 'protect_serial', 'redact_serials']
