"""Mouse input: event types, the immutable `MouseEvent` handed to handlers, and
the stateful `MouseTracker` that decodes the terminal's mouse reports.

Reports
-------
tuikit speaks the xterm mouse protocol itself instead of going through
curses.getmouse(): ncurses (ABI 6) only knows buttons 1-5 and folds buttons 6/7
- the horizontal wheel - into a bare REPORT_MOUSE_POSITION, losing the
direction. `MouseTracker.enable()` turns on click reporting in SGR encoding
(`ESC [ < Cb ; Cx ; Cy M|m`); `KeyReader` cuts each report out of the input
(also the classic X10 `ESC [ M Cb Cx Cy`, which terminals without SGR - and the
golden tests - send) into a `MouseReport`.

Event flow
----------
`MouseTracker.decode()` turns one report into zero or more `MouseEvent`s
(a press of one button while the other is still recorded as held first yields
a CANCEL of the abandoned gesture). `Screen.dispatch_mouse()` routes each event to the view
under the cursor (or to whatever grabbed the drag), and every level of the
routing hands its children a *retargeted copy* (`event.at(x, y)`) whose
`x`/`y` are in the child's own coordinates. Handlers never mutate an event.

Grabs and capture
-----------------
A press that a view or item handled *grabs* the drag: the moves and the release
that follow go to it even when the cursor has left it, in the same coordinate
system it was pressed in. While an item holds the grab, moves and the release
are reported relative to its row: LEFT_MOVE_IN while still on the row,
LEFT_MOVE_OUT / LEFT_RELEASE_OUT once off it (so a button can un-press when
dragged off and not fire on release).

Terminals only stream bare motion events while asked to; `capture(owner)` turns
that on (e.g. a menu tracking the hovered row), `release(owner)` turns it off
once no owner needs it.
"""

from __future__ import annotations

import dataclasses
import time
import typing

LEFT_CLICK = "left-click"
DOUBLE_CLICK = "double-click"
RIGHT_CLICK = "right-click"
LEFT_RELEASE = "left-release"
LEFT_RELEASE_OUT = "left-release-out"  # released off the grabbed item's row
RIGHT_RELEASE = "right-release"
LEFT_MOVE = "left-move"
LEFT_MOVE_IN = "left-move-in"  # dragging, still on the grabbed item's row
LEFT_MOVE_OUT = "left-move-out"  # dragging, off the grabbed item (or segment)
RIGHT_MOVE = "right-move"
MOVE = "move"  # no button held (only reported while captured)
# The drag in progress was abandoned: a button was pressed while the other was
# still recorded as held, i.e. that one's release never arrived (some
# terminals never report a right release). Ends the grab like a release, but
# completes nothing - no button fires, no menu entry activates.
CANCEL = "cancel"
WHEEL_UP = "wheel-up"
WHEEL_DOWN = "wheel-down"
WHEEL_LEFT = "wheel-left"  # horizontal wheel / tilt (buttons 6 and 7)
WHEEL_RIGHT = "wheel-right"

PRESSES = frozenset((LEFT_CLICK, DOUBLE_CLICK, RIGHT_CLICK))
RELEASES = frozenset((LEFT_RELEASE, LEFT_RELEASE_OUT, RIGHT_RELEASE, CANCEL))
MOVES = frozenset((LEFT_MOVE, LEFT_MOVE_IN, LEFT_MOVE_OUT, RIGHT_MOVE, MOVE))

DOUBLE_CLICK_SECONDS = 0.3

# Button code (Cb) bits of a report. The low two bits pick the button (0 left,
# 1 middle, 2 right, 3 X10's "released"); shift/meta/ctrl are flags, MOTION
# marks a move, WHEEL selects buttons 4-7 (64 up, 65 down, 66 left, 67 right)
# and EXTRA buttons 8-11.
CB_BUTTON = 3
CB_MODIFIERS = 4 | 8 | 16
CB_MOTION = 32
CB_WHEEL = 64
CB_EXTRA = 128
_WHEELS = {0: WHEEL_UP, 1: WHEEL_DOWN, 2: WHEEL_LEFT, 3: WHEEL_RIGHT}

