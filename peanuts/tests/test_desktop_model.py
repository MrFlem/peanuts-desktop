#!/usr/bin/env python3
"""peanuts/desktop/model: what an input is, what would run, and the rule that keeps the layers apart.

The classification tests use the packer's own readers on throwaway folders, so they need no game data: a dump
needs a real main.npdm, which this test does not invent, and the cases it does cover are the ones a user hits
first (a game file, a folder of them, a folder with neither, a file that is not a .nup)."""
import ast
import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from peanuts.desktop.model import Kind, InputInfo, LatestSelection, classify, plan_for, summary
from peanuts.desktop.model.plan import BUILD_SCRIPT, PACK_SCRIPT
from peanuts.desktop.platform import open_folder
from peanuts.desktop.ui import keys, lifecycle


class Classification(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_game_file_needs_keys(self):
        p = self.dir / 'Game [01006BB00C6F0000].nsp'
        p.write_bytes(b'')
        info = classify(p)
        self.assertIs(info.kind, Kind.FILE)
        self.assertTrue(info.needs_keys)
        self.assertTrue(info.usable)

    def test_a_folder_of_game_files(self):
        (self.dir / 'a.nsp').write_bytes(b'')
        (self.dir / 'b.xci').write_bytes(b'')
        info = classify(self.dir)
        self.assertIs(info.kind, Kind.GAMES)
        self.assertTrue(info.needs_keys)
        self.assertEqual(len(info.files), 2)

    def test_macos_junk_files_are_ignored(self):
        (self.dir / 'a.nsp').write_bytes(b'')
        (self.dir / '._a.nsp').write_bytes(b'')
        self.assertEqual(len(classify(self.dir).files), 1)

    def test_an_appledouble_file_chosen_on_its_own_is_refused_like_the_builder_does(self):
        # B5: the builder's visible() skips `._*`, so offering it as a game file planned a build the builder fails.
        p = self.dir / '._a.nsp'
        p.write_bytes(b'\x00\x05\x16\x07')
        info = classify(p)
        self.assertFalse(info.usable)
        self.assertIn('AppleDouble', info.error)
        self.assertIn("'a.nsp'", info.error)

    def test_a_folder_with_neither_says_so(self):
        info = classify(self.dir)
        self.assertFalse(info.usable)
        self.assertIn('base/exefs', info.error)

    def test_a_text_file_is_refused_by_name(self):
        p = self.dir / 'notes.txt'
        p.write_text('hello')
        info = classify(p)
        self.assertFalse(info.usable)
        self.assertIn('notes.txt', info.error)

    def test_a_missing_path_says_so(self):
        info = classify(self.dir / 'ghost.nup')
        self.assertFalse(info.usable)
        self.assertIn('does not exist', info.error)

    def test_a_broken_nup_becomes_a_message_not_an_exception(self):
        p = self.dir / 'broken.nup'
        p.write_bytes(b'NOT A NUP')
        info = classify(p)
        self.assertIs(info.kind, Kind.NUP)
        self.assertIsNotNone(info.error)


def info(**kw):
    """An InputInfo with the fields a test cares about; the rest defaulted."""
    kw.setdefault('path', Path('/tmp/game'))
    return InputInfo(**kw)


class Plans(unittest.TestCase):
    def test_a_game_file_without_keys_is_blocked(self):
        plan = plan_for(info(kind=Kind.FILE))
        self.assertFalse(plan.runnable)
        self.assertIn('prod.keys', ' '.join(plan.blockers))

    def test_a_game_file_with_keys_runs_the_build_script(self):
        plan = plan_for(info(kind=Kind.FILE), keys='/home/someone/prod.keys')
        self.assertTrue(plan.runnable)
        argv = plan.steps[0].argv
        self.assertIn(BUILD_SCRIPT, ' '.join(argv))
        self.assertIn('--keys', argv)
        self.assertIn('/home/someone/prod.keys', argv)

    def test_a_nup_runs_the_pack_script_and_needs_no_keys(self):
        plan = plan_for(info(kind=Kind.NUP, title_id='01006BB00C6F0000', name="Link's Awakening", has_profile=True))
        self.assertTrue(plan.runnable)
        argv = plan.steps[0].argv
        self.assertIn(PACK_SCRIPT, ' '.join(argv))
        self.assertNotIn('--keys', argv)

    def test_a_dump_update_is_version(self):
        plan = plan_for(info(kind=Kind.DUMP, title_id='01006BB00C6F0000', has_profile=True), update=458752)
        argv = plan.steps[0].argv
        self.assertIn('--version', argv)
        self.assertIn('458752', argv)

    def test_a_game_file_update_is_update(self):
        plan = plan_for(info(kind=Kind.FILE), keys='/k', update='131072')
        argv = plan.steps[0].argv
        self.assertIn('--update', argv)
        self.assertNotIn('--version', argv)

    def test_no_profile_blocks_the_build(self):
        plan = plan_for(info(kind=Kind.NUP, title_id='0100FFFFFFFFFF', has_profile=False))
        self.assertFalse(plan.runnable)
        self.assertIn('dm/games', ' '.join(plan.blockers))

    def test_the_plan_says_it_is_not_run_yet(self):
        plan = plan_for(info(kind=Kind.NUP, title_id='01006BB00C6F0000', has_profile=True))
        self.assertIn('Phase A', ' '.join(plan.notes))

    def test_an_unusable_input_has_its_own_blocker(self):
        plan = plan_for(info(kind=Kind.UNKNOWN, error='not a game folder'))
        self.assertFalse(plan.runnable)
        self.assertEqual(plan.blockers[0], 'not a game folder')

    def test_a_leading_dash_path_is_passed_as_an_argument(self):
        # B5: argparse reads '-x.nsp' as an option; './-x.nsp' is the same file and an argument.
        plan = plan_for(info(kind=Kind.FILE, path=Path('-x.nsp')), keys='/k')
        argv = plan.steps[0].argv
        self.assertIn('./-x.nsp', argv)
        self.assertNotIn('-x.nsp', argv)

    def test_an_ordinary_path_is_unchanged(self):
        plan = plan_for(info(kind=Kind.FILE, path=Path('/games/Game [01006BB00C6F0000].nsp')), keys='/k/prod.keys')
        argv = plan.steps[0].argv
        self.assertIn('/games/Game [01006BB00C6F0000].nsp', argv)
        self.assertIn('/k/prod.keys', argv)


def _function(path, name):
    """The one FunctionDef called `name` in `path`, for the structural checks below."""
    module = ast.parse(Path(path).read_text())
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f'{name} not found in {path}')


