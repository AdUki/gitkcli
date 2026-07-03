"""Keyboard and mouse input state.

`KeyboardState` and `MouseState` are the value objects handed to
`handle_input` / `handle_mouse_input`, plus the readers that decode raw curses
events. `from __future__ import annotations` keeps the `View`/`Item` type hints
lazy so this module needs no UI imports — those references are duck-typed at
runtime through the parent `app`.
"""

from __future__ import annotations

import curses
import dataclasses
import time

# Synthetic key codes (negative, so they never collide with real curses codes)
# for sequences curses does not surface as a single key.
KEY_SHIFT_F5 = -100
KEY_CTRL_LEFT = -101
KEY_CTRL_RIGHT = -102
KEY_CTRL_BACKSPACE = -103
KEY_CTRL_DEL = -104
KEY_SHIFT_LEFT = -105
KEY_SHIFT_RIGHT = -106
KEY_ENTER = 10
KEY_RETURN = 13
KEY_TAB = 9
ENTER_KEYS = (curses.KEY_ENTER, KEY_ENTER, KEY_RETURN)


@dataclasses.dataclass
class KeyboardState:
    """Whole keyboard input state passed to handle_input()."""

    key: int = -1  # normalized key code
    sequence: list = dataclasses.field(default_factory=list)  # raw escape bytes

    # The App struct. Unannotated so dataclass does NOT make it a field — the
    # many synthetic KeyboardState(<key>) instances stay single-positional. Set
    # only on the real keyboard (the one that calls read(), which logs).
    app = None

    def read(self, stdscr) -> bool:
        """Read and normalize a key from curses. Returns False when nothing
        usable was read (timeout or an unrecognized escape sequence)."""
        key = stdscr.getch()
        if key < 0:
            return False

        # parse escape sequences
        if key == 27:  # Esc key
            sequence = []
            while key >= 0:
                if key == 27:
                    sequence.clear()
                sequence.append(key)
                key = stdscr.getch()
            self.app.log.debug("Escape sequence: " + str(sequence))
            self.sequence = sequence
            if len(sequence) == 1:
                key = curses.KEY_EXIT
            elif sequence == [27, 91, 49, 53, 59, 50, 126]:
                key = KEY_SHIFT_F5
            elif sequence == [27, 91, 49, 59, 53, 68]:
                key = KEY_CTRL_LEFT
            elif sequence == [27, 91, 49, 59, 53, 67]:
                key = KEY_CTRL_RIGHT
            elif sequence == [27, 91, 49, 59, 50, 68]:
                key = KEY_SHIFT_LEFT
            elif sequence == [27, 91, 49, 59, 50, 67]:
                key = KEY_SHIFT_RIGHT
            elif sequence == [27, 91, 51, 59, 53, 126]:
                key = KEY_CTRL_DEL
            else:
                return False
        else:
            self.app.log.debug("Key: " + str(key))
            self.sequence = [key]

        # Ctrl+Backspace arrives as ^H (8) on most terminals; plain
        # Backspace arrives as KEY_BACKSPACE / 127
        if key == 8:
            key = KEY_CTRL_BACKSPACE

        self.key = key
        return True


