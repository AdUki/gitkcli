"""Configurable keyboard shortcuts.

Every rebindable action is listed once in ACTIONS: an id, the scope it works
in, the label the shortcut editor shows, and its default key. A key is written
as a *spec* - "c", "D", "Del", "Shift+F5", "Ctrl+W" - which is what the config
file stores (section "keys", only the actions changed from their default) and
what the context menus show right-aligned next to the action.

Scopes say where a key is live. "log", "diff" and "refs" are the three git
views (their keys act on the selected row); "copy" is every list with the
clipboard trio (git log, git diff and the Logs view); "global" works
everywhere. Two actions may share a key only when their scopes never meet.

Keys the lists themselves use for navigation and search (RESERVED) cannot be
bound: a binding is checked before the list's own handling, so it would
shadow them.

A leaf module: depends only on curses and tuikit's key constants.
"""

from __future__ import annotations

import curses
import dataclasses
import typing

from tuikit import keys as K


@dataclasses.dataclass(frozen=True)
class Action:
    id: str
    scope: str
    label: str
    default: str  # key spec; "" = unbound


SCOPES = [
    ("global", "Everywhere"),
    ("log", "Git log"),
    ("diff", "Git diff"),
    ("refs", "Git references"),
    ("copy", "Git log, Git diff and Logs"),
]

# Scopes whose keys are live at the same time (besides a scope with itself).
_OVERLAPS = {
    frozenset(("global", "log")),
    frozenset(("global", "diff")),
    frozenset(("global", "refs")),
    frozenset(("global", "copy")),
    frozenset(("copy", "log")),
    frozenset(("copy", "diff")),
}

ACTIONS = [
    Action("global.split", "global", "Cycle split view", "|"),
    Action("global.switch_pane", "global", "Switch split pane", "Ctrl+W"),
    Action("global.jump_back", "global", "Jump back", "Ctrl+O"),
    Action("global.jump_forward", "global", "Jump forward", "Ctrl+I"),
    Action("global.reload", "global", "Reload all commits", "Shift+F5"),
    Action("log.new_branch", "log", "Create new branch", "b"),
    Action("log.new_tag", "log", "Create new tag", "t"),
    Action("log.cherry_pick", "log", "Cherry-pick this commit", "c"),
    Action("log.revert", "log", "Revert this commit", "v"),
    Action("log.reset", "log", "Reset branch here", "r"),
    Action("log.diff_to_marked", "log", "Diff this --> marked commit", "d"),
    Action("log.diff_from_marked", "log", "Diff marked commit --> this", "D"),
    Action("log.mark", "log", "Mark this commit", "m"),
    Action("log.return_to_mark", "log", "Return to mark", "M"),
    Action("copy.line", "copy", "Copy line to clipboard", "y"),
    Action("copy.all", "copy", "Copy all to clipboard", "Y"),
    Action("diff.more_context", "diff", "More context lines", "+"),
    Action("diff.less_context", "diff", "Fewer context lines", "-"),
    Action("diff.ignore_whitespace", "diff", "Toggle ignore whitespace", "w"),
    Action("refs.checkout", "refs", "Check out this branch", "o"),
    Action("refs.rename", "refs", "Rename this branch", "R"),
    Action("refs.copy_name", "refs", "Copy ref name", "y"),
    Action("refs.push", "refs", "Push ref to remote", "p"),
    Action("refs.tag_annotation", "refs", "Show tag annotation", "a"),
    Action("refs.remove", "refs", "Remove this ref", "Del"),
]

ACTIONS_BY_ID = {action.id: action for action in ACTIONS}

# Named keys, both ways. Printable characters are their own spec; Ctrl+<letter>
# and F<n> are built in parse/format.
_NAMED = {
    "Space": ord(" "),
    "Tab": K.KEY_TAB,
    "Enter": K.KEY_ENTER,
    "Esc": K.KEY_ESC,
    "Backspace": curses.KEY_BACKSPACE,
    "Del": curses.KEY_DC,
    "Ins": curses.KEY_IC,
    "Home": curses.KEY_HOME,
    "End": curses.KEY_END,
    "PgUp": curses.KEY_PPAGE,
    "PgDn": curses.KEY_NPAGE,
    "Up": curses.KEY_UP,
    "Down": curses.KEY_DOWN,
    "Left": curses.KEY_LEFT,
    "Right": curses.KEY_RIGHT,
    "Shift+Tab": curses.KEY_BTAB,
    "Shift+F5": K.KEY_SHIFT_F5,
    "Shift+Left": K.KEY_SHIFT_LEFT,
    "Shift+Right": K.KEY_SHIFT_RIGHT,
    "Ctrl+Left": K.KEY_CTRL_LEFT,
    "Ctrl+Right": K.KEY_CTRL_RIGHT,
    "Ctrl+Del": K.KEY_CTRL_DEL,
    "Ctrl+Backspace": K.KEY_CTRL_BACKSPACE,
}
_NAMES = {code: name for name, code in _NAMED.items()}
# Ctrl+I *is* Tab on a terminal; it is the jump-forward key, so name it so.
_NAMES[K.ctrl("i")] = "Ctrl+I"
_NAMES[K.KEY_RETURN] = "Enter"
_NAMES[127] = "Backspace"

