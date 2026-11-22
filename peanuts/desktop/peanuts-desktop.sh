#!/bin/sh
# Peanuts Desktop on Linux and SteamOS: open the window with a Python 3.14 that has Tk, the same interpreter the
# builder needs (android/environment.yml). PEANUTS_PYTHON names it, as peanuts/scripts/peanuts-build.sh takes it: a
# command name (python3) or a path; else python3.14, else python3 when it is 3.14 or later.
#   PEANUTS_PYTHON=~/peanuts-env/bin/python peanuts/desktop/peanuts-desktop.sh [<game folder | .nup>]
# On SteamOS the interpreter lives inside the distrobox container, not in the read-only root (Work Order #002 §5).
set -e
REPO=$(cd "$(dirname "$0")/../.." && pwd)
die() { printf '%s\n' "[RED] $*" >&2; exit 1; }

py=
if [ -n "${PEANUTS_PYTHON:-}" ]; then
	# A command name or a path, used as given, the way peanuts/scripts/peanuts-build.sh uses it (it checks
	# nothing); the check here only turns a typo into a line that names it. A path must be executable, a bare
	# name must be on PATH.
	case "$PEANUTS_PYTHON" in
	*/*) [ -x "$PEANUTS_PYTHON" ] || die "PEANUTS_PYTHON names $PEANUTS_PYTHON, which is not executable" ;;
	*) command -v "$PEANUTS_PYTHON" >/dev/null 2>&1 || die "PEANUTS_PYTHON names $PEANUTS_PYTHON, which is not on PATH" ;;
	esac
	py=$PEANUTS_PYTHON
else
	for c in python3.14 python3; do
		if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 14))' 2>/dev/null; then
			py=$c
			break
		fi
	done
fi
[ -n "$py" ] || die "the window needs Python 3.14 with Tk: make the environment from android/environment.yml (peanuts/docs/BUILDER.md) and set PEANUTS_PYTHON to its python"

"$py" -c 'import tkinter' 2>/dev/null || die "$py has no Tk: use the packer environment (conda-forge Python has Tk), or install python3-tk"

# -m with the repository on the path, from any working directory: the window reads the profiles and tools relative
# to the checkout, and the tools it starts do the same.
PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
exec "$py" -m peanuts.desktop "$@"
