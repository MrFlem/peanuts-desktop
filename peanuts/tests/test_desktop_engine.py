#!/usr/bin/env python3
"""peanuts/desktop/engine: the marker contract, the redaction, and supervision run against real child processes
(no packer, no game data, no display). The child processes are this same interpreter, so the tests need nothing
installed beyond the standard library."""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from peanuts.desktop.engine import (AlreadyRunning, Done, Exited, Line, Progress, SpawnError, Stage,
                                    SupervisedProcess, parse_line, redact, redact_event)
from peanuts.desktop.engine.events import ECHO
from peanuts.desktop.engine.redact import SERIAL_PLACEHOLDER, SERIALS, protect_serial

# The four kinds of line the tools actually print, in one child process. Kept as source text so the test exercises
# the real stdout path (a pipe, unbuffered) rather than a list of strings fed to the parser.
CHILD_MARKERS = (
    'print("progress: 1/4", flush=True)\n'
    'print("progress 2 4 Building the shader library", flush=True)\n'
    'print("  main: rewriting", flush=True)\n'
    'print("done: /tmp/pack (12.3 MiB); RomFS base/romfs and 0 DLC stay", flush=True)\n'
)

SLEEPER = 'import time; print("ready", flush=True); time.sleep(30)'


def child(source):
    return [sys.executable, '-u', '-c', source]


class ParkedLineReader(SupervisedProcess):
    """A reader that has a line in hand and has not queued it yet: the moment D7's ordering is about.

    The line has been read from the pipe, _closing is not set, and the supervisor is about to finish. Parking
    here makes that race deterministic instead of a timing accident.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parked = threading.Event()
        self.let_go = threading.Event()

    def _deliver(self, raw):
        if raw == 'park me' and not self.parked.is_set():
            self.parked.set()
            self.let_go.wait(30)   # the run finishes while this line waits to be emitted
        super()._deliver(raw)


class ReleaseWatcher(SupervisedProcess):
    """Records what a window could observe at the moment the run slot came back.

    B3's race was between publishing `exit_event` / freeing the slot and queueing Exited: a window polling
    `busy()` could drop the run and never see its terminal event. Recording both at `_release()` makes the order
    a fact of the run rather than a matter of timing.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.at_release = None

    def _release(self):
        if self._claimed:
            queued = list(self._events.queue)
            self.at_release = dict(exit_event=self.exit_event, last=queued[-1] if queued else None)
        super()._release()


class Markers(unittest.TestCase):
    def test_plain_line_is_one_info_event(self):
        events = parse_line('reading the game folder')
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], Line)
        self.assertEqual(events[0].severity, 'info')

    def test_red_line_has_red_severity(self):
        (line,) = parse_line('[RED] no profile in dm/games for this game')
        self.assertEqual(line.severity, 'red')

    def test_shader_split_progress(self):
        events = parse_line('progress: 12/40')
        progress = [e for e in events if isinstance(e, Progress)]
        self.assertEqual(len(progress), 1)
        self.assertEqual((progress[0].done, progress[0].total), (12, 40))
        self.assertIn('Splitting', progress[0].label)

    def test_shader_library_progress_and_its_tail(self):
        events = parse_line('progress 7 40 Building (main.patched)')
        progress = [e for e in events if isinstance(e, Progress)]
        self.assertEqual((progress[0].done, progress[0].total), (7, 40))
        self.assertIn('(main.patched)', progress[0].label)

    def test_shader_library_progress_without_tail(self):
        events = parse_line('progress 7 40')
        progress = [e for e in events if isinstance(e, Progress)]
        self.assertEqual(progress[0].label, 'Building the shader library')

    def test_rewrite_line_is_a_stage(self):
        stages = [e for e in parse_line('  main: rewriting') if isinstance(e, Stage)]
        self.assertEqual(len(stages), 1)
        self.assertIn('main', stages[0].text)

    def test_stage_words(self):
        for text, wanted in (('shaders: splitting', 'Splitting'),
                             ('shaders: building', 'Building')):
            stages = [e for e in parse_line(text) if isinstance(e, Stage)]
            self.assertIn(wanted, stages[0].text)

    def test_done_carries_the_pack_path(self):
        events = parse_line('done: /tmp/pack (12.3 MiB); RomFS base/romfs and 0 DLC stay')
        done = [e for e in events if isinstance(e, Done)]
        self.assertEqual(done[0].path, '/tmp/pack')

    def test_done_code_only_names_the_folder(self):
        done = [e for e in parse_line('done: the modules in /tmp/pack (--code-only: pack.tsv and the shaders '
                                      'unchanged)') if isinstance(e, Done)]
        self.assertEqual(done[0].path, '/tmp/pack')

    def test_progress_fraction_is_clamped(self):
        self.assertEqual(Progress(1, 4, 'x').fraction, 0.25)
        self.assertEqual(Progress(9, 4, 'x').fraction, 1.0)
        self.assertEqual(Progress(1, 0, 'x').fraction, 0.0)