# Keys a list (or the app) already gives a fixed meaning: navigation, search,
# open/close, the function-key bar and the line-editor keys of the dialogs.
RESERVED = frozenset(
    [ord(c) for c in "jkhlHLgG/nNq"]
    + [K.KEY_ENTER, K.KEY_RETURN, K.KEY_ESC, curses.KEY_ENTER]
    + [curses.KEY_BACKSPACE, 127]
    + [curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT]
    + [curses.KEY_HOME, curses.KEY_END, curses.KEY_PPAGE, curses.KEY_NPAGE]
    + [K.KEY_SHIFT_LEFT, K.KEY_SHIFT_RIGHT, K.KEY_CTRL_LEFT, K.KEY_CTRL_RIGHT]
    + [K.ctrl("b"), K.ctrl("f"), K.ctrl("n"), K.ctrl("p")]
    + [curses.KEY_F0 + n for n in range(1, 11)]
)


def parse(spec: str) -> typing.Optional[int]:
    """Key code of a spec ("c", "Del", "F5", "Ctrl+W"); None when it names no
    key. The empty spec is not a key either (an unbound action)."""
    if not isinstance(spec, str) or not spec:
        return None
    if len(spec) == 1:
        return ord(spec) if K.is_printable(ord(spec)) else None
    for name, code in _NAMED.items():
        if spec.lower() == name.lower():
            return code
    head, _, tail = spec.partition("+")
    if head.lower() == "ctrl" and len(tail) == 1 and tail.isalpha():
        return K.ctrl(tail.lower())
    if spec[0] in "fF" and spec[1:].isdigit() and 1 <= int(spec[1:]) <= 12:
        return curses.KEY_F0 + int(spec[1:])
    return None


def format_key(key: int) -> typing.Optional[str]:
    """The spec of a key code; None for a key with no name (unbindable)."""
    if key in _NAMES:
        return _NAMES[key]
    if K.is_printable(key):
        return chr(key)
    if 1 <= key <= 26:
        return "Ctrl+" + chr(key + 96).upper()
    if curses.KEY_F0 + 1 <= key <= curses.KEY_F0 + 12:
        return f"F{key - curses.KEY_F0}"
    return None


def scopes_meet(a: str, b: str) -> bool:
    return a == b or frozenset((a, b)) in _OVERLAPS


class Keymap:
    """The active bindings: the defaults with the config's overrides on top.
    The views ask it which action a key runs (`action_for`); the menus and the
    shortcut editor ask it for an action's key (`key`, `spec`)."""

    def __init__(self, overrides: typing.Optional[dict] = None):
        self._specs = {}
        self.reset()
        # Ignore what the config cannot mean: unknown actions, unparsable or
        # reserved keys. "" is a deliberately unbound action.
        for action_id, spec in (overrides or {}).items():
            if action_id not in ACTIONS_BY_ID:
                continue
            key = parse(spec)
            if spec == "" or (key is not None and key not in RESERVED):
                self._specs[action_id] = "" if key is None else format_key(key)

    def reset(self):
        """Back to the default bindings."""
        self._specs = {action.id: action.default for action in ACTIONS}

    def spec(self, action_id: str) -> str:
        """The action's key as shown ("c", "Shift+F5"); "" when unbound."""
        return self._specs.get(action_id, "")

    def key(self, action_id: str) -> typing.Optional[int]:
        return parse(self.spec(action_id))

    def action_for(self, scope: str, key: int) -> typing.Optional[str]:
        """The action of `scope` that `key` runs, if any."""
        for action in ACTIONS:
            if action.scope == scope and self.key(action.id) == key:
                return action.id
        return None

    def conflict(self, action_id: str, key: int) -> typing.Optional[str]:
        """Another action already on `key` where `action_id` is live."""
        scope = ACTIONS_BY_ID[action_id].scope
        for action in ACTIONS:
            if (
                action.id != action_id
                and scopes_meet(scope, action.scope)
                and self.key(action.id) == key
            ):
                return action.id
        return None

    def bind(self, action_id: str, key: typing.Optional[int]):
        """Put `action_id` on `key` (None unbinds it). The caller checks
        RESERVED and conflicts first."""
        self._specs[action_id] = "" if key is None else format_key(key)

    def overrides(self) -> dict:
        """The bindings that differ from the defaults (what the config keeps)."""
        return {
            action.id: self._specs[action.id]
            for action in ACTIONS
            if self._specs[action.id] != action.default
        }


def handle_copy_key(view, key: int) -> bool:
    """Run a "copy" action of `view` (a list with the clipboard trio) for
    `key`; False when the key is not one."""
    action = view.app.keymap.action_for("copy", key)
    if action == "copy.line":
        item = view.get_selected()
        if item is not None:
            item.copy_text_to_clipboard()
    elif action == "copy.all":
        view.copy_text_to_clipboard()
    else:
        return False
    return True
