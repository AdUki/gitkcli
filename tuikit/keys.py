"""Keyboard input: key constants, `KeyEvent`, and the `KeyReader` that decodes
raw curses input (including the escape sequences curses does not surface as a
single key, and the terminal's mouse reports) into events.

Handlers receive a `KeyEvent` in `handle_key(event)` and return True when they
consumed it. A synthetic event is just `KeyEvent(curses.KEY_DOWN)`.
"""

from __future__ import annotations

import curses
import dataclasses
import typing

from tuikit.mouse import MouseReport

# Synthetic key codes (negative, so they never collide with real curses codes)
# for sequences curses does not report as a single key.
KEY_SHIFT_F5 = -100
KEY_CTRL_LEFT = -101
KEY_CTRL_RIGHT = -102
KEY_CTRL_BACKSPACE = -103
KEY_CTRL_DEL = -104
KEY_SHIFT_LEFT = -105
KEY_SHIFT_RIGHT = -106

KEY_TAB = 9
KEY_ENTER = 10
KEY_RETURN = 13
KEY_ESC = curses.KEY_EXIT  # a lone Esc is reported as KEY_EXIT
ENTER_KEYS = (curses.KEY_ENTER, KEY_ENTER, KEY_RETURN)
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127)


def ctrl(char: str) -> int:
    """Key code of Ctrl+<char>, e.g. ctrl("w") == 23."""
    return ord(char) & 0x1F


def is_printable(key: int) -> bool:
    return 32 <= key <= 126


# Longest SGR mouse report body we wait for ("Cb;Cx;Cy" + final byte).
_SGR_MAX = 24

# Escape sequences (after the ESC byte run is collected) that map to one key.
_SEQUENCES = {
    (27, 91, 49, 53, 59, 50, 126): KEY_SHIFT_F5,
    (27, 91, 49, 59, 53, 68): KEY_CTRL_LEFT,
    (27, 91, 49, 59, 53, 67): KEY_CTRL_RIGHT,
    (27, 91, 49, 59, 50, 68): KEY_SHIFT_LEFT,
    (27, 91, 49, 59, 50, 67): KEY_SHIFT_RIGHT,
    (27, 91, 51, 59, 53, 126): KEY_CTRL_DEL,
    # Shift+Tab: ncurses only reports KEY_BTAB when terminfo has kcbt, so decode
    # the raw sequence too.
    (27, 91, 90): curses.KEY_BTAB,
}


@dataclasses.dataclass(frozen=True)
class KeyEvent:
    """One key press. `key` is the normalized key code; `sequence` the raw
    bytes it was decoded from (empty for synthetic events). A mouse report is
    a KEY_MOUSE event carrying the decoded `mouse` report."""

    key: int
    sequence: typing.Tuple[int, ...] = ()
    mouse: typing.Optional[MouseReport] = None

    @property
    def is_mouse(self) -> bool:
        return self.key == curses.KEY_MOUSE

    @property
    def is_enter(self) -> bool:
        return self.key in ENTER_KEYS


class KeyReader:
    """Reads one key from a curses window and normalizes it to a KeyEvent."""

    def __init__(self, log=None):
        self.log = log  # anything with .debug(str); optional
        self._kmous = None  # terminfo's mouse prefix, looked up on first use

    def _debug(self, txt):
        if self.log is not None:
            self.log.debug(txt)

    def read(self, stdscr) -> typing.Optional[KeyEvent]:
        """The next key, or None when nothing usable was read (a timeout or an
        unrecognized escape sequence)."""
        key = stdscr.getch()
        if key < 0:
            return None

        if key == curses.KEY_MOUSE:
            # curses matched terminfo's kmous prefix (ESC [ M or ESC [ <) and
            # left the rest of the report in the input.
            return self._read_mouse(stdscr, sgr=self._kmous_is_sgr())

        if key == 27:  # Esc: either a lone Esc or the start of a sequence
            sequence = [key]
            while True:
                key = stdscr.getch()
                if key < 0:
                    break
                if key == 27:
                    sequence.clear()
                sequence.append(key)
                # A mouse report curses did not take as kmous.
                if sequence == [27, 91, 60]:  # ESC [ <
                    return self._read_mouse(stdscr, sgr=True)
                if sequence == [27, 91, 77]:  # ESC [ M
                    return self._read_mouse(stdscr, sgr=False)
            self._debug("Escape sequence: " + str(sequence))
            if len(sequence) == 1:
                key = curses.KEY_EXIT
            else:
                key = _SEQUENCES.get(tuple(sequence))
                if key is None:
                    return None
        else:
            self._debug("Key: " + str(key))
            sequence = [key]

        # Ctrl+Backspace arrives as ^H (8) on most terminals; plain Backspace
        # arrives as KEY_BACKSPACE / 127.
        if key == 8:
            key = KEY_CTRL_BACKSPACE

        return KeyEvent(key, tuple(sequence))

    def _kmous_is_sgr(self) -> bool:
        if self._kmous is None:
            try:
                self._kmous = curses.tigetstr("kmous") or b""
            except curses.error:
                self._kmous = b""
        return self._kmous.endswith(b"<")

    def _read_mouse(self, stdscr, sgr: bool) -> typing.Optional[KeyEvent]:
        """Read the body of a mouse report whose prefix was just consumed."""
        body = []
        while True:
            key = stdscr.getch()
            if key < 0 or key > 255:
                self._debug(f"Truncated mouse report: {body}")
                return None
            body.append(key)
            if sgr:
                if key in (77, 109):  # M / m ends it
                    break
                if len(body) >= _SGR_MAX:
                    self._debug(f"Bad mouse report: {body}")
                    return None
            elif len(body) == 3:
                break
        report = (
            MouseReport.from_sgr(bytes(body)) if sgr else MouseReport.from_x10(*body)
        )
        if report is None:
            self._debug(f"Bad mouse report: {body}")
            return None
        return KeyEvent(curses.KEY_MOUSE, tuple(body), report)