class Redaction(unittest.TestCase):
    def test_the_real_home_becomes_a_tilde(self):
        home = os.path.expanduser('~')
        self.assertEqual(redact(f'{home}/games/x'), '~/games/x')

    def test_another_users_home_is_masked(self):
        self.assertEqual(redact('/home/alice/games/x'), '/home/<user>/games/x')

    def test_macos_homes_are_masked(self):
        self.assertEqual(redact('/Users/alice/Library/x'), '/Users/<user>/Library/x')

    def test_windows_homes_are_masked(self):
        self.assertIn('<user>', redact(r'C:\Users\alice\games\x'))

    def test_idempotent(self):
        once = redact('/home/alice/games/x')
        self.assertEqual(redact(once), once)

    def test_a_line_without_a_path_is_untouched(self):
        self.assertEqual(redact('progress: 1/4'), 'progress: 1/4')


class RedactedEvents(unittest.TestCase):
    """B2: every field an event carries is redacted, not only a Line's text and a Done's path.

    A `progress` or `done:` line is parsed into a second copy of its own text, in another field: the window shows
    that copy in its status line and its progress bar, so redacting only the Line left the path in plain sight.
    """

    HOME = '/home/' + 'alice'
    SERIAL = 'PEANUTS00EVENTSERIAL'   # fictional, and the shape and length of a real one

    def tearDown(self):
        SERIALS.clear()   # the registry is process-wide: never leak a value into another test

    def test_a_progress_label_is_redacted(self):
        event = redact_event(Progress(1, 4, 'Building ' + self.HOME + '/games/x'), self.HOME)
        self.assertNotIn(self.HOME, event.label)
        self.assertIn('~/games/x', event.label)
        self.assertEqual((event.done, event.total), (1, 4))

    def test_a_stage_text_is_redacted(self):
        event = redact_event(Stage('Writing ' + self.HOME + '/games/x'), self.HOME)
        self.assertNotIn(self.HOME, event.text)

    def test_a_line_is_redacted(self):
        self.assertNotIn(self.HOME, redact_event(Line('at ' + self.HOME + '/games/x'), self.HOME).text)

    def test_a_done_path_is_redacted(self):
        self.assertNotIn(self.HOME, redact_event(Done(self.HOME + '/games/x'), self.HOME).path)

    def test_a_registered_serial_is_masked_in_every_field(self):
        protect_serial(self.SERIAL)
        line = redact_event(Line(self.SERIAL), self.HOME)
        progress = redact_event(Progress(1, 2, self.SERIAL), self.HOME)
        stage = redact_event(Stage(self.SERIAL), self.HOME)
        done = redact_event(Done(self.SERIAL), self.HOME)
        self.assertNotIn(self.SERIAL, line.text)
        self.assertNotIn(self.SERIAL, progress.label)
        self.assertNotIn(self.SERIAL, stage.text)
        self.assertNotIn(self.SERIAL, done.path)

    def test_an_event_with_nothing_to_redact_is_unchanged(self):
        event = Progress(1, 4, 'Splitting the shaders out of the dump')
        self.assertEqual(redact_event(event, self.HOME), event)

    def test_a_progress_line_from_a_real_child_carries_no_home_folder(self):
        run = SupervisedProcess(child('print("progress 1 2 ' + self.HOME + '/games/x", flush=True)'),
                                home=self.HOME)
        run.run(timeout=30)
        progresses = [e for e in run.drain() if isinstance(e, Progress)]
        self.assertTrue(progresses, 'the run emitted no Progress event')
        self.assertNotIn(self.HOME, progresses[0].label)
        self.assertIn('~/games/x', progresses[0].label)


