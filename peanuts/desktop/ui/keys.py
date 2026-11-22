"""What the keyboard may do in the window's read-only panes, kept out of the window so the tests can run it.

app.py needs tkinter and a display; these functions do not. That matters because the rules here are the ones a
user feels: Tab and Shift+Tab move the keyboard focus instead of typing a tab into a pane, a letter is refused,
and the window's own shortcuts (Ctrl+O, Ctrl+D, Alt+P/K/L, F5, Esc) still reach the window while a pane has the
focus.

Ctrl+O and Ctrl+D are not simply let through. Tk's Text class binds both to an edit (open a line, delete a
character), and a class binding runs after the pane's own, so a pane that passed them on was edited before the
window's shortcut ran. `shortcut()` names the window action for them, and the pane's own binding runs it and stops
the key (app.py: read_only / _run_shortcut); `refuse_edit()` reports them as "not an edit" for the same reason.

The modifier bits are Tk's: 0x4 is Control, 0x8 is Mod1 (Alt on X11 and Windows).
"""
from __future__ import annotations

CONTROL = 0x4
ALT = 0x8

# Keys a read-only pane may act on by itself: moving around and selecting. Everything else is refused, so the pane
# can hold the keyboard focus (a `disabled` Text cannot: Tk sends its keys nowhere) without taking an edit.
NAV_KEYS = frozenset({'Left', 'Right', 'Up', 'Down', 'Home', 'End', 'Prior', 'Next',
                      'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Super_L', 'Super_R',
                      'Caps_Lock', 'Insert'})

# The window's own shortcuts, which a focused pane must let through to the toplevel bindings in app.py.
WINDOW_KEYS = frozenset({'F5', 'Escape'})
CONTROL_SHORTCUTS = frozenset({'a', 'c', 'insert'})   # select the pane, copy, copy: Tk's own, read-only-safe

# The window's shortcuts that Tk's Text class would otherwise turn into an edit: the pane runs the action itself
# and answers 'break'. The value is the action's name, which app.py maps to its pickers.
CONTROL_ACTIONS = {'o': 'file', 'd': 'dir'}
ALT_SHORTCUTS = frozenset({'p', 'k', 'l'})            # the three panes

# Tk reports Shift+Tab as ISO_Left_Tab on X11; the other two spellings cover the platforms that do not.
FOCUS_KEYS = {'Tab': 1, 'Shift-Tab': -1, 'ISO_Left_Tab': -1}


def shortcut(keysym, state=0):
    """The window action a key asks for ('file' for Ctrl+O, 'dir' for Ctrl+D), or None.

    A pane runs it itself and stops the key: passing it through would let Tk's Text class binding edit the pane
    first, because class bindings run after the widget's own.
    """
    if not state & CONTROL:
        return None
    return CONTROL_ACTIONS.get(keysym.lower())


def refuse_edit(keysym, state=0):
    """True when a key pressed in a read-only pane must not edit it (its binding returns 'break')."""
    if keysym in NAV_KEYS or keysym in WINDOW_KEYS:
        return False
    if shortcut(keysym, state) is not None:
        return True      # not an edit: the pane's own binding runs the action and stops the key
    if state & CONTROL and keysym.lower() in CONTROL_SHORTCUTS:
        return False
    if state & ALT and keysym.lower() in ALT_SHORTCUTS:
        return False
    return True


def focus_step(keysym):
    """+1 for Tab, -1 for Shift+Tab, 0 for any other key: which way the focus moves, or that it does not."""
    return FOCUS_KEYS.get(keysym, 0)
