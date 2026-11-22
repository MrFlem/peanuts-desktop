"""Running one build tool as a separate process, and reading it back as events.

The window never imports the build tools. peanuts_build.py and pack_android.py report failure with sys.exit(),
so importing them into a GUI process would terminate the window on a bad input; they are started here instead,
with an argument list (never a shell string), and their output is parsed into events.

Guarantees this class is responsible for, and the tests check:
  * an argument list, never a shell line: no quoting rules, no injection through a path
  * one run at a time in this process (two builds must not write the same pack folder)
  * output read on a worker thread, so the window stays responsive
  * stderr merged into stdout, so an error and its context stay in order
  * cancellation that reaches the tool's own children (one process group), then a kill after a grace period
  * the run slot is given back only once that whole group is gone: a survivor keeps it taken, so a second
    build can never start beside the previous one's children
  * exactly one Exited event, last, carrying the exit code and whether it was cancelled
  * Exited is queued before exit_event, the wait handle and the run slot are published, so nothing can observe a
    finished run whose terminal event it has not been given yet
  * nothing is ever queued after Exited: the queue is sealed with it, so a line the reader had in hand at that
    moment is dropped rather than shown after the run
  * the last line is in before Exited, or Exited says the pipe stayed open and the tail may be missing
  * every line, and every path an event carries, redacted before it leaves this object
  * a start that fails frees the slot: an invalid argument is refused as SpawnError, never left holding it
  * stop() only asks, and its escalation runs on its own thread; a window that is about to exit calls
    stop_and_wait() instead, which does the bounded wait and the SIGKILL escalation on the caller's thread, because
    a thread of a process that is ending cannot be relied on to finish the cleanup

The queue is the supported interface: a callback runs on its own dispatcher thread, in the queue's order, and must
not touch Tk directly.
"""
from __future__ import annotations

import codecs
import os
import queue
import select
import shlex
import signal
import subprocess
import sys
import threading
import time

from .events import ECHO, RED, Exited, Line
from .markers import parse_line
from .redact import redact, redact_event

POSIX = os.name != 'nt'
DEFAULT_GRACE = 5.0            # SIGTERM to SIGKILL, for a run that has not ended at all
DEFAULT_GROUP_GRACE = 2.0      # what is left of the process group once the tool itself has exited
DEFAULT_READER_GRACE = 2.0     # how long the last line may take once the group is gone
READER_STOP = 1.0              # how long the reader gets to leave once its pipe has been abandoned
READER_POLL = 0.1              # how often the reader checks for a stop request while the pipe is quiet
READER_CHUNK = 65536           # bytes asked of the pipe at a time
SETTLE = 0.2                   # a group usually empties on its own: give it this long before acting
PROC = '/proc'                 # Linux, where a zombie can be told from a live process


def _pollable(fd):
    """True when this pipe can be waited on with select() without reading it.

    POSIX pipes can. A Windows pipe cannot be waited on that way (select() there takes sockets only), so the
    reader falls back to a blocking read and can only leave once the pipe closes; see _read.
    """
    try:
        select.select([fd], [], [], 0)
    except (OSError, ValueError):
        return False
    return True


def _readable(fd, timeout):
    """True when the pipe has data or an EOF to read now, or within `timeout` seconds. Never reads."""
    try:
        return bool(select.select([fd], [], [], timeout)[0])
    except (OSError, ValueError):
        return True     # it stopped being pollable: a blocking read is the only thing left to try


class SpawnError(RuntimeError):
    """The program could not be started (missing interpreter, bad path, not executable)."""


class AlreadyRunning(RuntimeError):
    """Another supervised run is still going: the caller must wait for it to end before starting another."""


