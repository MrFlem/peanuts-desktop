# Peanuts Desktop — the window around the builder

A desktop GUI for the build workflow that [`peanuts/docs/BUILDER.md`](../docs/BUILDER.md) documents: it drives
`peanuts/scripts/peanuts_build.py` and `scripts/pack_android.py` and never reimplements what they do.

**This is not a PC port of Peanuts Native.** The build host is the desktop; the game runs on the Android device.

## Phase A — what exists today

| Works now | Not yet |
|---|---|
| Pick a `.nup`, a dump folder, or `.nsp`/`.xci`/`.nsz`/`.xcz` files | Importing (`Phase B`) |
| Say what the input is, in the packer's own terms (title ID, versions, DLC, profile present or not) | Choosing `prod.keys` (`Phase B`) |
| Show the exact command that would run, and why it cannot | Running a build (`Phase C`) |
| Check this computer: Python, `adb`, and the packer environment as a supervised run | Choosing updates and DLC (`Phase C`) |
| A live log with redaction, a working **Stop**, and correct exit reporting | Output management, ADB transfer (`Phase D`) |

Phase A is deliberately inspection-only. The plan is **displayed, not executed**: a button that says it will build
and then does not would be worse than no button.

## Running it

The window needs the packer's Python (3.14, with Tk), the same environment the builder uses:

```sh
# the environment from android/environment.yml, made once (see BUILDER.md "Linux")
PEANUTS_PYTHON=~/peanuts-env/bin/python peanuts/desktop/peanuts-desktop.sh
```

or, with that interpreter already active:

```sh
python -m peanuts.desktop
```

`PEANUTS_PYTHON` takes a command name or a path, as `peanuts/scripts/peanuts-build.sh` takes it; the launcher only
adds a named error when a bare name is not on `PATH` or a path is not executable. Without it the launcher looks for
`python3.14`, then `python3` when it is 3.14 or later.

## How it is put together

```
peanuts/desktop/
  engine/    process supervision: argv, markers, cancel, redaction    no tkinter, no build logic
  model/     classification, preflight, the run plan                  no tkinter
  ui/        the Tk window                                            the only package that imports tkinter
  platform/  opening a folder; adb discovery later                    per-host details
```

Three rules hold this together, and each has a test:

1. **The build tools are never imported.** `peanuts_build.py` reports failure with `sys.exit()`, so importing it
   into the window would kill the window on a bad input. They are started as child processes with an argument
   list (never a shell string), and read back through the markers they already print.
2. **`engine/` and `model/` never import tkinter.** That is what lets the tests run in CI, which has no display.
   `peanuts/tests/test_desktop_model.py` enforces it by importing both with `sys.modules['tkinter'] = None`.
3. **One run at a time.** `SupervisedProcess` holds a process-wide slot, so two builds can never write the same
   pack folder; a second attempt raises `AlreadyRunning` instead of corrupting a pack. The slot comes back only
   once the run's whole process group is gone: a child that outlived the tool, or ignored the stop request, is
   ended, and if the group cannot be confirmed empty the slot stays taken rather than letting a second build
   start beside it.

## The marker contract

The window understands the lines the packer already prints. This is the whole integration surface; if a tool's
output changes, `engine/markers.py` changes with it.

| Line | Meaning |
|---|---|
| `progress: 12/40` | `shader_split.py`: the shaders split out of the dump |
| `progress 12 40 Building …` | `vk_android_build.py`: the shader library |
| `  main: rewriting` | one module at a time |
| `shaders: splitting` / `shaders: building` | a stage with no numbers |
| `done: <path> (<n> MiB); …` | the pack is complete; `<path>` is authoritative |
| `[RED] …` | something wrong; the exit code says whether it was fatal |

## Keyboard and closing

The Plan, Preflight and Log panes are read-only rather than `disabled`, so they can hold the focus and their text
can be scrolled, selected and copied (`Ctrl+C`; `Ctrl+A` selects the pane). `Tab` and `Shift+Tab` move the focus
between widgets and never type a tab into a pane; `Alt+P`, `Alt+K` and `Alt+L` put the focus on the Plan, Preflight
and Log pane. The window's own shortcuts (`Ctrl+O`, `Ctrl+D`, `F5`, `Esc`) keep working while a pane has the focus:
that rule is `ui/keys.py`, so it is checked without a display.