class StaleClassifications(unittest.TestCase):
    """D8: an older classification must never overwrite a newer selection.

    The rule lives in LatestSelection, which needs no display; the last two checks read the window's source
    instead of starting it, because this machine has no display and CI has none either.
    """

    APP = REPO / 'peanuts' / 'desktop' / 'ui' / 'app.py'

    def test_a_new_selection_invalidates_the_older_token(self):
        gate = LatestSelection()
        a = gate.next()
        b = gate.next()
        self.assertFalse(gate.current(a))
        self.assertTrue(gate.current(b))

    def test_the_older_answer_is_dropped_even_when_it_arrives_last(self):
        # The sequence that used to corrupt the pane: A starts, B starts, B finishes first, A finishes later.
        gate = LatestSelection()
        a, b = gate.next(), gate.next()
        shown = []
        for token, answer in ((b, 'B'), (a, 'A')):
            if gate.current(token):
                shown.append(answer)
        self.assertEqual(shown, ['B'])

    def test_an_error_from_an_old_selection_is_dropped_too(self):
        gate = LatestSelection()
        a, b = gate.next(), gate.next()
        shown = []
        for token, result in ((b, 'B'), (a, 'error: could not read A')):
            if gate.current(token):
                shown.append(result)
        self.assertEqual(shown, ['B'])

    def test_the_worker_only_posts_to_the_queue(self):
        """A worker thread must not touch a widget: its only contact with the window is the queue."""
        worker = _function(self.APP, '_classify_worker')
        touched = {node.attr for node in ast.walk(worker)
                   if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                   and node.value.id == 'self'}
        self.assertEqual(touched, {'ui_queue'})

    def test_the_result_is_applied_on_the_main_thread(self):
        """show_input() is called from _pump only, and _pump is the root.after() callback."""
        module = ast.parse(self.APP.read_text())
        callers = set()
        for func in [n for n in ast.walk(module) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(func):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == 'show_input'):
                    callers.add(func.name)
        self.assertEqual(callers, {'_pump'})
        pump = _function(self.APP, '_pump')
        self.assertTrue(any(isinstance(n, ast.Attribute) and n.attr == 'after' for n in ast.walk(pump)),
                        '_pump does not reschedule itself with root.after')


