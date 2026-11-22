"""Taking the user's home folder and the protected device serials out of the output before it reaches a log, a
screenshot or a bug report.

The build tools print absolute paths, and a path names its owner. The project's security requirements also cover
device identifiers, so a serial that the application has been told to protect is masked here as well. Every line
the supervisor emits passes through this module first.

The redaction is deliberately narrow: home folders are matched structurally, and serials are matched only because
they were registered. An over-eager filter would hide the very paths, title IDs and hashes a bug report needs, so
nothing else is touched.

What is replaced:
    /home/<name>/...        -> /home/<user>/...
    /Users/<name>/...       -> /Users/<user>/...
    C:\\Users\\<name>\\...     -> C:\\Users\\<user>\\...
    the real $HOME prefix   -> ~              (so "~/games/x" stays readable and useful)
    a registered serial     -> [REDACTED_DEVICE_SERIAL]

Serials are not inferred: a bare alphanumeric string is far too common to match (title IDs, versions, hashes).
Only a value passed to protect_serial() is masked, and registering one never prints it — that is the point.
"""
from __future__ import annotations

import os
import re
import threading

RE_HOME = re.compile(r'/home/[^/\s"\'`]+')
RE_USERS = re.compile(r'/Users/[^/\s"\'`]+')
RE_WIN_USERS = re.compile(r'[Cc]:\\Users\\[^\\\s"\'`]+')

SERIAL_PLACEHOLDER = '[REDACTED_DEVICE_SERIAL]'
MIN_SERIAL_LENGTH = 4   # anything shorter would match ordinary words; no real device serial is this short


class SerialRegistry:
    """The serials this process has been told to protect, and the mask that removes them.

    An explicit list rather than a pattern: masking every long alphanumeric token would hide title IDs and
    versions, and a filter that hides too much is as unhelpful as one that hides nothing. Registration is silent
    on purpose — a serial must not appear in an output line, and that includes the line that registers it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._values = ()
        self._pattern = None

    def protect(self, serial):
        """Add one serial. False when it was already there or is too short to match without false positives."""
        value = str(serial if serial is not None else '').strip()
        if len(value) < MIN_SERIAL_LENGTH:
            return False
        with self._lock:
            if value in self._values:
                return False
            values = tuple(sorted((*self._values, value), key=len, reverse=True))
            self._values = values
            # Longest first, so a serial that contains another is still masked whole. Case-insensitive: the same
            # device is reported in either case depending on which tool saw it.
            self._pattern = re.compile('|'.join(re.escape(v) for v in values), re.IGNORECASE)
            return True

    def clear(self):
        """Forget every serial. The tests use it so one case cannot leak into the next."""
        with self._lock:
            self._values = ()
            self._pattern = None

    def apply(self, text):
        with self._lock:
            pattern = self._pattern
        if pattern is None or not text:
            return text
        return pattern.sub(SERIAL_PLACEHOLDER, text)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        # Never the values: a debug print must not undo the masking it is reporting on.
        return f'<SerialRegistry: {len(self._values)} serial(s) protected>'


SERIALS = SerialRegistry()   # process-wide: a serial registered once is masked everywhere redact() is used


def protect_serial(serial):
    """Register a serial that must never reach a log, a screenshot or a crash report. True when it was added."""
    return SERIALS.protect(serial)


def redact_serials(text):
    """text with every registered serial replaced. Unregistered alphanumeric strings are left alone."""
    return SERIALS.apply(text)


def redact(text, home=None, serials=None):
    """text with the user's home folder and the registered serials replaced. Idempotent."""
    if not text:
        return text
    out = text
    # The real home first and longest-match: "~/games/x" reads better than "/home/<user>/games/x".
    home = home if home is not None else os.path.expanduser('~')
    if home and len(home) > 1 and home in out:
        out = out.replace(home, '~')
    out = RE_HOME.sub('/home/<user>', out)
    out = RE_USERS.sub('/Users/<user>', out)
    out = RE_WIN_USERS.sub(r'C:\\Users\\<user>', out)
    return (serials if serials is not None else SERIALS).apply(out)


def redact_lines(lines, home=None):
    """redact() over an iterable of lines, lazily."""
    for line in lines:
        yield redact(line, home)
