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

## Security

- The window never reads `prod.keys`; it only passes a path to the importer (`Phase B`). Nothing about the file's
  contents is displayed, logged or stored.
- Every line passes through `engine/redact.py` before it reaches the log pane or a screenshot, including the
  `$ <command>` line the window prints itself: home folders become `~` or `<user>`. Do not disable it to debug
  something that will be shared.
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

Both run in CI through `.github/workflows/peanuts.yml`, which runs every `peanuts/tests/test_*.py` on Python 3.14
with no display.

## What comes next

`Phase B` import and keys · `Phase C` configuration, packaging and progress · `Phase D` output and host-side ADB ·
`Phase E` Steam Deck validation and installation. The plan for each is Work Order #002.