@dataclasses.dataclass
class MouseState:
    """Whole mouse input state passed to handle_mouse_input()."""

    event_type: str = ""  # current event ('left-click', 'double-click', ...)
    state: int = 0  # raw curses bstate
    screen_x: int = 0  # absolute screen position (persistent)
    screen_y: int = 0
    x: int = 0  # coordinates relative to the handler being invoked
    y: int = 0
    rel_x: int = 0  # delta since previous event (used for resize)
    rel_y: int = 0
    click_x: int = 0  # position/time of last left-press (double-click detection)
    click_y: int = 0
    click_time: float = dataclasses.field(default_factory=time.time)
    left_pressed: bool = False
    right_pressed: bool = False
    movement_capture: set = dataclasses.field(default_factory=set)
    clicked_view: "View|None" = None  # drag targets that captured a press
    clicked_item: "Item|None" = None

    # The App struct, set on the single real mouse in launch_curses. Unannotated
    # so dataclass does not treat it as a field.
    app = None

    def capture_mouse_movement(self, enable: bool, id=None):
        enabled = len(self.movement_capture) > 0
        if enable:
            self.movement_capture.add(id)
            if not enabled:
                print(
                    "\033[?1003h", end="", flush=True
                )  # start capturing mouse movement
        elif id in self.movement_capture:
            self.movement_capture.remove(id)
            if enabled and len(self.movement_capture) == 0:
                print("\033[?1000h", end="", flush=True)  # end capturing mouse movement

    def read_curses_event(self, stdscr) -> bool:
        """Decode a curses mouse event into this state. Returns False when the
        event should be ignored (a release with no matching press, or an
        unrecognized button state)."""
        _, screen_x, screen_y, _, self.state = curses.getmouse()
        self.rel_x = screen_x - self.screen_x
        self.rel_y = screen_y - self.screen_y
        self.screen_x = screen_x
        self.screen_y = screen_y
        self.app.log.debug("Mouse state: " + str(self.state))

        self.event_type = ""
        if self.state == curses.BUTTON1_PRESSED:
            now = time.time()
            self.left_pressed = True
            if (
                now - self.click_time < 0.3
                and self.screen_x == self.click_x
                and self.screen_y == self.click_y
            ):
                self.event_type = "double-click"
            else:
                self.click_time = now
                self.event_type = "left-click"
            self.click_x = self.screen_x
            self.click_y = self.screen_y

        elif self.state == curses.BUTTON1_RELEASED:
            if not self.left_pressed:
                return False
            self.left_pressed = False
            self.event_type = "left-release"

        elif self.state == curses.BUTTON3_PRESSED:
            self.right_pressed = True
            self.event_type = "right-click"

        elif self.state == curses.BUTTON3_RELEASED:
            if not self.right_pressed:
                return False
            self.right_pressed = False
            self.event_type = "right-release"

        elif self.state == curses.REPORT_MOUSE_POSITION:
            if self.left_pressed:
                self.event_type = "left-move"
            elif self.right_pressed:
                self.event_type = "right-move"
            else:
                self.event_type = "move"

        elif self.state & curses.BUTTON1_DOUBLE_CLICKED:
            self.event_type = "double-click"

        elif self.state == curses.BUTTON4_PRESSED:
            self.event_type = "wheel-up"

        elif self.state == curses.BUTTON5_PRESSED:
            self.event_type = "wheel-down"

        return self.event_type != ""

    def _update_capture(self, event_type: str):
        """Start/stop the terminal mouse-movement capture around a click/release,
        so movement events only stream in while a button is actually held."""
        if "click" in event_type:
            self.capture_mouse_movement(True)
        if "release" in event_type:
            self.capture_mouse_movement(False)

    def _adjust_drag_event(self, event_type: str) -> str:
        """Rewrite a move/release event relative to the item a drag started on:
        'in' while still over its row, 'out' once the cursor (or the release)
        has left it. No-op when no item is being dragged."""
        if self.clicked_item:
            if self.click_y == self.screen_y:
                if event_type == "left-move":
                    event_type = "left-move-in"
            elif event_type == "left-move":
                event_type = "left-move-out"
            elif event_type == "left-release":
                event_type = "left-release-out"
        return event_type

    def _route_bottom_bar(self, event_type: str) -> bool:
        """Route a single click on the reserved bottom row to its F-key bar
        entry. Returns True when the click was consumed (nothing else should
        process it). Not while a modal popup is open: let those fall through so
        an outside-click dismisses it. Only 'left-click', never 'double-click':
        a bar entry has no double-click meaning, and firing twice would e.g.
        open then instantly close the menu/preferences popup an entry just
        raised."""
        if event_type == "left-click":
            bar_y = self.app.screen.stdscr.getmaxyx()[0] - 1
            active = self.app.screen.get_active_view()
            if self.screen_y == bar_y and not (active and active.is_popup):
                for x_start, x_end, callback in self.app.screen.bar_hitmap:
                    if x_start <= self.screen_x < x_end:
                        callback()
                        break
                return True
        return False

    def _find_enclosed_view(self):
        """The topmost shown view whose rect contains the current mouse
        position (a popup counts as enclosing regardless of its rect, since it
        should capture everything under it), or None over empty space."""
        for view in reversed(self.app.screen.showed_views):
            if view.is_popup or view.win.enclose(self.screen_y, self.screen_x):
                return view
        return None

    def process_mouse_event(self, active_view: View, event_type: str = None):
        if event_type is None:
            event_type = self.event_type

        self._update_capture(event_type)
        event_type = self._adjust_drag_event(event_type)

        # expose the (possibly adjusted) type to handlers reading mouse.event_type
        self.event_type = event_type

        if self._route_bottom_bar(event_type):
            return

        enclosed_view = self._find_enclosed_view()

        if enclosed_view and event_type == "left-click":
            self.clicked_view = enclosed_view
            if enclosed_view and enclosed_view != active_view:
                enclosed_view.show()
                enclosed_view.dirty = True
                active_view.dirty = True

        send_event_to = None
        view_to_process = enclosed_view
        item_x = 0
        item_y = 0
        if "move" in event_type or "release" in event_type:
            if self.clicked_view:
                view_to_process = self.clicked_view
            if self.clicked_item:
                send_event_to = self.clicked_item
                if self.clicked_view:
                    item_x = self.clicked_view.x
                    item_y = self.clicked_view.y

        if not send_event_to:
            send_event_to = view_to_process

        if view_to_process and send_event_to:
            begin_y, begin_x = view_to_process.win.getbegyx()
            self.x = (self.screen_x - begin_x) - item_x
            self.y = (self.screen_y - begin_y) - item_y
            if send_event_to.handle_mouse_input(self):
                view_to_process.dirty = True

        if "release" in event_type:
            self.clicked_view = None
            self.clicked_item = None
