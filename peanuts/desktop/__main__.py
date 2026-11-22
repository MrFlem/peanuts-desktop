"""python -m peanuts.desktop: open the window."""
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        import tkinter  # noqa: F401  (the window's toolkit: checked here so the message names the real problem)
    except ImportError:
        sys.exit('[RED] this Python has no Tk: use the packer environment (android/environment.yml: conda-forge '
                 'Python has Tk), or install python3-tk on Linux / python-tk on macOS')
    from .ui.app import main as run
    return run(argv)


if __name__ == '__main__':
    sys.exit(main())