# Terminal modes (DECSET): 1000 clicks, 1003 clicks + all motion, 1006 SGR
# encoding. Setting 1000 or 1003 replaces the other.
_CLICKS = "\033[?1000h"
_MOTION = "\033[?1003h"
_SGR = "\033[?1006h"
_OFF = "\033[?1003l\033[?1002l\033[?1000l\033[?1006l"


def _send(seq: str):
    print(seq, end="", flush=True)


@dataclasses.dataclass(frozen=True)
class MouseReport:
    """One raw mouse report: the button code `cb` (see CB_*), the 0-based
    screen cell, and whether it is an SGR release (`m`; X10 reports a release
    as button 3 instead)."""

    cb: int
    x: int
    y: int
    release: bool = False

    @classmethod
    def from_sgr(cls, body: bytes) -> typing.Optional["MouseReport"]:
        """`body` is what follows `ESC [ <`: `Cb;Cx;Cy` then `M` or `m`."""
        if not body or body[-1:] not in (b"M", b"m"):
            return None
        try:
            cb, x, y = (int(n) for n in body[:-1].split(b";"))
        except ValueError:
            return None
        return cls(cb, x - 1, y - 1, body[-1:] == b"m")

    @classmethod
    def from_x10(cls, cb: int, cx: int, cy: int) -> "MouseReport":
        """The three bytes that follow `ESC [ M` (each value + 32, 1-based)."""
        return cls(cb - 32, cx - 33, cy - 33)


@dataclasses.dataclass(frozen=True)
class MouseEvent:
    """One mouse event. `x`/`y` are relative to the handler receiving it;
    `screen_x`/`screen_y` are absolute; `rel_x`/`rel_y` the motion since the
    previous report (used to drag windows)."""

    type: str
    screen_x: int
    screen_y: int
    x: int = 0
    y: int = 0
    rel_x: int = 0
    rel_y: int = 0

    @property
    def is_press(self) -> bool:
        return self.type in PRESSES

    @property
    def is_release(self) -> bool:
        return self.type in RELEASES

    @property
    def is_move(self) -> bool:
        return self.type in MOVES

    @property
    def is_left_press(self) -> bool:
        return self.type in (LEFT_CLICK, DOUBLE_CLICK)

    def at(self, x: int, y: int) -> "MouseEvent":
        """This event retargeted to a handler that sees it at (x, y)."""
        return dataclasses.replace(self, x=x, y=y)

    def with_type(self, type: str) -> "MouseEvent":
        return dataclasses.replace(self, type=type)


