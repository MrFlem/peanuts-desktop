"""What the user picked, what this computer can do with it, and what would run.

No tkinter here either: the window asks these questions, it does not answer them. That keeps the decisions
(classify an input, decide whether keys are needed, decide what would run) testable without a display.
"""
from .inputs import Kind, InputInfo, LatestSelection, classify, summary
from .plan import RunPlan, Step, plan_for
from .preflight import Check, adb_check, blockers, local_checks, python_check

__all__ = ['Kind', 'InputInfo', 'LatestSelection', 'classify', 'summary', 'RunPlan', 'Step', 'plan_for',
           'Check', 'adb_check', 'blockers', 'local_checks', 'python_check']
