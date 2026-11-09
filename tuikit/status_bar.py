"""StatusBar: the Midnight-Commander-style function-key bar on the bottom row.

    screen.status_bar.set_entries([("F1", "Help", show_help), ...])

Each entry is a clickable cell (` 1Help      2Open  …`, spread evenly over the
width) and binds its key (`curses.KEY_F1` for "F1"). Two transient states
replace the whole row: a green *flash* (`flash(msg)`, auto-expires after
FLASH_SECONDS) and a blue *working* bar (`working = msg`) shown while a long
operation runs.
"""

from __future__ import annotations

import curses
import time
import typing

from tuikit import colors
from tuikit import mouse as M

FLASH_SECONDS = 2.0


class StatusBar:
    def __init__(self, screen):
        self.screen = screen
        self.entries: typing.List[typing.Tuple[str, str, typing.Callable]] = []
        self.key_actions: typing.Dict[int, typing.Callable] = {}
        # (x_start, x_end, callback) per drawn cell, rebuilt by every draw()
        self.hitmap: typing.List[typing.Tuple[int, int, typing.Callable]] = []
        self.flash_message = ""
        self.flash_time = 0.0
        self.working = ""

    def set_entries(self, entries):
        """`entries`: (key label, name, callback) tuples; a label like "F5"
        also binds that function key."""
        self.entries = list(entries)
        self.key_actions = {}
        for label, _, callback in self.entries:
            code = getattr(curses, "KEY_" + label, None)
            if code is not None:
                self.key_actions[code] = callback

    def action_for_key(self, key: int):
        return self.key_actions.get(key)

    # -- transient states -----------------------------------------------------

    def flash(self, message: str):
        """Replace the bar with `message` on green for FLASH_SECONDS."""
        self.flash_message = message.splitlines()[0] if message else ""
        self.flash_time = time.time()

    def flash_active(self) -> bool:
        """True while a flash keeps the main loop redrawing (still showing, or
        expired but awaiting the draw that clears it)."""
        return bool(self.flash_message)

    # -- input ----------------------------------------------------------------

    def handle_mouse(self, event) -> bool:
        """A click on the bottom row runs its entry. True when consumed. Not
        while a popup is open (an outside click must dismiss it instead), and
        only a single click: a double-click would fire the entry twice."""
        if event.type != M.LEFT_CLICK:
            return False
        bar_y = self.screen.stdscr.getmaxyx()[0] - 1
        active = self.screen.get_active_view()
        if event.screen_y != bar_y or (active and active.is_popup):
            return False
        for x_start, x_end, callback in self.hitmap:
            if x_start <= event.screen_x < x_end:
                callback()
                break
        return True

    # -- drawing --------------------------------------------------------------

    def draw(self, stdscr):
        """Draw onto stdscr's bottom row (under the panel deck). Never writes
        the bottom-right cell: that advances the cursor off-screen and raises
        addwstr() ERR."""
        lines, cols = stdscr.getmaxyx()
        if cols < 2 or lines < 1:
            return
        y = lines - 1

        if self.working:
            stdscr.addstr(
                y,
                0,
                self.working[: cols - 1].ljust(cols - 1),
                colors.bar_attr(colors.BAR_WORK) | curses.A_BOLD,
            )
            self.hitmap = []  # the cells are hidden: swallow clicks
            return

        if self.flash_message:
            if time.time() - self.flash_time < FLASH_SECONDS:
                stdscr.addstr(
                    y,
                    0,
                    self.flash_message[: cols - 1].ljust(cols - 1),
                    colors.bar_attr(colors.BAR_FLASH),
                )
                self.hitmap = []
                return
            self.flash_message = ""  # expired: redraw the key bar

        num_attr = colors.attr(colors.NORMAL)  # key number: plain text
        label_attr = colors.bar_attr(colors.BAR_LABEL)  # black on cyan
        stdscr.addstr(y, 0, " " * (cols - 1), num_attr)

        # Equal cells over the width, the remainder going to the leftmost ones;
        # each is a 2-wide key number then the label padded out on cyan.
        self.hitmap = []
        total = cols - 1
        n = len(self.entries)
        x = 0
        for i, (key, name, callback) in enumerate(self.entries):
            cell_w = total // n + (1 if i < total % n else 0)
            if cell_w < 2:
                break
            num = (key[1:] if key.startswith("F") else key).rjust(2)  # ' 1', '10'
            label = name[: cell_w - len(num)].ljust(cell_w - len(num))
            stdscr.addstr(y, x, num, num_attr)
            if label:
                stdscr.addstr(y, x + len(num), label, label_attr)
            self.hitmap.append((x, x + cell_w, callback))
            x += cell_w