class MouseTracker:
    """Decodes mouse reports and holds the cross-event state: which
    buttons are down, double-click timing, the drag grab, and motion capture."""

    def __init__(self, log=None):
        self.log = log  # anything with .debug(str); optional
        self.screen_x = 0  # last reported position (persistent)
        self.screen_y = 0
        self.left_pressed = False
        self.right_pressed = False
        self._click_x = 0
        self._click_y = 0
        self._click_time = time.time()
        self._capture: typing.Set[object] = set()
        # Drag grab: the view the left press landed in, and the item (row or
        # header) that handled the press plus its coordinate offset.
        self.grabbed_view = None
        self.grabbed_item = None
        self._item_origin = (0, 0)

    # -- position -------------------------------------------------------------

    @property
    def position(self) -> typing.Tuple[int, int]:
        """Last known (x, y) screen position."""
        return self.screen_x, self.screen_y

    # -- terminal modes -------------------------------------------------------

    def enable(self):
        """Ask the terminal for mouse reports (again, e.g. after a shell-out),
        restoring motion reporting if something still captures it."""
        _send(_SGR + (_MOTION if self._capture else _CLICKS))

    def disable(self):
        """Stop all mouse reporting (before handing the terminal back)."""
        _send(_OFF)

    # -- motion capture -------------------------------------------------------

    def capture(self, owner=None):
        if not self._capture:
            _send(_MOTION)  # start motion reports
        self._capture.add(owner)

    def release(self, owner=None):
        if owner in self._capture:
            self._capture.remove(owner)
            if not self._capture:
                _send(_CLICKS)  # back to clicks only

    def is_capturing(self, owner) -> bool:
        return owner in self._capture

    # -- grab -----------------------------------------------------------------

    def grab(self, item, event: MouseEvent):
        """Route the rest of this drag to `item`, in the coordinates `event`
        was delivered to it in."""
        self.grabbed_item = item
        self._item_origin = (event.screen_x - event.x, event.screen_y - event.y)

    def item_event(self, event: MouseEvent) -> MouseEvent:
        """`event` in the grabbed item's coordinates."""
        ox, oy = self._item_origin
        return event.at(event.screen_x - ox, event.screen_y - oy)

    def drop_grab_of(self, owner_view):
        """Forget a grabbed item that belongs to `owner_view` (its rows are being
        discarded), so a queued move/release cannot reach a stale row."""
        if getattr(self.grabbed_item, "_view", None) is owner_view:
            self.grabbed_item = None

    def end_drag(self):
        self.grabbed_view = None
        self.grabbed_item = None

    # -- decoding -------------------------------------------------------------

    def _debug(self, txt):
        if self.log is not None:
            self.log.debug(txt)

    def decode(self, report: MouseReport) -> typing.List[MouseEvent]:
        """The events one report stands for, oldest first."""
        screen_x, screen_y = report.x, report.y
        rel_x = screen_x - self.screen_x
        rel_y = screen_y - self.screen_y
        self.screen_x = screen_x
        self.screen_y = screen_y
        self._debug(f"Mouse report: {report}")

        type = self._decode_type(report)
        if not type:
            return []

        events = []
        # A press of one button while the other is still recorded as held means
        # that one's release was lost: cancel its gesture first.
        if type == RIGHT_CLICK and self.left_pressed:
            self.left_pressed = False
            events.append(CANCEL)
        if type in (LEFT_CLICK, DOUBLE_CLICK) and self.right_pressed:
            self.right_pressed = False
            events.append(CANCEL)
        events.append(type)

        return [MouseEvent(t, screen_x, screen_y, 0, 0, rel_x, rel_y) for t in events]

    def _decode_type(self, report: MouseReport) -> str:
        # Modifier keys are ignored: shift-click is a click.
        cb = report.cb & ~CB_MODIFIERS
        if cb & CB_EXTRA:
            return ""  # buttons 8-11
        if cb & CB_MOTION:
            if self.left_pressed:
                return LEFT_MOVE
            if self.right_pressed:
                return RIGHT_MOVE
            return MOVE
        if cb & CB_WHEEL:
            # Wheels only press; ignore the release some terminals send.
            return "" if report.release else _WHEELS[cb & CB_BUTTON]

        button = cb & CB_BUTTON
        if report.release or button == 3:
            # X10 says only "released"; SGR names the button.
            if button == 0 or (button == 3 and self.left_pressed):
                if not self.left_pressed:
                    return ""  # a release with no matching press
                self.left_pressed = False
                return LEFT_RELEASE
            if button == 2 or button == 3:
                if not self.right_pressed:
                    return ""
                self.right_pressed = False
                return RIGHT_RELEASE
            return ""  # middle button
        if button == 0:
            now = time.time()
            self.left_pressed = True
            if (
                now - self._click_time < DOUBLE_CLICK_SECONDS
                and self.screen_x == self._click_x
                and self.screen_y == self._click_y
            ):
                type = DOUBLE_CLICK
            else:
                self._click_time = now
                type = LEFT_CLICK
            self._click_x = self.screen_x
            self._click_y = self.screen_y
            return type
        if button == 2:
            self.right_pressed = True
            return RIGHT_CLICK
        return ""  # middle button

    def adjust_for_grab(self, event: MouseEvent) -> MouseEvent:
        """Rewrite a drag event relative to the grabbed item's row: IN while
        still on it, OUT once the cursor (or the release) has left it."""
        if self.grabbed_item is None:
            return event
        type = event.type
        if self._click_y == event.screen_y:
            if type == LEFT_MOVE:
                return event.with_type(LEFT_MOVE_IN)
        elif type == LEFT_MOVE:
            return event.with_type(LEFT_MOVE_OUT)
        elif type == LEFT_RELEASE:
            return event.with_type(LEFT_RELEASE_OUT)
        return event

    def update_capture_for(self, event: MouseEvent):
        """Stream motion while a button is held: on for a press, off again on
        the release."""
        if event.is_press:
            self.capture(None)
        if event.is_release:
            self.release(None)