class Supervision(unittest.TestCase):
    def tearDown(self):
        # A leaked run would hold the class-wide slot and fail every later test: always leave it clear.
        deadline = time.monotonic() + 10
        while SupervisedProcess.busy() and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_a_shell_line_is_refused(self):
        with self.assertRaises(SpawnError):
            SupervisedProcess('echo hello')
        with self.assertRaises(SpawnError):
            SupervisedProcess([])

    def test_a_missing_program_is_a_spawn_error_and_frees_the_slot(self):
        with self.assertRaises(SpawnError):
            SupervisedProcess(['/nonexistent/peanuts-test-program']).start()
        self.assertFalse(SupervisedProcess.busy())

    def test_an_argument_the_os_cannot_pass_is_refused_and_frees_the_slot(self):
        # Popen raises ValueError (not OSError) for a NUL, after the slot has been taken: the supervisor used to stay
        # busy for the life of the window, and no run could ever start again.
        with self.assertRaises(SpawnError):
            SupervisedProcess(child('pass') + ['bad' + chr(0) + 'arg']).start()
        self.assertFalse(SupervisedProcess.busy())

    def test_an_unusable_environment_is_refused_and_frees_the_slot(self):
        # The same failure one layer down: Popen raises TypeError for an env value that is not a string.
        with self.assertRaises(SpawnError):
            SupervisedProcess(child('pass'), env={'NOT_A_STRING': 1}).start()
        self.assertFalse(SupervisedProcess.busy())

    def test_it_reads_the_markers_of_a_real_child(self):
        run = SupervisedProcess(child(CHILD_MARKERS))
        exit_event = run.run(timeout=30)
        self.assertIsNotNone(exit_event)
        self.assertEqual(exit_event.code, 0)
        self.assertTrue(exit_event.ok)

        events = run.drain()
        progresses = [e for e in events if isinstance(e, Progress)]
        dones = [e for e in events if isinstance(e, Done)]
        self.assertEqual([(p.done, p.total) for p in progresses], [(1, 4), (2, 4)])
        self.assertEqual(dones[0].path, '/tmp/pack')
        self.assertIsInstance(events[-1], Exited)
        self.assertEqual(run.returncode, 0)

    def test_a_failing_child_reports_its_code(self):
        run = SupervisedProcess(child('import sys; sys.exit(3)'))
        exit_event = run.run(timeout=30)
        self.assertEqual(exit_event.code, 3)
        self.assertFalse(exit_event.ok)
        self.assertEqual(exit_event.word(), 'failed (exit code 3)')

    def test_output_is_redacted_on_the_way_through(self):
        run = SupervisedProcess(child('import os; print("at", os.path.expanduser("~") + "/games/x", flush=True)'))
        run.run(timeout=30)
        lines = [e.text for e in run.drain() if isinstance(e, Line)]
        self.assertTrue(any('~/games/x' in text for text in lines), lines)

    def test_a_done_path_is_redacted_too(self):
        # The pack path a Done carries is what the window offers to open and a report quotes: same treatment.
        home = '/home/' + 'alice'
        run = SupervisedProcess(child('print("done: ' + home + '/games/x (12.3 MiB)", flush=True)'), home=home)
        run.run(timeout=30)
        dones = [e for e in run.drain() if isinstance(e, Done)]
        self.assertTrue(dones, 'the run emitted no Done event')
        self.assertEqual(dones[0].path, '~/games/x')

    def test_the_command_echo_line_is_redacted_too(self):
        # The echo names the argv, and the argv can carry a keys path or an output folder under the home folder.
        run = SupervisedProcess([sys.executable, '-u', '-c', 'pass', '/home/alice/games/x.nsp'],
                                home='/home/alice')
        run.run(timeout=30)
        echoes = [e for e in run.drain() if isinstance(e, Line) and e.severity == ECHO]
        self.assertTrue(echoes, 'the run printed no command line')
        self.assertIn('~/games/x.nsp', echoes[0].text)
        self.assertNotIn('/home/alice', echoes[0].text)

    def test_red_a_line_hears_about_it(self):
        run = SupervisedProcess(child('print("[RED] shader split read no shader", flush=True)'))
        exit_event = run.run(timeout=30)
        self.assertEqual(exit_event.code, 0)
        lines = [e for e in run.drain() if isinstance(e, Line)]
        self.assertTrue(any(line.severity == 'red' for line in lines))

    def test_one_run_at_a_time(self):
        first = SupervisedProcess(child(SLEEPER))
        first.start()
        try:
            self.assertTrue(SupervisedProcess.busy())
            with self.assertRaises(AlreadyRunning):
                SupervisedProcess(child('pass')).start()
        finally:
            first.stop()
        self.assertTrue(first.wait(15))
        self.assertFalse(SupervisedProcess.busy())

    def test_stop_ends_the_child_and_says_it_was_cancelled(self):
        run = SupervisedProcess(child(SLEEPER))
        run.start()
        # wait for the child to be up, then stop it
        ready = False
        deadline = time.monotonic() + 15
        while not ready and time.monotonic() < deadline:
            ready = any(isinstance(e, Line) and 'ready' in e.text for e in run.drain(timeout=0.2))
        self.assertTrue(ready, 'the child never started')
        self.assertTrue(run.stop(grace=5))
        self.assertTrue(run.wait(15), 'the child did not end after stop()')
        self.assertTrue(run.exit_event.cancelled)
        self.assertFalse(run.exit_event.ok)
        self.assertEqual(run.exit_event.word(), 'stopped')

    def test_stop_on_a_finished_run_is_harmless(self):
        run = SupervisedProcess(child('print("hi", flush=True)'))
        run.run(timeout=30)
        self.assertFalse(run.stop())