class BuilderArguments(unittest.TestCase):
    """B5, against the builder's own parser rather than a copy of its rules."""

    SCRIPT = REPO / 'peanuts' / 'scripts' / 'peanuts_build.py'

    def run_builder(self, *args):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / '-x.nsp').write_bytes(b'')   # a real game file whose name starts with a dash
            return subprocess.run([sys.executable, str(self.SCRIPT), *args], cwd=d,
                                  capture_output=True, text=True, timeout=120)

    def test_a_leading_dash_path_is_an_option_to_the_builder(self):
        r = self.run_builder('-x.nsp')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)   # argparse refuses it: the file is never the input
        self.assertIn('inputs', r.stderr)

    def test_the_form_the_plan_uses_is_the_input_to_the_builder(self):
        r = self.run_builder('./-x.nsp')
        self.assertNotEqual(r.returncode, 2, r.stdout + r.stderr)   # parsed as the input; refused later for its keys
        self.assertNotIn('unrecognized arguments', r.stderr)


class WindowStructure(unittest.TestCase):
    """B1, B2 and B6: what the window's source must do, since this machine and CI have no display.

    These read the source (and run the two functions that need no Tk). They are structural checks, not GUI
    validation: no pane was rendered, focused or scrolled here.
    """

    APP = REPO / 'peanuts' / 'desktop' / 'ui' / 'app.py'

    def source(self):
        return self.APP.read_text()

    def test_the_three_panes_are_read_only_but_focusable(self):
        text = self.source()
        self.assertEqual(text.count('read_only(tk.Text('), 3)
        for name in ('self.plan', 'self.checks', 'self.log'):
            self.assertIn(f'{name} = read_only(', text)

    def test_no_pane_is_disabled(self):
        # A disabled Text cannot take the keyboard focus: that is the defect B2 is about.
        text = self.source()
        for name in ('self.plan', 'self.checks', 'self.log'):
            self.assertNotIn(f'{name}.configure(state=', text)

    def test_each_pane_has_a_keyboard_binding(self):
        text = self.source()
        for binding in ("('p', self.plan)", "('k', self.checks)", "('l', self.log)"):
            self.assertIn(binding, text)

    def test_the_edit_filter_is_the_tested_rule_not_a_second_copy(self):
        # The rule itself is ui/keys.py (see Keyboard below); this reads the window to prove it delegates.
        module = ast.parse(self.source())
        fn = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == '_refuse_edit')
        body = ast.get_source_segment(self.source(), fn) or ''
        self.assertIn('keys.refuse_edit', body)
        self.assertFalse(any(isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'NAV_KEYS'
                             for n in module.body), 'app.py still carries its own key list')

    def test_the_log_follows_only_when_it_is_at_the_bottom(self):
        module = ast.parse(self.source())
        say = next(n for n in ast.walk(module) if isinstance(n, ast.FunctionDef) and n.name == 'say')

        def calls_to(node, name):
            return [c for c in ast.walk(node)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == name]

        self.assertTrue(calls_to(say, 'yview'), 'say() never reads the view position')
        # The name the view position is read into, and the branch that follows the log: they must be the same one.
        followed = set()
        for node in ast.walk(say):
            if isinstance(node, ast.Assign) and calls_to(node.value, 'yview'):
                followed.update(t.id for t in node.targets if isinstance(t, ast.Name))
        self.assertTrue(followed, 'the view position is read but not kept for a decision')
        ifs = [n for n in ast.walk(say) if isinstance(n, ast.If)]
        self.assertTrue(any(calls_to(n, 'see') for n in ifs), 'the log is scrolled by something other than a branch')
        self.assertTrue(any(isinstance(n.test, ast.Name) and n.test.id in followed and calls_to(n, 'see') for n in ifs),
                        'the call that follows the log is not guarded by the "already at the bottom" test')

    def test_closing_stops_the_run_and_waits_before_destroying(self):
        close = _function(self.APP, 'close')
        worker = _function(self.APP, '_shutdown_worker')
        finish = _function(self.APP, '_finish_quit')

        def calls(node, name):
            return [c for c in ast.walk(node) if isinstance(c, ast.Call)
                    and ((isinstance(c.func, ast.Attribute) and c.func.attr == name)
                         or (isinstance(c.func, ast.Name) and c.func.id == name))]

        self.assertTrue(calls(close, 'Thread'), 'close() does not hand the bounded wait to a worker thread')
        self.assertTrue(calls(worker, 'stop_and_wait'), '_shutdown_worker does not wait for the process group')
        self.assertTrue(calls(finish, 'destroy'), 'nothing destroys the window once the wait is over')
        self.assertFalse(calls(close, 'destroy') and not (calls(close, 'still_running') or calls(close, 'settled')),
                         'close() destroys the window without checking the run first')


