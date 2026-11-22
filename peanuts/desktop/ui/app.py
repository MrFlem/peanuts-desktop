"""The Peanuts Desktop window (Phase A).

What it does now: pick an input, say what it is in the packer's own terms, show the exact command that would run,
show what this computer is missing, and run the project's environment check as a supervised child process with a
live log and a working Stop.

What it deliberately does not do yet: build. The plan is displayed, not executed (Phase C), the import step and
keys are Phase B, and the transfer is Phase D. Buttons for those are absent rather than disabled, so the window
never implies a capability it does not have.

Keyboard: the Plan, Preflight and Log panes are read-only rather than `disabled`, so Tab reaches them and their text
can be scrolled, selected and copied; Alt+P, Alt+K and Alt+L put the focus on each one. The log follows new output
only while it is scrolled to the bottom, so reading earlier lines is not interrupted by the next line.

Closing: the window does not exit over a running check. It asks, then waits - bounded, on a worker thread so the
window stays responsive - for the check's whole process group to be gone before it destroys itself; a group that
cannot be confirmed gone is sent SIGKILL and reported on stderr.

Threading rules, which matter more than the layout:
  * Tk is touched only on the main thread. Work happens in worker threads and arrives through `self.ui_queue`,
    drained by `_pump` (every 100 ms) together with the supervisor's event queue.
  * A run's events are drained from `SupervisedProcess.drain()` on the main thread, never from a callback.
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import __version__, repo_root
from ..engine import (AlreadyRunning, Exited, Line, Progress, SpawnError, Stage, SupervisedProcess, python_argv)
from ..engine.events import ECHO
from ..model import LatestSelection, classify, local_checks, plan_for, summary

TITLE = 'Peanuts Desktop'
HINT = ('Ctrl+O game files   Ctrl+D dump folder   F5 check the packer environment   Esc stop\n'
        'Alt+P plan   Alt+K preflight   Alt+L log   Tab between panes; arrows, Page Up/Down and Ctrl+C in a pane')
QUIT_NOTE = 'Stopping… the window closes once the check and the processes it started are gone.'
QUIT_TIMEOUT = 12.0   # stop_and_wait()'s own bound is shorter; this is the window's backstop

# The keys a read-only pane may still act on: moving around, selecting and copying. Everything else is refused, so
# the pane can hold the keyboard focus (a `disabled` Text cannot: Tk sends its keys nowhere) without taking an edit.
NAV_KEYS = frozenset({'Left', 'Right', 'Up', 'Down', 'Home', 'End', 'Prior', 'Next', 'Tab', 'ISO_Left_Tab',
                      'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Super_L', 'Super_R',
                      'Caps_Lock', 'Escape', 'Insert'})


def _refuse_edit(event):
    """'break' for every key that would change the pane, so it can be focused without being editable."""
    if event.keysym in NAV_KEYS:
        return None
    if event.state & 0x4 and event.keysym.lower() in ('a', 'c', 'insert'):   # Control-a selects, Control-c copies
        return None
    return 'break'


def read_only(text):
    """A Text pane that takes the keyboard focus, scrolls and copies, and refuses every edit."""
    text.configure(takefocus=True, highlightthickness=2, highlightbackground='#c8c8c8', highlightcolor='#2c7fb8')
    text.bind('<Key>', _refuse_edit)
    for sequence in ('<<Cut>>', '<<Paste>>', '<<Clear>>', '<Button-2>'):
        text.bind(sequence, lambda _e: 'break')
    return text


class App:
    def __init__(self, root, initial=None):
        self.root = root
        self.proc = None
        self.ui_queue = queue.Queue()
        self.selection = LatestSelection()   # which classification request is the current one
        self.current = None
        self._quitting = None                # the run the window is waiting for before it closes
        root.title(f'{TITLE} {__version__} (Phase A)')
        root.minsize(760, 620)
        self._build()
        self._bind()
        self.show_checks(local_checks())
        root.after(100, self._pump)
        if initial:
            self.choose(initial)

    # --- construction ---------------------------------------------------------------------------
    def _build(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)   # the log takes the space the window has

        pick = ttk.Frame(root, padding=(10, 10, 10, 0))
        pick.grid(row=0, column=0, sticky='ew')
        pick.columnconfigure(1, weight=1)
        ttk.Label(pick, text='Game').grid(row=0, column=0, sticky='w')
        self.path = tk.StringVar()
        ttk.Entry(pick, textvariable=self.path, state='readonly').grid(row=0, column=1, sticky='ew', padx=6)
        buttons = ttk.Frame(pick)
        buttons.grid(row=0, column=2, sticky='e')
        ttk.Button(buttons, text='Game files…', command=self.pick_file).pack(side='left')
        ttk.Button(buttons, text='Dump folder…', command=self.pick_dir).pack(side='left', padx=(4, 0))

        about_box = ttk.LabelFrame(root, text='Input', padding=8)
        about_box.grid(row=1, column=0, sticky='ew', padx=10, pady=(8, 0))
        about_box.columnconfigure(0, weight=1)
        self.about = ttk.Label(
            about_box, justify='left', wraplength=720,
            text='Pick a .nup package, a dump folder (base/exefs, updates/, dlc/), or .nsp/.xci/.nsz/.xcz files.')
        self.about.grid(row=0, column=0, sticky='w')

        plan_box = ttk.LabelFrame(root, text='Plan', padding=8)
        plan_box.grid(row=2, column=0, sticky='ew', padx=10, pady=(8, 0))
        plan_box.columnconfigure(0, weight=1)
        self.plan = read_only(tk.Text(plan_box, height=6, wrap='word', font='TkFixedFont'))
        self.plan.grid(row=0, column=0, sticky='ew')

        pc = ttk.LabelFrame(root, text='Preflight', padding=8)
        pc.grid(row=3, column=0, sticky='ew', padx=10, pady=8)
        pc.columnconfigure(0, weight=1)
        self.checks = read_only(tk.Text(pc, height=4, wrap='word', font='TkFixedFont'))
        self.checks.grid(row=0, column=0, sticky='ew')
        bar = ttk.Frame(pc)
        bar.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        bar.columnconfigure(1, weight=1)
        self.check_btn = ttk.Button(bar, text='Check the packer environment', command=self.check_environment)
        self.check_btn.grid(row=0, column=0)
        self.progress = ttk.Progressbar(bar, mode='determinate', maximum=1000)
        self.progress.grid(row=0, column=1, sticky='ew', padx=8)
        self.stop_btn = ttk.Button(bar, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.grid(row=0, column=2)
        self.stage = ttk.Label(pc, text='', foreground='gray')
        self.stage.grid(row=2, column=0, sticky='w', pady=(4, 0))

        log_box = ttk.LabelFrame(root, text='Log', padding=8)
        log_box.grid(row=4, column=0, sticky='nsew', padx=10)
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log = read_only(tk.Text(log_box, height=14, wrap='word', font='TkFixedFont'))
        scroll = ttk.Scrollbar(log_box, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        self.log.tag_configure('red', foreground='#c0392b')
        self.log.tag_configure('echo', foreground='#555555')

        ttk.Label(root, text=HINT, foreground='gray', justify='left').grid(row=5, column=0, sticky='w',
                                                                          padx=10, pady=(0, 8))

    def _bind(self):
        """Keyboard access without a modifier where possible: F5 and Esc, plus the two pickers on Ctrl."""
        self.root.bind('<Control-o>', lambda _e: self.pick_file())
        self.root.bind('<Control-d>', lambda _e: self.pick_dir())
        self.root.bind('<F5>', lambda _e: self.check_environment())
        self.root.bind('<Escape>', lambda _e: self.stop())
        for key, pane in (('p', self.plan), ('k', self.checks), ('l', self.log)):
            self.root.bind(f'<Alt-{key}>', lambda _e, w=pane: self.focus_pane(w))
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def focus_pane(self, pane):
        """Put the keyboard focus on one of the three panes, where its text can be read, scrolled and copied."""
        pane.focus_set()

    # --- choosing an input ----------------------------------------------------------------------
    def pick_file(self):
        chosen = filedialog.askopenfilename(
            title='A game file or a .nup package',
            filetypes=[('Game files and packages', '*'), ('All files', '*')])
        if chosen:
            self.choose(chosen)

    def pick_dir(self):
        chosen = filedialog.askdirectory(title='A dump folder (base/, updates/, dlc/)', mustexist=True)
        if chosen:
            self.choose(chosen)

    def choose(self, path):
        token = self.selection.next()   # a new choice invalidates whatever an older worker is still reading
        self.path.set(str(path))
        self.set_about('reading…')
        self.set_plan([])
        threading.Thread(target=self._classify_worker, args=(str(path), token), daemon=True).start()

    def _classify_worker(self, path, token):
        # A worker thread: it reads the input and posts the answer. It never touches a widget — Tk may only be
        # used on the main thread — and it tags the answer with its token so a stale one can be recognised.
        try:
            info = classify(path)
        except Exception as e:   # a reader that fails on an odd file must not take the window down
            self.ui_queue.put(('error', token, f'{type(e).__name__}: {e}'))
        else:
            self.ui_queue.put(('info', token, info))

    # --- the environment check ------------------------------------------------------------------
    def check_environment(self):
        if self.proc is not None or self._quitting is not None:
            return
        script = repo_root() / 'scripts' / 'bananative_env.py'
        if not script.is_file():
            messagebox.showerror(TITLE, f'{script} is missing: is this the repository root?')
            return
        try:
            self.proc = SupervisedProcess(python_argv(script)).start()
        except (AlreadyRunning, SpawnError) as e:
            self.proc = None
            messagebox.showerror(TITLE, str(e))
            return
        self.progress.configure(mode='indeterminate')
        self.progress.start(15)
        self.stage.configure(text='Checking the packer environment…')
        self.check_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')

    def stop(self):
        if self.proc is not None and self.proc.running:
            self.proc.stop()
            self.stage.configure(text='Stopping…')

    # --- events ---------------------------------------------------------------------------------
    def handle(self, event):
        if isinstance(event, Line):
            self.say(event.text, event.severity)
        elif isinstance(event, Progress):
            self.progress.stop()
            self.progress.configure(mode='determinate')
            self.progress['value'] = 1000 * event.fraction
            self.stage.configure(text=f'{event.label}: {event.done:,} / {event.total:,}')
        elif isinstance(event, Stage):
            self.stage.configure(text=event.text)
        elif isinstance(event, Exited):
            self.progress.stop()
            self.progress.configure(mode='determinate')
            self.progress['value'] = 1000 if event.ok else 0
            detail = f'Environment check {event.word()} in {event.seconds:.1f} s'
            if event.cleanup:
                detail += f' — {event.cleanup}'
            if not event.output_complete:
                detail += ' — the last log lines may be missing'
            self.stage.configure(text=detail)
            # The button follows the supervisor, not this event: when the process group could not be confirmed
            # gone the slot is still held, and starting another run would put two builds in one pack folder.
            self.check_btn.configure(state='disabled' if SupervisedProcess.busy() else 'normal')
            self.stop_btn.configure(state='disabled')
            self.proc = None

    def _pump(self):
        try:
            while True:
                kind, token, payload = self.ui_queue.get_nowait()
                if kind == 'quit':
                    self._finish_quit(payload)
                    return       # the window is gone: do not reschedule the pump
                if not self.selection.current(token):
                    continue   # an answer about a selection the user has already replaced: drop it
                if kind == 'info':
                    self.show_input(payload)
                elif kind == 'error':
                    self.set_about(str(payload))
        except queue.Empty:
            pass
        if self.proc is not None:
            for event in self.proc.drain():
                self.handle(event)
        self.root.after(100, self._pump)

    # --- what the panes say ---------------------------------------------------------------------
    def show_input(self, info):
        self.current = info
        self.set_about(summary(info))
        plan = plan_for(info)
        lines = []
        for blocker in plan.blockers:
            lines.append(f'[RED] {blocker}')
        for step in plan.steps:
            lines.append(f'{step.title}')
            lines.append(f'  $ {step.command()}')
            lines.append(f'  {step.note}')
        lines.extend(plan.notes)
        self.set_plan(lines)

    def show_checks(self, checks):
        self.checks.delete('1.0', 'end')
        for check in checks:
            mark = 'ok   ' if check.ok else '[RED] '
            self.checks.insert('end', f'{mark}{check.what}: {check.detail}\n')

    def set_about(self, text):
        self.about.configure(text=text)

    def set_plan(self, lines):
        self.plan.delete('1.0', 'end')
        self.plan.insert('1.0', '\n'.join(lines))

    def say(self, text, severity='info'):
        tag = 'red' if severity == 'red' else ('echo' if severity == ECHO else None)
        at_bottom = self.log.yview()[1] >= 0.999   # reading earlier output: do not pull the view away from it
        self.log.insert('end', text + '\n', tag or ())
        if at_bottom:
            self.log.see('end')

    # --- closing --------------------------------------------------------------------------------
    def close(self):
        if self._quitting is not None:
            return      # already stopping: a repeated WM_DELETE must not start a second shutdown
        run = self.proc
        if run is not None and not run.settled():
            if not messagebox.askyesno(TITLE, 'A check is still running. Stop it and quit?'):
                return
            self._quitting = run
            self.check_btn.configure(state='disabled')
            self.stop_btn.configure(state='disabled')
            self.stage.configure(text=QUIT_NOTE)
            # The wait is bounded and belongs on a worker thread: the window stays responsive while the run's
            # process group is ended, and _pump closes the window when the answer arrives.
            threading.Thread(target=self._shutdown_worker, name='peanuts-quit', args=(run,), daemon=True).start()
            return
        self.root.destroy()

    def _shutdown_worker(self, run):
        """End the run and wait, bounded, for its whole process group; then let the main thread close the window."""
        settled = run.stop_and_wait(grace=5.0, timeout=QUIT_TIMEOUT)
        self.ui_queue.put(('quit', None, settled))

    def _finish_quit(self, settled):
        if not settled:
            sys.stderr.write('[RED] the window closed while the check\'s processes were still alive: they were sent '
                             'SIGKILL and could not be confirmed gone\n')
        self.root.destroy()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = tk.Tk()
    App(root, argv[0] if argv else None)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