`Ctrl+O` and `Ctrl+D` need care, because Tk's Text class binds both to an edit (open a line, delete a character) and
a class binding runs after the pane's own. The pane therefore runs the window's picker itself and answers `break`,
which stops the class binding; cut, paste, undo and middle-click are stopped the same way. The panes stay
immutable, and `PaneBindings` in the tests drives a real `Text` with those keys and events on a machine with a
display. The log follows new output only while it is scrolled to the bottom, so reading earlier lines is not
interrupted.

Closing the window during a check asks first, then stops the run and waits - bounded - for its whole process group
to be gone before the window goes away. `Exited` is not the end of the run: the supervisor ends what the tool
started afterwards and queues `Exited` before it frees the run slot, and the window keeps the run until that event
has been handled *and* the supervisor reports it settled (`ui/lifecycle.py`). It will not close over it, and it will
not drop it early either - the slot can come back before the window has read the queue, so "the slot is free" alone
is not the end. A group that cannot be confirmed gone is sent `SIGKILL` and named on stderr, rather than left behind
by a window that closed over it.

## Security

- **What a screenshot can show.** The path box, the `Input` label and the Plan pane deliberately show the input you
  chose and the exact command that would run, including the paths in it (in `Phase B`, the `prod.keys` path). They
  are local, not a log, and they are not redacted: read them before sharing a screenshot or a copied plan. Everything
  the tools print - the log, the status line, the progress text - is redacted, and so is a reader's error sentence
  (see below). The redaction still shortens the *error's* paths, not the ones you picked.
- A failure to read an input is a sentence from the reader, not the log: `classify()` keeps it in `InputInfo.error`
  and `model/inputs.diagnostic()` redacts it before it reaches the `Input` label or the Plan pane's blocker, so a
  home folder or a registered serial in an error cannot be photographed by accident. A message box gets the same
  treatment (`ui/app.py`), because a spawn error quotes the command it could not run.
- The window never reads `prod.keys`; it only passes a path to the importer (`Phase B`). Nothing about the file's
  contents is displayed, logged or stored.
- Every event passes through `engine/redact.py` before it reaches the window, not only a `Line`: a `progress`
  label, a stage line, the pack path a `done:` line carries and the `$ <command>` line the window prints itself are
  redacted too, so a home folder becomes `~` or `<user>` wherever it would appear (log, status line, progress text),
  and an error the reader reports passes through the same redaction. The shortened path still opens locally, so the
  pane stays useful while what you copy out of it is what is safe to share. Do not disable it to debug something
  that will be shared.
- Device serials become `[REDACTED_DEVICE_SERIAL]` once registered with `engine/redact.protect_serial()`. Only a
  registered serial is matched — never every long alphanumeric string, which would hide title IDs and versions —
  and registering one never prints it. Phase A registers none; the transfer step (Phase D) registers the serials
  it learns.
- Subprocesses are started from an argument list, with `shell=False` implied, so a path containing spaces, quotes
  or `$(…)` is one argument and nothing else.
- The window opens no network ports and downloads nothing.

## Tests

```sh
python peanuts/tests/test_desktop_engine.py     # markers, redaction, real child processes, cancel, one-at-a-time
python peanuts/tests/test_desktop_model.py      # classification, the run plan, the no-tkinter rule
```

The keyboard rules and the run lifecycle are checked by running them (`ui/keys.py` and `ui/lifecycle.py` import no
Tk, so `Keyboard` and `RunLifecycle` in `test_desktop_model.py` exercise the real functions), and the rest of the
window's structure is read from its source (`WindowStructure`). `PaneBindings` additionally drives a real Tk `Text`
with `read_only()`'s bindings - `Ctrl+O`, `Ctrl+D`, typing, paste, cut, undo, middle-click, selection, `Tab` - but
it skips itself when there is no tkinter or no display, which is the case on this machine and in CI. So no pane was
rendered, focused, typed into, scrolled or copied from *here*, and no GUI behaviour is claimed from the checks that
ran: the device/UI pass, and what it must cover, are still pending. `test_desktop_engine.py` covers the supervisor's
side of the same lifecycle against real child processes.

Both run in CI through `.github/workflows/peanuts.yml`, which runs every `peanuts/tests/test_*.py` on Python 3.14
with no display.

## What comes next

`Phase B` import and keys · `Phase C` configuration, packaging and progress · `Phase D` output and host-side ADB ·
`Phase E` Steam Deck validation and installation. The plan for each is Work Order #002.
