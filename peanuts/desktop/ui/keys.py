"""What the keyboard may do in the window's read-only panes, kept out of the window so the tests can run it.

app.py needs tkinter and a display; these two functions do not. That matters because the rules here are the ones a
user feels: Tab and Shift+Tab move the keyboard focus instead of typing a tab into a pane, a letter is refused,
and the window's own shortcuts (Ctrl+O, Ctrl+D, Alt+P/K/L, F5, Esc) still reach the window while a pane has the
focus. A pane binding that answered 'break' to everything used to swallow them.

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
CONTROL_SHORTCUTS = frozenset({'a', 'c', 'insert', 'o', 'd'})   # select, copy, the two pickers
ALT_SHORTCUTS = frozenset({'p', 'k', 'l'})                       # the three panes

# Tk reports Shift+Tab as ISO_Left_Tab on X11; the other two spellings cover the platforms that do not.
FOCUS_KEYS = {'Tab': 1, 'Shift-Tab': -1, 'ISO_Left_Tab': -1}


def refuse_edit(keysym, state=0):
    """True when a key pressed in a read-only pane must do nothing (its binding returns 'break')."""
    if keysym in NAV_KEYS or keysym in WINDOW_KEYS:
        return False
    if state & CONTROL and keysym.lower() in CONTROL_SHORTCUTS:
        return False
    if state & ALT and keysym.lower() in ALT_SHORTCUTS:
        return False
    return True


def focus_step(keysym):
    """+1 for Tab, -1 for Shift+Tab, 0 for any other key: which way the focus moves, or that it does not."""
    return FOCUS_KEYS.get(keysym, 0)