class SerialRedaction(unittest.TestCase):
    """A device serial is masked because it was registered, and registering it prints nothing."""

    SERIAL = 'PEANUTS00TESTSERIAL'   # fictional, and the shape and length of a real one

    def tearDown(self):
        SERIALS.clear()   # the registry is process-wide: never leak a value into another test

    def test_a_registered_serial_is_masked(self):
        self.assertTrue(protect_serial(self.SERIAL))
        text = redact(f'device {self.SERIAL} connected')
        self.assertIn(SERIAL_PLACEHOLDER, text)
        self.assertNotIn(self.SERIAL, text)

    def test_every_appearance_is_masked(self):
        protect_serial(self.SERIAL)
        self.assertEqual(redact(f'{self.SERIAL} {self.SERIAL} {self.SERIAL}').count(SERIAL_PLACEHOLDER), 3)

    def test_case_and_a_port_suffix_are_masked(self):
        protect_serial(self.SERIAL)
        text = redact(f'{self.SERIAL.lower()}:5555')
        self.assertTrue(text.startswith(SERIAL_PLACEHOLDER), text)
        self.assertIn('5555', text)

    def test_an_unregistered_alphanumeric_string_is_untouched(self):
        protect_serial(self.SERIAL)
        self.assertEqual(redact('01006BB00C6F0000 and deadbeefcafe'), '01006BB00C6F0000 and deadbeefcafe')

    def test_registration_does_not_expose_the_serial(self):
        protect_serial(self.SERIAL)
        self.assertNotIn(self.SERIAL, repr(SERIALS))

    def test_a_value_too_short_to_match_safely_is_refused(self):
        self.assertFalse(protect_serial('AB'))
        self.assertEqual(redact('AB is everywhere'), 'AB is everywhere')


