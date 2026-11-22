"""When a run is over, kept out of the window so it can be checked without a display.

A run's Exited event says that the tool itself ended. It does not say that the processes the tool started are gone:
on POSIX the supervisor ends the rest of the tool's process group afterwards, and only then gives the one run slot
back. The window used to drop its reference to the run on Exited, which left it able to close over a group that was
still being cleaned up - and unable to see it at all.

So the run is kept until it is finished, and "finished" is the supervisor's own answer, never the exit code alone.
"""
from __future__ import annotations


def still_running(run):
    """True while a run's processes may still be alive: the window must not close over it."""
    return run is not None and not run.settled()


def finished(run, busy):
    """True when a run is over and nothing of it is left to clean up.

    `busy` is SupervisedProcess.busy(): the one run slot this process has, which is given back only once the run's
    whole process group is gone. A run that has exited but still holds it has processes of its own alive (or could
    not be confirmed gone), so the window keeps it, and its Start stays disabled, until the slot comes back.
    """
    if run is None:
        return True
    if run.exit_event is None:
        return False
    return run.settled() or not busy