class SupervisedProcess:
    """One external tool, watched. Create it, start() it, drain() its events, stop() it when asked."""

    _slot = threading.Lock()   # class-wide, so two windows (or a forgotten run) can never build at once

    def __init__(self, argv, *, cwd=None, env=None, home=None, redact_output=True, on_event=None,
                 reader_grace=DEFAULT_READER_GRACE, group_grace=DEFAULT_GROUP_GRACE):
        self._check_argv(argv)
        self.argv = [str(a) for a in argv]
        self.cwd = str(cwd) if cwd else None
        self.env = dict(env) if env is not None else dict(
            os.environ, PYTHONUNBUFFERED='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        self.home = home
        self.redact_output = redact_output
        self.on_event = on_event
        self._events = queue.Queue()
        self._emit_lock = threading.RLock()   # orders every emission, and seals the queue with Exited
        self._sealed = False                  # guarded by _emit_lock: no event may follow Exited
        self._callback_queue = queue.Queue()  # events waiting for on_event, in the event queue's own order
        self._dispatcher = None               # the one thread that calls on_event; started when there is one
        self._proc = None
        self._cancelled = False
        self._claimed = False
        self._exited = threading.Event()
        self._reader_done = threading.Event()
        self._read_broken = False            # the reader could not finish its pipe: that is never "complete"
        self._closing = threading.Event()    # set when the pipe is abandoned: what is left in it is not ours
        self._pgid = None                   # this run's process group: the tool's own pid on POSIX
        self.reader_grace = float(reader_grace)
        self.group_grace = float(group_grace)
        self._t0 = 0.0
        self.exit_event = None   # the Exited event, once it has been emitted
        self.started_at = None

    # --- construction helpers -------------------------------------------------------------------
    @staticmethod
    def _check_argv(argv):
        if isinstance(argv, (str, bytes)):
            raise SpawnError('argv must be a list of arguments, not a command line: the tools are started '
                             'without a shell, so nothing has to be quoted and nothing is interpreted')
        if not isinstance(argv, (list, tuple)) or not argv:
            raise SpawnError('argv must be a non-empty list of arguments')
        bad = [a for a in argv if not isinstance(a, str) or not a]
        if bad:
            raise SpawnError(f'argv has an empty or non-string argument: {bad!r}')
        if any('\0' in a for a in argv):
            # Popen raises ValueError for it, after the slot has been taken: refuse it here, where nothing is held.
            raise SpawnError('argv has a NUL in an argument: an argument cannot contain one')

    # --- lifecycle ------------------------------------------------------------------------------
    def start(self):
        """Start the tool. Raises AlreadyRunning when another run holds the slot, SpawnError when it cannot run."""
        if self._proc is not None:
            raise SpawnError('this run was already started')
        if not self._slot.acquire(blocking=False):
            raise AlreadyRunning('another build is running: stop it or wait for it to end')
        self._claimed = True
        try:
            # Binary and unbuffered: the reader takes the pipe's bytes from its own descriptor (see _read), so no
            # buffered Python stream sits between the tool and the supervisor, and nothing reads self._proc.stdout.
            kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          bufsize=0, cwd=self.cwd, env=self.env)
            if POSIX:
                # One session for the tool and every program it starts, so stop() reaches the rewriter and the
                # shader build too, not only the Python process in front of them.
                kwargs['start_new_session'] = True
            else:   # Windows: a new process group, so a console Ctrl-Break can reach it
                kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            self._proc = subprocess.Popen(self.argv, **kwargs)
            if POSIX:
                # start_new_session made the tool the leader of a new session and process group before it ran, so
                # that group's id is the tool's own pid. Recording it here is exact and cannot race: asking the OS
                # (os.getpgid) after the start fails for a parent that has already exited, and the group would then
                # be taken for gone while the child it started was still running.
                self._pgid = self._proc.pid
        except (OSError, ValueError, TypeError) as e:
            # ValueError (a NUL the check above did not see, an environment value that is not a string) and
            # TypeError (an unusable env) are raised after the slot is taken, exactly like OSError: free it, or the
            # supervisor stays busy for the life of the window and no run can ever start.
            self._release()
            raise SpawnError(f'cannot run {self.argv[0]}: {e}') from None
        self._t0 = time.monotonic()
        self.started_at = time.time()
        # The echo is the window's own line, but the argv it prints can name the user's home folder (a keys path,
        # a pack's output folder), so it passes through the same redaction as the tool's own lines.
        echo = '$ ' + shlex.join(self.argv)
        self._emit(Line(redact(echo, self.home) if self.redact_output else echo, ECHO))
        threading.Thread(target=self._read, name='peanuts-read', daemon=True).start()
        threading.Thread(target=self._wait, name='peanuts-wait', daemon=True).start()
        return self

    def _read(self):
        """Turn the pipe's bytes into events until EOF, or until this run is told to stop reading.

        The pipe is read with a bounded wait on its descriptor, never by iterating a buffered TextIOWrapper: that
        blocks inside the stream's own lock, and closing it from the supervisor thread to break the block is
        neither safe nor bounded. Here the supervisor only sets _closing, and this loop notices within
        READER_POLL. So an escaped descendant that inherited the pipe and keeps it open cannot hold the run: the
        reader leaves, the stream is closed by the thread that owns it, and Exited reports output_complete=False.
        """
        stream = self._proc.stdout if self._proc is not None else None
        if stream is None:
            self._reader_done.set()
            return
        fd = stream.fileno()
        pollable = _pollable(fd)
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        pending = ''
        try:
            while not self._closing.is_set():
                if pollable and not _readable(fd, READER_POLL):
                    continue     # quiet: look for a stop request, then wait again
                chunk = os.read(fd, READER_CHUNK)
                if not chunk:
                    break        # EOF: every writer of this pipe is gone
                pending += decoder.decode(chunk)
                complete, pending = self._take_lines(pending)
                for raw in complete:
                    self._deliver(raw)
            if self._closing.is_set():
                return           # abandoned: what is left in the pipe belongs to a run that has already ended
            pending += decoder.decode(b'', final=True)
            complete, tail = self._take_lines(pending)
            for raw in complete:
                self._deliver(raw)
            if tail.strip('\r'):
                self._deliver(tail)   # a last line with no newline is still a line
        except (OSError, ValueError):
            # The pipe went away under us. That is not a complete read, so Exited must not call it one.
            self._read_broken = True
        finally:
            try:
                stream.close()   # only this thread ever touches the stream
            except OSError:
                pass
            self._reader_done.set()

    def _deliver(self, raw):
        """One line of output as events, redacted. Never emits once the pipe has been abandoned."""
        if self._closing.is_set():
            return
        for event in parse_line(raw):
            # Every field an event carries: a line's text, a Progress label, a Stage line, and the pack path a Done
            # names (the folder the window offers to open, and the one a report quotes).
            self._emit(redact_event(event, self.home) if self.redact_output else event)

    @staticmethod
    def _take_lines(pending):
        """The complete lines in `pending`, and the unterminated tail.

        LF, CRLF and a lone CR all end a line, as they did when this loop read a TextIOWrapper. A CR at the very
        end is held back, because the LF of a CRLF can arrive in the next read.
        """
        held = ''
        if pending.endswith('\r'):
            held, pending = '\r', pending[:-1]
        pending = pending.replace('\r\n', '\n').replace('\r', '\n')
        *complete, tail = pending.split('\n')
        return complete, tail + held

    def _wait(self):
        code = self._proc.wait()
        # Order matters: the group is ended first, because a child that inherited stdout is the reason the pipe
        # can still be open once the tool itself has finished. Only then is the last line judged.
        gone, note = self._finish_group()
        complete = self._finish_output()
        event = Exited(code, self._cancelled, round(time.monotonic() - self._t0, 3),
                       output_complete=complete,
                       cleanup=note if gone else 'the process group was still alive after SIGKILL')
        # Exited is queued before anything can show that the run ended. A window that saw the slot free - or
        # exit_event set - while the terminal event was still on its way could drop the run and lose that event,
        # its status line and its button state. So the event goes in first, and only then are the run's state and
        # the slot published: from here on, "not busy" and "has an exit_event" both mean Exited is already queued.
        self._emit(event, last=True)   # the last lifecycle event: the queue is sealed with it
        self.exit_event = event
        self._exited.set()
        if gone:
            self._release()
        if not gone:
            # Fail closed. The slot stays taken until the group really is empty; if it never is, this process
            # cannot start another build, which is the safe end of the trade.
            threading.Thread(target=self._watch_group, name='peanuts-group', daemon=True).start()

    def _finish_group(self):
        """Wait for this run's process group to be empty, ending what is left of it.

        Returns (gone, note). `gone` is False only when the group survived SIGKILL. A process that has exited
        but not been reaped yet is a zombie, not something that can still be writing: on Linux it does not count.
        """
        if self._pgid is None:
            return True, ''      # Windows: only the process itself is visible, and it has already exited
        deadline = time.monotonic() + SETTLE
        while time.monotonic() < deadline:
            if not self._group_alive():
                return True, ''
            time.sleep(0.02)
        note = ('a child process outlived the tool: ending the rest of its process group' if not self._cancelled
                else 'a child process ignored the stop request: ending its process group')
        self._emit(Line(note, ECHO))
        self._signal_group(signal.SIGTERM)
        deadline = time.monotonic() + self.group_grace
        while time.monotonic() < deadline:
            if not self._group_alive():
                return True, note
            time.sleep(0.02)
        self._signal_group(signal.SIGKILL)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if not self._group_alive():
                return True, note
            time.sleep(0.02)
        return False, note

    def _finish_output(self):
        """True when the reader genuinely finished and every line it read has already been queued.

        The wait is a bound, not proof: a descendant that inherited stdout can hold the pipe open. When that
        happens the reader is asked to stop (by `_closing`, never by closing the stream from this thread), gets
        READER_STOP seconds to leave, and the tail is then declared missing rather than the run being called
        clean. Nothing the reader holds can reach the queue after Exited: _emit seals it.
        """
        if self._reader_done.wait(self.reader_grace):
            return not self._read_broken
        self._closing.set()
        # Bounded either way: a reader left inside a read this platform cannot poll (a Windows pipe) is a daemon
        # thread whose events the seal now drops, so the run still ends and says its output is incomplete.
        self._reader_done.wait(READER_STOP)
        self._emit(Line('[RED] the output pipe stayed open after the tool ended: the last lines may be missing',
                        RED))
        return False

    def _group_alive(self):
        """True while a process of this run's group is still running. Never signals anything."""
        if self._pgid is None:
            return False
        if os.path.isdir(PROC):
            # A zombie is a program that has already ended: it cannot write to the pack folder, so it must not
            # keep the slot taken. /proc is how to tell it apart without signalling it.
            try:
                for name in os.listdir(PROC):
                    if not name.isdigit():
                        continue
                    try:
                        with open(f'{PROC}/{name}/stat', 'rb') as fh:
                            stat = fh.read()
                    except OSError:
                        continue
                    fields = stat[stat.rfind(b')') + 2:].split()
                    if len(fields) >= 3 and fields[2] == str(self._pgid).encode() and fields[0] != b'Z':
                        return True
                return False
            except OSError:
                pass   # no usable /proc: fall back to the group probe below
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True      # something is there, but it is not ours to signal
        except OSError:
            return False
        return True

    def _signal_group(self, sig):
        """Signal this run's process group, and only when the run is that group's leader.

        start_new_session makes the tool the leader, so its group holds the tool and every program it started.
        The pgid == pid check is the safety net: any other pgid would be somebody else's group.
        """
        proc = self._proc
        if proc is None:
            return False
        try:
            if self._pgid is not None and self._pgid == proc.pid:
                os.killpg(self._pgid, sig)
            elif POSIX:
                return False     # not a group leader: refuse rather than guess at a pid that may have been reused
            elif sig == signal.SIGKILL:
                proc.kill()
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            return False
        return True

    def _watch_group(self):
        """Keep the slot until the group is really gone, then give it back. Silent: Exited has already been sent."""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if not self._group_alive():
                self._release()
                return
            time.sleep(0.1)

    def stop(self, grace=DEFAULT_GRACE):
        """Ask the run to stop, then kill it if it has not ended after `grace` seconds. Safe to call twice."""
        proc = self._proc
        if proc is None:
            return False
        if proc.poll() is not None and not self._group_alive():
            return False   # the tool is gone and so is everything it started
        self._cancelled = True
        self._emit(Line('stopping… (a later run resumes: finished modules and translated shaders are kept)', ECHO))
        self._signal_group(signal.SIGTERM)
        threading.Thread(target=self._kill_after, args=(float(grace),), daemon=True).start()
        return True

    def _kill_after(self, grace):
        """Escalate to SIGKILL when the group is still there after `grace`.

        The group, not the tool: a tool that has exited while one of its children is still working is exactly
        the case this exists for.
        """
        deadline = time.monotonic() + max(0.0, grace)
        while time.monotonic() < deadline:
            if not self._group_alive():
                return
            time.sleep(0.05)
        if not self._group_alive():
            return
        self._emit(Line(f'did not stop after {grace:g} s: ending it', ECHO))
        self._signal_group(signal.SIGKILL)

    def settled(self):
        """True when this run has ended and its whole process group is gone: nothing is left to clean up.

        The window asks this before it closes. A child that outlived the tool, or ignored the stop request, is
        exactly what a window that closed over its run would leave behind.
        """
        if self._proc is None:
            return True
        return self._exited.is_set() and not self._group_alive()

    def stop_and_wait(self, grace=DEFAULT_GRACE, timeout=None):
        """stop(), then wait until the whole group is gone, bounded. True when nothing is left of the run.

        This is what a window calls before it exits. stop() only asks, and its SIGKILL escalation runs on a thread
        of a process that is about to end, so it cannot be relied on there; here both happen in the caller's thread
        and within the caller's bounds, so the window can show "stopping…" and close only once the children are
        really gone. Never unbounded: a group that survives SIGKILL is reported by False, not waited on.
        """
        if self._proc is None or self.settled():
            return True
        if timeout is None:
            timeout = float(grace) + self.group_grace + 5.0
        deadline = time.monotonic() + float(timeout)
        kill_at = time.monotonic() + max(0.0, float(grace))
        self.stop(grace=grace)
        killed = False
        while True:
            if self.settled():
                return True
            now = time.monotonic()
            if now >= deadline:
                return False
            if not killed and now >= kill_at:
                self._emit(Line(f'did not stop after {float(grace):g} s: ending it', ECHO))
                self._signal_group(signal.SIGKILL)
                killed = True
            time.sleep(0.02)

    def wait(self, timeout=None):
        """True when the run ended within `timeout` seconds (None waits until it does)."""
        return self._exited.wait(timeout)

    def run(self, timeout=None):
        """start(), wait, and return the Exited event (or None on timeout). A convenience for scripts and tests."""
        self.start()
        return self.exit_event if self._exited.wait(timeout) else None

    # --- reading --------------------------------------------------------------------------------
    def drain(self, timeout=None):
        """Every event available now. With a timeout, wait that long for the first one."""
        out = []
        try:
            out.append(self._events.get(timeout=timeout))
        except queue.Empty:
            return out
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    def _emit(self, event, *, last=False):
        """Queue one event in order, and hand it to the window's callback; False once the queue is sealed.

        The lock is what makes Exited the last event: the reader's lines and the supervisor's final event cannot
        interleave, so a line the reader had already parsed is either queued before Exited or dropped after it.

        The callback is called by the dispatcher thread, in the queue's own order, never by the thread that emitted
        the event and never while _emit_lock is held (WO#012). A callback that waits — for a dialog, another thread,
        another event — would otherwise hold the lock, and every later emission would stop there: _wait could not
        queue Exited, _exited would never be set, so wait() would never return, and a stop() from the window (which
        emits too) would freeze the window's own thread. The dispatcher keeps that wait off the lifecycle and still
        gives the callback the events in FIFO order, Exited last.
        """
        with self._emit_lock:
            if self._sealed:
                return False
            self._events.put(event)
            if self.on_event is not None:
                if self._dispatcher is None:
                    self._dispatcher = threading.Thread(target=self._dispatch, name='peanuts-events', daemon=True)
                    self._dispatcher.start()
                self._callback_queue.put(event)   # under the same lock, so its order is the event queue's
            if last:
                self._sealed = True
        return True

    def _dispatch(self):
        """Call on_event for each queued event, in order, off the reader and lifecycle threads; ends after Exited."""
        while True:
            event = self._callback_queue.get()
            try:
                self.on_event(event)
            except Exception:   # a window callback must never take the dispatcher thread down with it
                pass
            if isinstance(event, Exited):
                return

    # --- state ----------------------------------------------------------------------------------
    @property
    def running(self):
        return self._proc is not None and self._proc.poll() is None

    @property
    def returncode(self):
        return None if self._proc is None else self._proc.poll()

    @property
    def cancelled(self):
        return self._cancelled

    @classmethod
    def busy(cls):
        """True while any run in this process holds the slot (the window disables Start with this)."""
        return cls._slot.locked()

    def _release(self):
        if self._claimed:
            self._claimed = False
            try:
                self._slot.release()
            except RuntimeError:
                pass


def python_argv(script, *args):
    """[this interpreter, the tool's script, *args]: the tools run on the same Python as the window."""
    return [sys.executable, str(script), *[str(a) for a in args]]
