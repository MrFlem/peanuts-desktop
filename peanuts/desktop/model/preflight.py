"""What this computer can do, in the two cases the window answers without starting anything.

The packer's own environment check is not duplicated here: it is a program (`scripts/bananative_env.py`) and the
window runs it through the supervisor, so its answer, its exit code and its output are the project's own.

These two are different: they are instant, local questions about the interpreter the window is running on and
about `adb`, which the transfer step (Phase D) needs on the *host*, outside the container.

Keeping them as data (Check) rather than as printed text is what lets Phase C gate the build on them.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    what: str
    ok: bool
    detail: str = ''

    def word(self):
        return 'ok' if self.ok else 'missing'


def python_check():
    """The packer needs Python 3.14 (compression.zstd); the window runs on the same interpreter."""
    ok = sys.version_info[:2] >= (3, 14)
    return Check('Python 3.14', ok, f'{sys.version.split()[0]} ({sys.executable})')


def adb_check():
    """adb on PATH: the optional transfer step. A missing adb is not a build blocker."""
    found = shutil.which('adb')
    return Check('adb (transfer)', bool(found), found or 'not found on PATH')


def local_checks():
    """The two checks the window can answer immediately."""
    return [python_check(), adb_check()]


def blockers(checks):
    """The failed checks that stop a build; adb is excluded, since the transfer is optional."""
    return [c for c in checks if not c.ok and not c.what.startswith('adb')]