class ProcessGroupAndOutput(unittest.TestCase):
    """D6 and D7: the process group is what gets stopped and confirmed, and Exited waits for the last line."""

    def tearDown(self):
        deadline = time.monotonic() + 20
        while SupervisedProcess.busy() and time.monotonic() < deadline:
            time.sleep(0.05)

    # --- helpers, so a failure still cleans up after itself -------------------------------------
    @staticmethod
    def _wait_for_pid(pidfile, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                text = pidfile.read_text().strip()
            except OSError:
                text = ''
            if text.isdigit():
                return int(text)
            time.sleep(0.05)
        raise AssertionError(f'the child never wrote {pidfile}')

    @staticmethod
    def _gone(pid, timeout):
        """True once the pid is no longer running. A zombie has ended: it cannot be writing anywhere."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with open(f'/proc/{pid}/stat', 'rb') as fh:
                    stat = fh.read()
            except OSError:
                return True
            if stat[stat.rfind(b')') + 2:].split()[:1] == [b'Z']:
                return True
            time.sleep(0.05)
        return False

    @staticmethod
    def _kill_pid(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    @staticmethod
    def _wait_for_parent(run, timeout=25):
        """The (pid, process group) a supervised parent reported on its own pipe before it exited."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in run.drain(timeout=0.2):
                if isinstance(event, Line) and event.text.startswith('parent '):
                    pid, group = event.text.split()[1:3]
                    return int(pid), int(group)
        raise AssertionError('the parent never reported its pid and process group')

    # --- D6 -------------------------------------------------------------------------------------
    def test_a_child_that_ignores_sigterm_is_ended_and_the_slot_stays_taken(self):
        tmp = Path(tempfile.mkdtemp(prefix='peanuts-group-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        pidfile = tmp / 'child.pid'
        child_src = ('import os, signal, time\n'
                     'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                     f'open({str(pidfile)!r}, "w").write(str(os.getpid()))\n'
                     'while True:\n'
                     '    time.sleep(0.5)\n')
        parent_src = ('import subprocess, sys, time\n'
                      f'subprocess.Popen([sys.executable, "-c", {child_src!r}])\n'
                      'time.sleep(120)\n')
        run = SupervisedProcess(child(parent_src), group_grace=0.5)
        run.start()
        child_pid = self._wait_for_pid(pidfile)
        self.addCleanup(self._kill_pid, child_pid)   # even when an assertion below fails

        self.assertTrue(run.stop(grace=5))
        self.assertTrue(SupervisedProcess.busy(), 'the slot came free while the process group was still alive')
        with self.assertRaises(AlreadyRunning):
            SupervisedProcess(child('pass')).start()

        self.assertTrue(run.wait(30), 'the run never ended')
        self.assertTrue(run.exit_event.cancelled)
        self.assertTrue(run.exit_event.cleanup, 'the supervisor never reported the child it had to end')
        self.assertTrue(self._gone(child_pid, 15), f'child {child_pid} survived the stop')
        self.assertFalse(SupervisedProcess.busy(), 'the slot was never given back')

    # --- WO#011: the group is the leader's own pid, never a post-start lookup -------------------
    def test_a_parent_that_exits_at_once_keeps_its_group_identified_and_ended(self):
        # start_new_session makes the tool the leader of its own process group, so that group's id is its pid. The
        # old code asked the OS for it after the start; a parent that exits at once can be gone before the question,
        # the lookup fails, the group is taken for empty, the slot comes free and the child it started is left
        # running. The lookup is made to fail here, exactly as it does in that race.
        if os.name == 'nt':
            self.skipTest('POSIX process groups only')
        tmp = Path(tempfile.mkdtemp(prefix='peanuts-pgid-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        pidfile = tmp / 'grandchild.pid'
        grandchild = ('import os, signal, time\n'
                      'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                      f'open({str(pidfile)!r}, "w").write(str(os.getpid()))\n'
                      'time.sleep(120)\n')
        parent = ('import os, subprocess, sys, time\n'
                  f'subprocess.Popen([sys.executable, "-u", "-c", {grandchild!r}])\n'
                  # Leave as soon as the child reports that it is up, with no delay of its own: the parent is then
                  # gone while the child it started is still running, which is the race.
                  'deadline = time.monotonic() + 20\n'
                  f'while not os.path.exists({str(pidfile)!r}) and time.monotonic() < deadline:\n'
                  '    time.sleep(0.01)\n'
                  'print("parent %d %d" % (os.getpid(), os.getpgid(0)), flush=True)\n')
        run = SupervisedProcess(child(parent), group_grace=1.0)
        lookup = mock.Mock(side_effect=ProcessLookupError(3, 'No such process'))
        with mock.patch('peanuts.desktop.engine.process.os.getpgid', lookup):
            run.start()
        parent_pid, parent_group = self._wait_for_parent(run)
        grandchild_pid = self._wait_for_pid(pidfile)
        self.addCleanup(self._kill_pid, grandchild_pid)   # cleaned up even when an assertion below fails
        lookup.assert_not_called()   # the group is the pid the start already gave us, not an answer to ask for
        self.assertEqual(parent_group, parent_pid, 'the tool was not the leader of its own process group')
        self.assertEqual(run._pgid, parent_pid, 'the supervisor did not identify the group by its leader pid')
        self.assertTrue(self._gone(parent_pid, 15), f'the parent {parent_pid} did not exit')
        self.assertTrue(run._group_alive(), 'the group was called empty while the child it started was running')
        self.assertTrue(SupervisedProcess.busy(), 'the slot came free while the process group was still alive')
        with self.assertRaises(AlreadyRunning):
            SupervisedProcess(child('pass')).start()

        self.assertTrue(run.wait(30), 'the run never ended')
        exit_event = run.exit_event
        self.assertFalse(exit_event.cancelled)
        self.assertIn('outlived', exit_event.cleanup, 'the supervisor never reported the child it had to end')
        self.assertTrue(exit_event.output_complete, 'output whose writers had all been ended was called incomplete')
        self.assertTrue(self._gone(grandchild_pid, 15), f'child {grandchild_pid} survived the supervisor')
        self.assertFalse(SupervisedProcess.busy(), 'the slot was never given back')
        events = run.drain()
        self.assertEqual(sum(1 for e in events if isinstance(e, Exited)), 1)
        self.assertIsInstance(events[-1], Exited, 'something was emitted after Exited')

    # --- D7: a line held at shutdown ------------------------------------------------------------
    def test_a_line_in_hand_when_shutdown_begins_cannot_follow_exited(self):
        run = ParkedLineReader(child('print("first", flush=True)\nprint("park me", flush=True)'),
                               reader_grace=0.3)
        run.start()
        self.assertTrue(run.parked.wait(20), 'the reader never reached the parked line')
        self.assertTrue(run.wait(30), 'the supervisor waited for a line the reader was holding')
        self.assertFalse(run.exit_event.output_complete,
                         'a run whose last line never arrived was reported as complete')
        run.let_go.set()
        self.assertTrue(run._reader_done.wait(10), 'the reader thread was left blocked')
        events = run.drain()
        self.assertEqual(sum(1 for e in events if isinstance(e, Exited)), 1)
        self.assertIsInstance(events[-1], Exited, 'something was emitted after Exited')
        lines = [e.text for e in events if isinstance(e, Line)]
        self.assertIn('first', lines)
        self.assertNotIn('park me', lines, 'the held line was emitted, which can only have been after Exited')
        # The queue is sealed too: a late event from any thread is dropped, not queued behind Exited.
        self.assertIs(run._emit(Line('late', 'info')), False, 'the queue accepted an event after Exited')
        self.assertEqual(run.drain(timeout=0), [])

    def test_a_pipe_that_breaks_under_the_reader_is_not_called_complete(self):
        run = SupervisedProcess(child(SLEEPER))
        run.start()
        ready = False
        deadline = time.monotonic() + 15
        while not ready and time.monotonic() < deadline:
            ready = any(isinstance(e, Line) and 'ready' in e.text for e in run.drain(timeout=0.2))
        self.assertTrue(ready, 'the child never started')
        os.close(run._proc.stdout.fileno())   # the writing end is gone: the reader can no longer finish its pipe
        self.assertTrue(run.stop(grace=5))
        self.assertTrue(run.wait(15), 'the run never ended')
        self.assertFalse(run.exit_event.output_complete, 'output that was cut off was reported as complete')
        self.assertTrue(run._reader_done.wait(5), 'the reader did not leave after its pipe broke')

    def test_an_escaped_descendant_holding_the_pipe_cannot_hang_the_run(self):
        tmp = Path(tempfile.mkdtemp(prefix='peanuts-escaped-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        pidfile = tmp / 'escaped.pid'
        escaped = tmp / 'escaped.py'
        escaped.write_text('import os, time\n'
                           'os.setsid()\n'   # a session of its own: the run's process group no longer holds it
                           f'open({str(pidfile)!r}, "w").write(str(os.getpid()))\n'
                           'time.sleep(120)\n')
        parent = ('import subprocess, sys\n'
                  f'subprocess.Popen([sys.executable, "-u", {str(escaped)!r}])\n'
                  'print("parent done", flush=True)\n')
        run = SupervisedProcess(child(parent), reader_grace=1.0, group_grace=1.0)
        run.start()
        pid = self._wait_for_pid(pidfile)
        self.addCleanup(self._kill_pid, pid)      # cleaned up even when an assertion below fails
        started = time.monotonic()
        finished = run.wait(60)
        elapsed = time.monotonic() - started
        self.assertTrue(finished, 'the supervisor hung on a pipe an escaped descendant kept open')
        self.assertLess(elapsed, 30, f'the supervisor took {elapsed:.1f} s to finish')
        exit_event = run.exit_event
        self.assertFalse(exit_event.output_complete, 'output it never read was reported as complete')
        events = run.drain()
        self.assertEqual(sum(1 for e in events if isinstance(e, Exited)), 1)
        self.assertIsInstance(events[-1], Exited, 'something was emitted after Exited')
        self.assertIn('parent done', [e.text for e in events if isinstance(e, Line)])
        self.assertTrue(any(isinstance(e, Line) and 'may be missing' in e.text for e in events),
                        'the run did not say that its output may be missing')
        self.assertTrue(run._reader_done.wait(10), 'the reader thread was left blocked')
        self.assertFalse(SupervisedProcess.busy(), 'the slot was never given back')

    # --- WO#012: the callback cannot hold the lifecycle ------------------------------------------
    def test_a_callback_that_blocks_cannot_hold_the_run_open(self):
        # The callback used to be invoked inside _emit() with _emit_lock held. A callback waiting there held the lock,
        # so _wait could not queue Exited, _exited was never set and wait() never returned; stop() from the window
        # (which emits too) blocked the main thread behind the same lock. Here the callback stays inside its first
        # event for the whole run: the queue, the slot and wait() must finish anyway, in order, Exited last.
        entered = threading.Event()
        release = threading.Event()   # closed for the whole run: the callback is still waiting when the run ends
        self.addCleanup(release.set)  # a failed assertion must not leave this test's thread waiting
        seen = []

        def callback(event):
            seen.append(event)
            entered.set()
            release.wait(30)

        run = SupervisedProcess(child('print("one", flush=True)'), on_event=callback)
        run.start()
        self.assertTrue(entered.wait(20), 'the callback was never called')
        self.assertTrue(run.wait(15), 'a blocked on_event callback held the run open')
        self.assertEqual(run.exit_event.code, 0)
        self.assertFalse(SupervisedProcess.busy(), 'the slot was never given back')

        events = run.drain()
        self.assertEqual(sum(1 for e in events if isinstance(e, Exited)), 1)
        self.assertIsInstance(events[-1], Exited, 'something was emitted after Exited')
        self.assertIn('one', [e.text for e in events if isinstance(e, Line)])
        # The authoritative queue finished while the callback was still inside its first event: the two are
        # independent, which is the correction. The callback had not been given Exited when the run ended.
        self.assertEqual(len(seen), 1, 'the callback received an event while it was blocked in an earlier one')
        self.assertIsInstance(seen[0], Line)

        release.set()   # let it go: what is left of the queue reaches it in order, Exited last
        self.assertIsNotNone(run._dispatcher, 'no dispatcher thread was started for the callback')
        run._dispatcher.join(15)
        self.assertFalse(run._dispatcher.is_alive(), 'the dispatcher did not end after Exited')
        self.assertEqual([type(e) for e in seen], [type(e) for e in events])

    # --- D7: repeated so a timing accident shows up ----------------------------------------------
    def test_repeated_runs_keep_exactly_one_exited_last(self):
        for i in range(12):
            run = SupervisedProcess(child('for i in range(250): print("line %d" % i)'))
            exit_event = run.run(timeout=60)
            self.assertIsNotNone(exit_event, f'run {i} did not finish')
            events = run.drain()
            self.assertEqual(sum(1 for e in events if isinstance(e, Exited)), 1, f'run {i}')
            self.assertIsInstance(events[-1], Exited, f'run {i}: something followed Exited')
            self.assertTrue(exit_event.output_complete, f'run {i}: complete output was called incomplete')
            self.assertEqual(len([e for e in events if isinstance(e, Line) and e.text.startswith('line ')]),
                             250, f'run {i}: a line went missing')

    def test_the_reader_shutdown_race_is_stable_over_repetition(self):
        for i in range(4):
            run = ParkedLineReader(child('print("park me", flush=True)'), reader_grace=0.2)
            run.start()
            self.assertTrue(run.parked.wait(20), f'iteration {i}: the reader never parked')
            self.assertTrue(run.wait(30), f'iteration {i}: the supervisor did not finish')
            run.let_go.set()
            self.assertTrue(run._reader_done.wait(10), f'iteration {i}: the reader stayed blocked')
            events = run.drain()
            self.assertEqual(sum(1 for e in events if isinstance(e, Exited)), 1, f'iteration {i}')
            self.assertIsInstance(events[-1], Exited, f'iteration {i}: something followed Exited')
            self.assertFalse(run.exit_event.output_complete, f'iteration {i}')

    # --- D7: complete output on the normal path ---------------------------------------------------
    def test_all_output_arrives_before_the_one_exit_event(self):
        n = 5000
        run = SupervisedProcess(child(f'for i in range({n}): print("line %d" % i)'))
        exit_event = run.run(timeout=60)
        self.assertIsNotNone(exit_event, 'the run did not finish')
        events = run.drain()
        self.assertEqual(sum(1 for e in events if isinstance(e, Exited)), 1)
        self.assertIsInstance(events[-1], Exited, 'something was emitted after Exited')
        self.assertEqual(sum(1 for e in events if isinstance(e, Line) and e.text.startswith('line ')), n)
        self.assertTrue(exit_event.output_complete)

    # --- B3: the terminal event is in the queue before the run looks finished --------------------
    def test_the_run_slot_comes_back_only_after_exited_is_queued(self):
        run = ReleaseWatcher(child('print("one", flush=True)'))
        run.run(timeout=30)
        self.assertIsNotNone(run.at_release, 'the run slot was never released')
        self.assertIsNotNone(run.at_release['exit_event'], 'the slot came back with no exit event published')
        self.assertIsInstance(run.at_release['last'], Exited,
                              'the slot came back before Exited was queued: a window could drop the run')
        self.assertIs(run.at_release['last'], run.at_release['exit_event'])

    def test_a_stopped_run_keeps_the_same_event_order(self):
        # The stop path ends in the same _wait() as a natural exit; the ordering must hold there too, or a window
        # that pressed Stop could lose the Exited event it is waiting for.
        run = ReleaseWatcher(child(SLEEPER))
        run.start()
        ready = False
        deadline = time.monotonic() + 15
        while not ready and time.monotonic() < deadline:
            ready = any(isinstance(e, Line) and 'ready' in e.text for e in run.drain(timeout=0.2))
        self.assertTrue(ready, 'the child never started')
        self.assertTrue(run.stop(grace=5))
        self.assertTrue(run.wait(15), 'the run did not end after stop()')
        self.assertIsNotNone(run.at_release, 'the run slot was never released')
        self.assertIsInstance(run.at_release['last'], Exited,
                              'the slot came back before Exited was queued on the stop path')
        self.assertIs(run.at_release['last'], run.exit_event)

    # --- B1: the window may not exit over a running check ----------------------------------------
    def test_stop_and_wait_ends_the_group_before_it_reports_settled(self):
        # A child that ignores SIGTERM: stop() alone only asks, and its escalation lives on a thread of a process
        # that is about to end. stop_and_wait() must end it, in the caller's thread, within its own bound.
        run = SupervisedProcess(child('import signal, time\n'
                                      'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                                      'print("ready", flush=True)\n'
                                      'time.sleep(120)'), group_grace=1.0)
        run.start()
        ready = False
        deadline = time.monotonic() + 15
        while not ready and time.monotonic() < deadline:
            ready = any(isinstance(e, Line) and 'ready' in e.text for e in run.drain(timeout=0.2))
        self.assertTrue(ready, 'the child never started')
        started = time.monotonic()
        self.assertTrue(run.stop_and_wait(grace=0.5, timeout=15), 'the run never settled')
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue(run.settled())
        self.assertTrue(run.exit_event.cancelled, 'the run was not reported as stopped')
        self.assertFalse(SupervisedProcess.busy(), 'the slot was never given back')

    def test_stop_and_wait_is_bounded_when_the_group_cannot_be_confirmed_gone(self):
        run = SupervisedProcess(child('pass'))
        run.start()
        with mock.patch.object(SupervisedProcess, 'settled', return_value=False):
            started = time.monotonic()
            self.assertFalse(run.stop_and_wait(grace=0.1, timeout=0.5), 'an unconfirmable group was reported gone')
            self.assertLess(time.monotonic() - started, 5.0, 'stop_and_wait outlived its own timeout')
        self.assertTrue(run.wait(15), 'the run did not finish on its own')

    def test_stop_and_wait_on_a_finished_run_is_true_at_once(self):
        run = SupervisedProcess(child('print("hi", flush=True)'))
        run.run(timeout=30)
        started = time.monotonic()
        self.assertTrue(run.stop_and_wait())
        self.assertLess(time.monotonic() - started, 2.0)

    def test_a_descendant_in_the_group_holding_the_pipe_is_ended_and_output_is_complete(self):
        # The parent starts a child that inherits stdout, then exits at once: the pipe stays open, so the reader
        # cannot end and a two-second guess would either truncate the log or hang forever.
        src = ('import subprocess, sys\n'
               'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
               'print("parent done")\n')
        run = SupervisedProcess(child(src), group_grace=1.0)
        started = time.monotonic()
        exit_event = run.run(timeout=30)
        elapsed = time.monotonic() - started
        self.assertIsNotNone(exit_event, 'the supervisor hung on a pipe a descendant kept open')
        self.assertLess(elapsed, 25)
        events = run.drain()
        self.assertIsInstance(events[-1], Exited, 'something was emitted after Exited')
        self.assertTrue(any(isinstance(e, Line) and 'outlived' in e.text for e in events),
                        'the supervisor did not say that a child outlived the tool')
        self.assertIn('outlived', exit_event.cleanup)
        self.assertTrue(exit_event.output_complete)
        self.assertFalse(SupervisedProcess.busy(), 'the slot was never given back')


class Launcher(unittest.TestCase):
    """peanuts-desktop.sh must take PEANUTS_PYTHON the way peanuts/scripts/peanuts-build.sh takes it: a command
    name as well as a path. A stub interpreter stands in for the real one, so these tests never need Tk or a
    display and never start the window."""

    LAUNCHER = REPO / 'peanuts' / 'desktop' / 'peanuts-desktop.sh'

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='peanuts-launcher-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # The stub answers both of the launcher's probes and never opens a window:
        #   python3 -c 'import sys; sys.exit(...)'   (the version probe, when PEANUTS_PYTHON is not set)
        #   python3 -c 'import tkinter'              (the Tk probe)
        #   python3 -m peanuts.desktop               (the window itself)
        stub = Path(self.tmp) / 'python3'
        stub.write_text('#!/bin/sh\n'
                        'case "$1" in\n'
                        '  -c) exit 0 ;;\n'
                        '  -m) printf "%s\\n" "STUB-OPENED-THE-WINDOW"; exit 0 ;;\n'
                        'esac\n'
                        'exit 0\n')
        stub.chmod(0o755)

    def run_launcher(self, peanuts_python, path_prefix=None):
        env = dict(os.environ, PEANUTS_PYTHON=peanuts_python)
        if path_prefix:
            env['PATH'] = str(path_prefix) + os.pathsep + env.get('PATH', '')
        return subprocess.run(['sh', str(self.LAUNCHER)], env=env, capture_output=True, text=True, timeout=60)

    def test_a_bare_command_name_is_used_as_given(self):
        result = self.run_launcher('python3', self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('STUB-OPENED-THE-WINDOW', result.stdout)
        self.assertNotIn('[RED]', result.stderr)

    def test_a_name_that_is_not_on_path_is_named(self):
        result = self.run_launcher('peanuts-no-such-python')
        self.assertEqual(result.returncode, 1)
        self.assertIn('is not on PATH', result.stderr)

    def test_a_path_that_is_not_executable_is_named(self):
        result = self.run_launcher('/etc/hostname')
        self.assertEqual(result.returncode, 1)
        self.assertIn('is not executable', result.stderr)


if __name__ == '__main__':
    unittest.main()
