"""The lines the build tools already print, parsed once so the window and the tests read them the same way.

Nothing here invents output: every pattern is one the tools emit today, and scripts/pack_gui.py parses the same
ones. If a tool's format changes, this file changes with it, which is why parsing is a pure function of one line.

    progress: 12/40                shader_split.py: the shaders split out of the dump
    progress 12 40 Building ...    vk_android_build.py: the shader library (the optional tail says what)
      main: rewriting              pack_android.rewrite: one module at a time (rewriting / done / unchanged)
    shaders: splitting             a stage with no numbers to show
    shaders: building              the same for the library
    done: <path> (<n> MiB); ...    the pack is complete: <path> is authoritative
    [RED] ...                      something wrong or unimplemented (the exit code says whether it was fatal)
"""
from __future__ import annotations

import re

from .events import INFO, RED, Done, Line, Progress, Stage

# The patterns, kept identical to scripts/pack_gui.py's so the two windows never disagree about a line.
RE_SPLIT = re.compile(r'^progress: (\d+)/(\d+)')                 # shader_split.py
RE_BUILD = re.compile(r'^progress (\d+) (\d+) ?(.*)')            # vk_android_build.py
RE_REWRITE = re.compile(r'^  (\S+): (rewriting|done|unchanged)')  # pack_android.rewrite
RE_DONE = re.compile(r'^done: (?:the modules in )?(?P<path>.*?) \((?:[0-9.]+ MiB|--code-only)')

RED_MARK = '[RED] '      # the repository's rule for a line that reports something wrong (AGENTS.md)
DONE_MARK = 'done: '


def severity(line):
    """'red' for the project's own error marker, 'info' otherwise (the window paints only the first)."""
    return RED if line.startswith(RED_MARK) else INFO


def parse_line(line):
    """-> the events one line carries: always its Line, plus a Progress, Stage or Done when the line is one.

    A pure function: same line in, same events out, no state. That is what makes the marker contract testable
    against recorded output instead of against a running packer.
    """
    text = line.rstrip('\r\n')
    events = [Line(text, severity(text))]

    if m := RE_SPLIT.match(text):
        events.append(Progress(int(m[1]), int(m[2]), 'Splitting the shaders out of the dump'))
        return events

    if m := RE_BUILD.match(text):
        label = 'Building the shader library' + (f' ({m[3]})' if m[3] else '')
        events.append(Progress(int(m[1]), int(m[2]), label))
        return events

    if m := RE_REWRITE.match(text):
        events.append(Stage(f'Rewriting the code: {m[1]} {m[2]}'))
        return events

    if text.startswith('shaders: splitting'):
        events.append(Stage('Splitting the shaders out of the dump (a few minutes)'))
    elif text.startswith('shaders: building'):
        events.append(Stage('Building the shader library (about 10 minutes)'))

    if text.startswith(DONE_MARK):
        m = RE_DONE.match(text)
        if m:
            events.append(Done(m['path']))

    return events


def parse_stream(lines):
    """Every event of an iterable of lines, in order (a recorded log, or a test fixture)."""
    for line in lines:
        yield from parse_line(line)
