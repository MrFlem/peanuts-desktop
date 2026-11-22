"""When a run is over, kept out of the window so it can be checked without a display.

A run's Exited event says that the tool itself ended. It does not say that the processes the tool started are gone:
on POSIX the supervisor ends the rest of the tool's process group afterwards, and only then gives the one run slot
back. The window used to drop its reference to the run on Exited, which left it able to close over a group that was
still being cleaned up - and unable to see it at all.

Two questions, kept apart:

  * may the window close? `still_running()`: the supervisor has not settled the run, so its processes may be alive.
  * may the window stop tracking it? `finished()`: the run is settled (or its slot came back) *and* the window has
    already drained and handled its Exited event. The supervisor queues Exited before it frees the slot
    (engine/process.py), but the event still travels through a queue the window reads on its own timer: a run
    dropped as soon as the slot came back would lose that event and the status line it sets.
"""
from __future__ import annotations


def still_running(run):
    """True while a run's processes may still be alive: the window must not close over it."""
    return run is not None and not run.settled()


def finished(run, busy, exit_seen):
    """True when a run is over, its terminal event has been processed, and nothing of it is left to clean up.

    `busy` is SupervisedProcess.busy(): the one run slot this process has, which is given back only once the run's
    whole process group is gone. A run that has exited but still holds it has processes of its own alive (or could
    not be confirmed gone), so the window keeps it, and its Start stays disabled, until the slot comes back.
    `exit_seen` is the window's own record that it has handled this run's Exited event: the slot can come back
    before the window has read the queue, and `not busy` alone must not drop a run whose terminal event is still
    waiting there.
    """
    if run is None:
        return True
    if not exit_seen:
        return False
    return run.settled() or not busy
