"""Mouse input: event types, the immutable `MouseEvent` handed to handlers, and
the stateful `MouseTracker` that decodes curses mouse reports.

Event flow
----------
`MouseTracker.read()` turns one curses report into zero or more `MouseEvent`s
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

import curses
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

PRESSES = frozenset((LEFT_CLICK, DOUBLE_CLICK, RIGHT_CLICK))
RELEASES = frozenset((LEFT_RELEASE, LEFT_RELEASE_OUT, RIGHT_RELEASE, CANCEL))
MOVES = frozenset((LEFT_MOVE, LEFT_MOVE_IN, LEFT_MOVE_OUT, RIGHT_MOVE, MOVE))

DOUBLE_CLICK_SECONDS = 0.3


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
    """Decodes curses mouse reports and holds the cross-event state: which
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

    # -- motion capture -------------------------------------------------------

    def capture(self, owner=None):
        if not self._capture:
            print("\033[?1003h", end="", flush=True)  # start motion reports
        self._capture.add(owner)

    def release(self, owner=None):
        if owner in self._capture:
            self._capture.remove(owner)
            if not self._capture:
                print("\033[?1000h", end="", flush=True)  # back to clicks only

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

    def read(self) -> typing.List[MouseEvent]:
        """Decode the pending curses mouse report (after getch() returned
        KEY_MOUSE) into the events to dispatch, oldest first."""
        try:
            _, screen_x, screen_y, _, state = curses.getmouse()
        except curses.error:
            return []
        return self.decode(screen_x, screen_y, state)

    def decode(
        self, screen_x: int, screen_y: int, state: int
    ) -> typing.List[MouseEvent]:
        rel_x = screen_x - self.screen_x
        rel_y = screen_y - self.screen_y
        self.screen_x = screen_x
        self.screen_y = screen_y
        self._debug("Mouse state: " + str(state))

        type = self._decode_type(state)
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

    def _decode_type(self, state: int) -> str:
        if state == curses.BUTTON1_PRESSED:
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
        if state == curses.BUTTON1_RELEASED:
            if not self.left_pressed:
                return ""  # a release with no matching press
            self.left_pressed = False
            return LEFT_RELEASE
        if state == curses.BUTTON3_PRESSED:
            self.right_pressed = True
            return RIGHT_CLICK
        if state == curses.BUTTON3_RELEASED:
            if not self.right_pressed:
                return ""
            self.right_pressed = False
            return RIGHT_RELEASE
        if state == curses.REPORT_MOUSE_POSITION:
            if self.left_pressed:
                return LEFT_MOVE
            if self.right_pressed:
                return RIGHT_MOVE
            return MOVE
        if state & curses.BUTTON1_DOUBLE_CLICKED:
            return DOUBLE_CLICK
        if state == curses.BUTTON4_PRESSED:
            return WHEEL_UP
        if state == curses.BUTTON5_PRESSED:
            return WHEEL_DOWN
        return ""

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
