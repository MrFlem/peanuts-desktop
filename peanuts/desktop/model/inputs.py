"""What a chosen file or folder is, in the packer's own terms.

The classification is not a guess: it asks the tools that will do the work. `pack_dump.scan()` already decides
whether something is a dump or a `.nup` and reports its title ID, versions and DLC, and `game.load()` already
decides whether a profile exists. Anything this module adds is presentation: a kind, a sentence, and whether the
import step (which is the only step that needs keys) applies.

Calling these readers is safe and cheap: they are not the CLI entry points, they raise rather than exit, and they
read file headers only. The entry points themselves are never imported (engine/process.py explains why).
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from .. import repo_root
from ..engine.redact import redact

# The file types the importer reads (peanuts/scripts/peanuts_build.py: GAME_EXTS).
GAME_EXTS = ('.nsp', '.xci', '.nsz', '.xcz')


def visible(name):
    """The builder's own rule (peanuts_build.py: visible()): macOS leaves an AppleDouble file beside each file it
    touches on an exFAT or FAT drive, and `._Game.nsp` is metadata, not the game. A folder of game files already
    skips them here; a file picked on its own is refused with the same reason instead of being offered for a build
    the builder would then fail on."""
    return not str(name).startswith('._')

_TOOLS = None


class Kind(str, Enum):
    """How the builder will treat the input, which is not the same question as what the file is."""
    NUP = 'nup'        # a package: no import, no keys
    DUMP = 'dump'      # a dump folder: no import, no keys
    GAMES = 'games'    # a folder of .nsp/.xci/.nsz/.xcz files: import, keys needed
    FILE = 'file'      # one such file: import, keys needed
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class InputInfo:
    path: Path
    kind: Kind = Kind.UNKNOWN
    title_id: str | None = None
    name: str | None = None
    versions: tuple = ()
    dlc: int = 0
    has_profile: bool = False
    files: tuple = ()
    error: str | None = None

    @property
    def needs_keys(self):
        """Only importing needs prod.keys. Packing a .nup or a dump needs none (BUILDER.md)."""
        return self.kind in (Kind.FILE, Kind.GAMES)

    @property
    def usable(self):
        return self.error is None and self.kind is not Kind.UNKNOWN


class LatestSelection:
    """Which background classification request is the current one.

    Reading an input happens on a worker thread, and the user can choose another input while the first is still
    being read. Without a marker, the older answer would arrive last and overwrite the newer one, leaving the
    window describing a file the user is no longer looking at. Each request takes a token here; the window applies
    a result only while that token is still current, and drops it otherwise.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._generation = 0

    def next(self):
        """A token for a new request. Every token handed out before this one stops being current."""
        with self._lock:
            self._generation += 1
            return self._generation

    def current(self, token):
        """True when `token` still belongs to the newest request."""
        with self._lock:
            return token == self._generation

    def __repr__(self):
        return f'<LatestSelection generation={self._generation}>'


def tools():
    """The packer's own readers, imported the way the packer imports them (scripts/ and dm/ on sys.path)."""
    global _TOOLS
    if _TOOLS is None:
        root = repo_root()
        for sub in ('scripts', 'dm/games', 'dm/gfx/tools'):
            p = str(root / sub)
            if p not in sys.path:
                sys.path.insert(0, p)
        import game        # noqa: E402  (dm/games/game.py: the profiles)
        import pack_dump   # noqa: E402  (scripts/pack_dump.py: the dump and .nup reader)
        _TOOLS = SimpleNamespace(pack_dump=pack_dump, game=game)
    return _TOOLS


def _profile(title_id):
    """(the profile's game name or None, whether a profile exists). Never raises: no profile is a normal answer."""
    try:
        g = tools().game.load(title_id)
    except SystemExit:   # game.load reports a missing profile by exiting; that is the expected "no" here
        return None, False
    return g.get('name'), True


def _scanned(path, kind):
    """The scanned description of a dump or .nup, or an InputInfo carrying the reader's own error."""
    try:
        app, versions, dlc = tools().pack_dump.scan(path)
    except Exception as e:   # pack_dump.Error is the expected one; the message is already written for a player
        return InputInfo(path, kind, error=str(e))
    title_id = f'{app:016X}'
    name, has_profile = _profile(title_id)
    return InputInfo(path, kind, title_id=title_id, name=name, versions=tuple(sorted(versions)),
                     dlc=len(dlc), has_profile=has_profile)


def classify(path):
    """Describe the chosen path. Always returns an InputInfo; a problem becomes `error`, not an exception."""
    p = Path(path).expanduser()
    try:
        exists = p.exists()
    except OSError as e:
        return InputInfo(p, Kind.UNKNOWN, error=f'{p}: {e}')
    if not exists:
        return InputInfo(p, Kind.UNKNOWN, error=f'{p} does not exist')

    if p.is_file():
        suffix = p.suffix.lower()
        if suffix == '.nup':
            return _scanned(p, Kind.NUP)
        if suffix in GAME_EXTS:
            if not visible(p.name):
                return InputInfo(p, Kind.UNKNOWN,
                                 error=f'{p.name} is macOS metadata (an AppleDouble file) beside a game file, not '
                                       f'the game file: the builder skips it too. Choose {p.name[2:]!r} instead')
            return InputInfo(p, Kind.FILE)
        return InputInfo(p, Kind.UNKNOWN,
                         error=f'{p.name} is not a game file this builder reads (.nsp, .xci, .nsz, .xcz, .nup)')

    try:
        if tools().pack_dump.is_dump(p):
            return _scanned(p, Kind.DUMP)
        found = tuple(sorted(f for f in p.iterdir()
                             if f.is_file() and f.suffix.lower() in GAME_EXTS and visible(f.name)))
    except Exception as e:   # an unreadable folder becomes a sentence, never a traceback in the window
        return InputInfo(p, Kind.UNKNOWN, error=f'{p}: {e}')
    if found:
        return InputInfo(p, Kind.GAMES, files=found)
    return InputInfo(p, Kind.UNKNOWN,
                     error=f'{p.name} holds no base/exefs (a dump) and no .nsp, .xci, .nsz or .xcz files at its '
                           'top level')


def diagnostic(text):
    """A reader's own error sentence as the window may show it.

    A reader's message names the path it was given - often the one the user chose, sometimes one it found under it -
    and it reaches the About label, the Plan pane's blocker and any screenshot of them. It passes through the same
    redaction as the log's lines (engine/redact.py), so a home folder becomes `~` or `<user>` and a registered serial
    is masked, while the file and the reason stay readable.
    """
    return redact(str(text))


def summary(info):
    """One sentence for the window: what was found, in the player's words.

    A failure is a reader's own sentence, so it is shown through diagnostic(): the other branches name only the
    input's own name and its title ID, which are not private data.
    """
    if info.error:
        return diagnostic(info.error)
    if info.kind is Kind.FILE:
        return f'{info.path.name}: a game file. Importing it needs your prod.keys; the builder reads the title ID from it.'
    if info.kind is Kind.GAMES:
        return f'{len(info.files)} game file(s) in {info.path.name}. Importing them needs your prod.keys.'
    bits = [f'{info.name or "?"} ({info.title_id})']
    if info.versions:
        bits.append('versions: base, ' + ', '.join(f'v{v}' for v in info.versions))
    else:
        bits.append('versions: base')
    if info.dlc:
        bits.append(f'DLC: {info.dlc}')
    if not info.has_profile:
        bits.append(f'no profile in dm/games/{info.title_id}: this game cannot be built yet')
    return '   '.join(bits)
