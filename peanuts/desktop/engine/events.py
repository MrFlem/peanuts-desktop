"""The events the window draws. Frozen and plain, so the tests can compare them literally.

A run produces, in order: Line events (every line of output, with its severity), zero or more Progress and Stage
events read from the lines that carry them, at most one Done (the pack completed: its path is authoritative), and
exactly one Exited last. Exited carries the exit code, whether the run was cancelled and how long it took, plus
whether the last line had arrived (`output_complete`) and whether a child outlived the tool (`cleanup`, a sentence
for the status line).
"""
from __future__ import annotations

from dataclasses import dataclass

# The severities a Line can have. 'red' is the project's own marker for something wrong or unimplemented
# ([RED] at the start of the line: the repository's rule for log lines), and the window paints it red.
INFO = 'info'
RED = 'red'
ECHO = 'echo'   # what the window itself says, such as the command it is about to run


class Event:
    """Base of every event: the supervisor's queue holds these, and the window switches on the subclass."""


@dataclass(frozen=True)
class Line(Event):
    text: str
    severity: str = INFO


@dataclass(frozen=True)
class Progress(Event):
    done: int
    total: int
    label: str

    @property
    def fraction(self):
        return 0.0 if self.total <= 0 else min(1.0, max(0.0, self.done / self.total))


@dataclass(frozen=True)
class Stage(Event):
    text: str


@dataclass(frozen=True)
class Done(Event):
    path: str
    detail: str = ''


@dataclass(frozen=True)
class Exited(Event):
    code: int
    cancelled: bool = False
    seconds: float = 0.0
    output_complete: bool = True   # False: the pipe was still open, so the last lines may be missing
    cleanup: str = ''              # a child that outlived the tool, or why the group could not be confirmed gone

    @property
    def ok(self):
        return self.code == 0 and not self.cancelled

    def word(self):
        """A short sentence for the status line, in the project's voice."""
        if self.cancelled:
            return 'stopped'
        if self.code == 0:
            return 'done'
        if self.code < 0:
            return f'ended by signal {-self.code}'
        return f'failed (exit code {self.code})'