class Keyboard(unittest.TestCase):
    """B1: Tab and Shift+Tab move the focus, and the window's shortcuts survive a focused pane.

    ui/keys.py imports no Tk, so these are real checks of the rule the window applies to every key. They are still
    not rendered-GUI verification: no pane was focused, typed into, scrolled or copied from here (this machine and
    CI have no display).
    """

    APP = REPO / 'peanuts' / 'desktop' / 'ui' / 'app.py'

    def test_tab_and_shift_tab_are_focus_moves(self):
        self.assertEqual(keys.focus_step('Tab'), 1)
        self.assertEqual(keys.focus_step('ISO_Left_Tab'), -1)   # what Tk reports for Shift+Tab on X11
        self.assertEqual(keys.focus_step('Shift-Tab'), -1)
        self.assertEqual(keys.focus_step('a'), 0)
        self.assertNotIn('Tab', keys.NAV_KEYS, 'Tab must be handled by its own binding, not passed through')

    def test_the_window_shortcuts_reach_the_window_from_a_pane(self):
        for keysym, state in (('o', keys.CONTROL), ('d', keys.CONTROL), ('p', keys.ALT),
                              ('k', keys.ALT), ('l', keys.ALT), ('F5', 0), ('Escape', 0),
                              ('Down', 0), ('Prior', 0)):
            self.assertFalse(keys.refuse_edit(keysym, state), (keysym, state))

    def test_typing_is_refused(self):
        for keysym in ('a', 'x', 'BackSpace', 'Return', 'space'):
            self.assertTrue(keys.refuse_edit(keysym), keysym)
        self.assertTrue(keys.refuse_edit('c'), 'a bare c is a letter, not a copy')
        self.assertFalse(keys.refuse_edit('c', keys.CONTROL))

    def test_a_control_letter_that_is_not_a_shortcut_is_refused(self):
        self.assertTrue(keys.refuse_edit('w', keys.CONTROL))

    def test_the_panes_bind_both_tab_keys_to_a_focus_move(self):
        text = self.APP.read_text()
        for sequence in ("'<Tab>'", "'<Shift-Tab>'", "'<ISO_Left_Tab>'"):
            self.assertIn(sequence, text)
        self.assertIn('tk_focusNext', text)
        self.assertIn('tk_focusPrev', text)
        move = _function(self.APP, '_move_focus')
        returns = [n for n in ast.walk(move) if isinstance(n, ast.Return)]
        self.assertTrue(any(isinstance(r.value, ast.Constant) and r.value.value == 'break' for r in returns),
                        '_move_focus does not stop the key after moving the focus, so Tk would also type it')


