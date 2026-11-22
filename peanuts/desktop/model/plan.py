"""What would run, as an argument list, before anything runs.

Phase A shows this plan and does not execute it. Two reasons for keeping it as data rather than as a button
handler: the exact argv can be reviewed (and compared against peanuts/docs/BUILDER.md) without running a build,
and Phase C only has to take the argv this module already produces and hand it to the supervisor.

The commands are the ones the project documents. Import and pack are different steps with different flags, and
`--update` (which update FILE to import) is not `--version` (which update FOLDER to pack from a dump); the plan
keeps them apart on purpose.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from .. import repo_root
from .inputs import Kind, InputInfo

BUILD_SCRIPT = 'peanuts/scripts/peanuts_build.py'
PACK_SCRIPT = 'scripts/pack_android.py'


@dataclass(frozen=True)
class Step:
    """One program to run. `note` says what it does in a player's words."""
    title: str
    argv: tuple
    note: str = ''

    def command(self):
        import shlex
        return shlex.join(self.argv)


@dataclass(frozen=True)
class RunPlan:
    steps: tuple = ()
    blockers: tuple = ()
    notes: tuple = ()

    @property
    def runnable(self):
        return bool(self.steps) and not self.blockers


def _rel(script):
    return str(repo_root() / script)


def plan_for(info: InputInfo, keys=None, update=None):
    """The plan for one input. `keys` is a path chosen locally; nothing here reads that file."""
    blockers = []
    notes = []

    if not info.usable:
        return RunPlan(blockers=(info.error or 'that input cannot be built',), notes=())

    if info.needs_keys and not keys:
        blockers.append('this input must be imported first, and importing needs your own prod.keys')

    if info.title_id and not info.has_profile:
        blockers.append(f'no profile in dm/games/{info.title_id}: the pack cannot be made for this game')

    if info.kind in (Kind.FILE, Kind.GAMES):
        sources = info.files if info.kind is Kind.GAMES else (info.path,)
        argv = [sys.executable, _rel(BUILD_SCRIPT), *[str(p) for p in sources]]
        argv += ['--keys', keys or '<your prod.keys>']
        if update is not None:
            argv += ['--update', str(update)]
        steps = (Step('Import and build',
                      tuple(argv),
                      'Imports the game files into a .nup, then builds the pack from it. Needs prod.keys, a C '
                      'compiler and cmake for the importer.'),)
    else:
        argv = [sys.executable, _rel(PACK_SCRIPT), str(info.path)]
        if info.kind is Kind.DUMP and update is not None:
            argv += ['--version', str(update)]
        steps = (Step('Build the pack',
                      tuple(argv),
                      'Rewrites the game code for Android and builds the shader library. No keys are needed for '
                      'this step.'),)

    notes.append('Phase A shows this plan; building is wired in Phase C.')
    notes.append('A stopped build resumes: finished modules and translated shaders are kept.')
    return RunPlan(steps=steps, blockers=tuple(blockers), notes=tuple(notes))