class RunLifecycle(unittest.TestCase):
    """B3: a run whose processes may still be alive is kept, and a window may not close over it."""

    APP = REPO / 'peanuts' / 'desktop' / 'ui' / 'app.py'

    class Run:
        def __init__(self, exit_event=None, settled=True):
            self.exit_event = exit_event
            self.is_settled = settled

        def settled(self):
            return self.is_settled

    def test_a_run_that_has_not_exited_is_not_finished(self):
        run = self.Run(None, settled=False)
        self.assertFalse(lifecycle.finished(run, busy=True))
        self.assertTrue(lifecycle.still_running(run))

    def test_an_exited_run_with_processes_left_is_kept(self):
        # Exited only says the tool ended: its process group may still be alive, and the run slot still taken.
        run = self.Run('exited', settled=False)
        self.assertFalse(lifecycle.finished(run, busy=True))
        self.assertTrue(lifecycle.still_running(run))

    def test_a_settled_run_is_finished(self):
        run = self.Run('exited', settled=True)
        self.assertTrue(lifecycle.finished(run, busy=False))
        self.assertFalse(lifecycle.still_running(run))

    def test_a_run_whose_slot_came_back_is_finished(self):
        self.assertTrue(lifecycle.finished(self.Run('exited', settled=False), busy=False))

    def test_no_run_is_finished(self):
        self.assertTrue(lifecycle.finished(None, busy=False))
        self.assertFalse(lifecycle.still_running(None))

    def test_the_window_does_not_drop_the_run_on_exited(self):
        handle = _function(self.APP, 'handle')
        for node in ast.walk(handle):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self.assertFalse(isinstance(target, ast.Attribute) and target.attr == 'proc',
                                     'handle() drops the run while its process group may still be alive')

    def test_the_window_keeps_the_run_until_the_supervisor_says_it_is_finished(self):
        pump = _function(self.APP, '_pump')
        calls = [n for n in ast.walk(pump) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'finished']
        self.assertTrue(calls, '_pump never asks whether the run is finished')

    def test_closing_asks_the_lifecycle_rule(self):
        close = _function(self.APP, 'close')
        calls = [n for n in ast.walk(close) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'still_running']
        self.assertTrue(calls, 'close() does not use the rule that keeps a live run open')


class Summaries(unittest.TestCase):
    def test_a_missing_profile_is_said_in_the_summary(self):
        text = summary(info(kind=Kind.NUP, title_id='0100FFFFFFFFFF', name='Unknown', has_profile=False))
        self.assertIn('no profile', text)

    def test_versions_are_listed(self):
        text = summary(info(kind=Kind.DUMP, title_id='01006BB00C6F0000', name='Game', versions=(1, 2),
                            has_profile=True))
        self.assertIn('v1', text)
        self.assertIn('v2', text)


class Layers(unittest.TestCase):
    """The architecture rule: engine/ and model/ must be importable with no Tk on the machine at all."""

    def test_engine_and_model_import_without_tkinter(self):
        code = (
            'import sys\n'
            "sys.modules['tkinter'] = None\n"      # any "import tkinter" from now on raises ImportError
            f'sys.path.insert(0, {str(REPO)!r})\n'
            'import peanuts.desktop.engine, peanuts.desktop.model, peanuts.desktop.platform\n'
            'import peanuts.desktop.ui.keys, peanuts.desktop.ui.lifecycle\n'
        )
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_window_compiles(self):
        """A syntax check only: this machine has no display (and CI has none either)."""
        with tempfile.TemporaryDirectory() as tmp:
            py_compile.compile(str(REPO / 'peanuts' / 'desktop' / 'ui' / 'app.py'),
                               cfile=str(Path(tmp) / 'app.pyc'), doraise=True)

    def test_opening_a_folder_that_is_not_there_is_false(self):
        self.assertFalse(open_folder('/nonexistent/peanuts-desktop-test'))


if __name__ == '__main__':
    unittest.main()
