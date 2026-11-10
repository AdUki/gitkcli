"""ListView: a scrollable, selectable list of items with search and copy.

Keys: Up/Down (k/j), PgUp/PgDn (Ctrl+b/f), Home/End (g/G), Left/Right (h/l)
and Shift+Left/Right (H/L) to scroll sideways, `/` to search, n/N for the
next/previous match. The selected item sees every key first. Mouse: click to
select, wheel to scroll; the row under the cursor gets the event in its own
coordinates (x = text column, y = row index).
"""

from __future__ import annotations

import curses
import re
import typing

from tuikit import colors
from tuikit import keys as K
from tuikit import mouse as M
from tuikit.segmented import WindowTopBarItem
from tuikit.view import (
    MIN_FLOAT_WIDTH,
    MODE_FULLSCREEN,
    MODE_WINDOW,
    WINDOW_MAX_SHARE,
    WINDOW_PADX,
    WINDOW_PADY,
    View,
)

HSCROLL_BIG_STEP = 10
WHEEL_STEP = 5


class ListView(View):
    def __init__(
        self,
        app,
        id: str,
        view_mode: str = MODE_FULLSCREEN,
        x: typing.Optional[int] = None,
        y: typing.Optional[int] = None,
        height: typing.Optional[int] = None,
        width: typing.Optional[int] = None,
    ):
        super().__init__(app, id, view_mode, x, y, height, width)
        self.items = []
        self._selected: int = 0
        self._offset_y: int = 0
        self._offset_x: int = 0
        self.autoscroll: bool = False  # follow the tail as items are appended
        self._search_dialog = None

    def set_search_dialog(self, search_dialog):
        """Attach a SearchDialog: `/` opens it, n/N repeat it, and rows it
        `matches()` are highlighted."""
        self._search_dialog = search_dialog
        self._search_dialog.parent_list_view = self

    # -- content --------------------------------------------------------------

    def append(self, item):
        item._view = self
        self.items.append(item)
        # The new row is on-screen when (len - offset_y) <= height ('<=': a row
        # landing exactly on the last visible line is on-screen).
        if len(self.items) - self._offset_y <= self.height:
            self.dirty = True
        else:
            # Off-screen: the body needn't redraw, but a header counter
            # ("[current/total]") changed - a cheap header-only redraw.
            self.header_dirty = True
        if self.autoscroll:
            # Follow the tail. The on-screen check above ran against the OLD
            # offset, so mark dirty whenever the offset moves.
            new_offset = max(0, len(self.items) - self.height)
            if new_offset != self._offset_y:
                self._offset_y = new_offset
                self.dirty = True

    def insert(self, index: int, item):
        """Insert `item` before row `index`. The selection stays on the row it
        was on, and a row inserted above the visible ones does not scroll
        what is on screen."""
        was_empty = not self.items
        item._view = self
        self.items.insert(index, item)
        if not was_empty:
            if index <= self._selected:
                self._selected += 1
            if index < self._offset_y:
                self._offset_y += 1
        self.dirty = True

    def clear(self):
        self.app.log.debug(f"Clear view {self.id}")
        # The rows cease to exist: drop a mouse grab on one of them, so a
        # queued move/release cannot reach a stale item.
        self.app.mouse.drop_grab_of(self)
        self.items = []
        self.set_selected(0)
        self._offset_y = 0
        self._offset_x = 0
        self.dirty = True

    # -- geometry -------------------------------------------------------------

    def pack(self, x=None, y=None, min_width=0, padx=0, pady=0, **limits):
        """Shrink-wrap the window around its rows, like Tk's pack: see
        packed_size() for the size (`limits`: max_width / max_height). x/y
        place the corner; None keeps the current position (centred unless
        placed or moved)."""
        height, width = self.packed_size(min_width, padx, pady, **limits)
        self.set_dimensions(
            self.fixed_x if x is None else x,
            self.fixed_y if y is None else y,
            height,
            width,
        )

    def packed_size(self, min_width=0, padx=0, pady=0, max_width=None, max_height=None):
        """(height, width) of a box around the rows: as tall as the rows plus
        `pady` blank lines, as wide as the widest row (`natural_width()`) plus
        `padx` spare columns, never narrower than `min_width` or than the
        header - for a popup, the title inset in its top border. Both include
        the box and stop at max_width / max_height; only the rows that fit
        are measured, so a long list costs no more than a screenful."""
        height = len(self.items) + pady + 2
        if max_height is not None:
            height = min(height, max_height)
        shown = self.items[self._offset_y :][: max(0, height - 2)]
        content = max((item.natural_width() for item in shown), default=0)
        content += padx
        if self.header_item is not None:
            self.update_header()  # e.g. the [current/total] counter, as drawn
            if self.is_popup:
                # ┌─ Title ─┐: the title, a space each side, and a rule cell
                title = len(self.header_item.get_text().strip())
                content = max(content, title + 4 if title else 0)
            else:
                content = max(content, self.header_item.natural_width())
        width = max(min_width, content + 2)
        if max_width is not None:
            width = min(width, max_width)
        return height, width

    def window_size(self, lines, cols):
        """A floated list packs around its rows with some air, up to
        WINDOW_MAX_SHARE of the screen."""
        return self.packed_size(
            MIN_FLOAT_WIDTH,
            WINDOW_PADX,
            WINDOW_PADY,
            max_width=int(cols * WINDOW_MAX_SHARE),
            max_height=int(lines * WINDOW_MAX_SHARE),
        )

    def get_selected(self) -> typing.Any:
        if 0 <= self._selected < len(self.items):
            return self.items[self._selected]
        return None

    def set_selected(self, what: int | str | re.Pattern, visible_mode="center") -> bool:
        """Select by index, by the first row containing a string, or by the
        first row a pattern matches. A non-selectable target lands on the
        nearest selectable row (travel direction first). `visible_mode` -
        center / top / bottom - places a row that was off-screen."""
        new_index = None

        if isinstance(what, int):
            if (0 <= what < len(self.items)) or (what <= 0 and not self.items):
                new_index = what
        elif isinstance(what, (str, re.Pattern)):
            test = (
                (lambda t: what in t)
                if isinstance(what, str)
                else (lambda t: what.match(t))
            )
            for i, item in enumerate(self.items):
                if test(item.get_text()):
                    new_index = i
                    break

        if new_index is None:
            return False
        if self._selected == new_index:
            return True

        # A non-selectable target: search both directions from the ORIGINAL
        # target, travel direction first, stopping at the first hit and never
        # crossing the current selection. Neither finds one: no change.
        direction = 1 if new_index > self._selected else -1
        if 0 <= new_index < len(self.items) and not self.items[new_index].is_selectable:
            target = new_index
            for step in (direction, -direction):
                i = target + step
                while 0 <= i < len(self.items) and i != self._selected:
                    if self.items[i].is_selectable:
                        new_index = i
                        break
                    i += step
                if new_index != target:
                    break
            if not self.items[new_index].is_selectable:
                return False

        self._selected = new_index
        self.dirty = True

        if self._offset_y <= self._selected < self._offset_y + self.height:
            return True  # already visible: keep the scroll position

        if visible_mode == "center":
            self._offset_y = max(
                0,
                min(
                    self._selected - int(self.height / 2),
                    len(self.items) - self.height,
                ),
            )
        elif visible_mode == "top":
            self._offset_y = max(0, self._selected)
        elif visible_mode == "bottom":
            self._offset_y = max(0, self._selected - self.height + 1)
        return True

    def row_screen_position(self, index=None) -> typing.Tuple[int, int]:
        """Screen (x, y) where row `index` (default: the selection) starts -
        e.g. to anchor a keyboard-opened menu at it. x can be negative while
        scrolled sideways."""
        if index is None:
            index = self._selected
        win_y, win_x = self.win.getbegyx()
        return (
            win_x + self.x - self._offset_x,
            win_y + self.y + index - self._offset_y,
        )

    def _scroll_x(self, delta: int):
        visible = self.items[self._offset_y : self._offset_y + self.height]
        widest = max((len(item.get_text()) for item in visible), default=0)
        max_offset_x = max(0, widest - self.width)
        self._offset_x = max(0, min(self._offset_x + delta, max_offset_x))

    def _scroll_y(self, delta: int):
        self._offset_y = max(
            0, min(self._offset_y + delta, max(0, len(self.items) - self.height))
        )

    # -- clipboard ------------------------------------------------------------

    def copy_text_to_clipboard(self):
        text = "\n".join(item.get_text() for item in self.items)
        if text:
            self.app.copy_to_clipboard(text)

    def copy_text_range_to_clipboard(self, to_item):
        """Copy the rows between the selection and `to_item`, inclusive."""
        lo, hi = sorted((self._selected, self.items.index(to_item)))
        self.app.copy_to_clipboard(
            "\n".join(item.get_text() for item in self.items[lo : hi + 1])
        )

    # -- search ---------------------------------------------------------------

    def search(self, backward: bool = False, repeat: bool = False) -> bool:
        """Move to the next/previous row the search dialog matches; `repeat`
        wraps around. True when one was found."""
        if not self._search_dialog:
            return False

        ranges = []
        if not backward:
            ranges.append(range(self._selected + 1, len(self.items)))
            if repeat:
                ranges.append(range(0, self._selected + 1))
        else:
            ranges.append(range(self._selected - 1, -1, -1))
            if repeat:
                ranges.append(range(len(self.items) - 1, self._selected - 1, -1))

        for search_range in ranges:
            for i in search_range:
                if self._search_dialog.matches(self.items[i]):
                    self.set_selected(i)
                    return True
        return False

    def open_search(self):
        if self._search_dialog:
            self._search_dialog.clear()
            self._search_dialog.show()

    # -- input ----------------------------------------------------------------

    def handle_mouse(self, event) -> bool:
        if event.type == M.WHEEL_UP:
            self._offset_y = max(0, self._offset_y - WHEEL_STEP)
            return True
        if event.type == M.WHEEL_DOWN:
            self._scroll_y(WHEEL_STEP)
            return True

        if not self.resize_mode:
            view_x = event.x - self.x
            view_y = event.y - self.y
            index = self._offset_y + view_y
            if (
                0 <= view_y < self.height
                and 0 <= view_x < self.width
                and 0 <= index < len(self.items)
            ):
                if event.is_move and self._selected == index:
                    return False  # hovering over the same row: no redraw
                selected = False
                # Select on a click, or on hover while this list captures motion
                # (a menu tracking the pointer).
                if event.is_left_press or (
                    event.is_move and self.app.mouse.is_capturing(self)
                ):
                    if self.items[index].is_selectable:
                        self.set_selected(index)
                        selected = True
                item = self.items[index]
                item_event = event.at(view_x + self._offset_x, index)
                handled = item.handle_mouse(item_event)
                if handled and event.is_left_press:
                    self.app.mouse.grab(item, item_event)
                if selected or handled:
                    return True

        return super().handle_mouse(event)

    def handle_key(self, event):
        key = event.key
        if not self.items:
            return super().handle_key(event)

        selected_item = self.get_selected()
        if selected_item and selected_item.handle_key(event):
            self.dirty = True
            return True

        if key in (curses.KEY_UP, ord("k")):
            self.set_selected(self._selected - 1, visible_mode="top")
        elif key in (curses.KEY_DOWN, ord("j")):
            self.set_selected(self._selected + 1, visible_mode="bottom")
        elif key in (curses.KEY_LEFT, ord("h")):
            self._scroll_x(-1)
        elif key in (curses.KEY_RIGHT, ord("l")):
            self._scroll_x(1)
        elif key in (K.KEY_SHIFT_LEFT, ord("H")):
            self._scroll_x(-HSCROLL_BIG_STEP)
        elif key in (K.KEY_SHIFT_RIGHT, ord("L")):
            self._scroll_x(HSCROLL_BIG_STEP)
        elif key in (curses.KEY_PPAGE, K.ctrl("b")):
            self._offset_y = max(0, self._offset_y - self.height)
            self.set_selected(max(0, self._selected - self.height))
        elif key in (curses.KEY_NPAGE, K.ctrl("f")):
            self._scroll_y(self.height)
            self.set_selected(
                min(self._selected + self.height, max(0, len(self.items) - 1))
            )
        elif key in (curses.KEY_HOME, ord("g")):
            self.set_selected(0)
        elif key in (curses.KEY_END, ord("G")):
            self.set_selected(max(0, len(self.items) - 1))
        elif key == ord("/"):
            self.open_search()
        elif key == ord("n"):
            # wrap past the last match back to the first (less/vim behaviour)
            self.search(repeat=True)
        elif key == ord("N"):
            self.search(backward=True, repeat=True)
        else:
            return super().handle_key(event)
        return True

    # -- drawing --------------------------------------------------------------

    def update_header(self):
        if isinstance(self.header_item, WindowTopBarItem):
            current = self._selected + 1 if self.items else 0
            self.header_item.set_counter(current, len(self.items))

    def draw(self):
        separator_rows = []
        win_height, win_width = self.win.getmaxyx()
        for i in range(0, min(self.height, len(self.items) - self._offset_y)):
            idx = i + self._offset_y
            item = self.items[idx]
            selected = idx == self._selected
            matched = (
                self._search_dialog.matches(item) if self._search_dialog else False
            )
            # curses raises when writing the bottom-right cell of a window (a
            # borderless view's last row; a box keeps rows off that cell). A
            # separator may also join a divider with one more cell (┤).
            width = self.width
            end = self.x + width + (1 if item.is_separator else 0)
            if self.y + i == win_height - 1 and end >= win_width:
                width -= 1
            if item.is_separator:
                separator_rows.append((i, width))
            else:
                self.win.move(self.y + i, self.x)
                item.draw_line(
                    self.win, self._offset_x, width, selected, matched, False
                )

        self.win.clrtobot()
        super().draw()

        if separator_rows:
            # Separators are drawn after the border so they can join onto it,
            # in its colour (blue when active, grey when not, red in a red box).
            attr = self.border_color()
            join = colors.attr(colors.DIM)  # joins onto a split divider are neutral
            sides = self.border_sides()
            if sides is not None:
                left = join if "left" in sides else None
                right = join if "right" in sides else None
            elif self.view_mode == MODE_WINDOW:
                left = right = attr
            else:
                left = right = None
            for i, width in separator_rows:
                if left is not None:
                    self.win.move(self.y + i, self.x - 1)
                    self.win.addstr("├", left)
                else:
                    self.win.move(self.y + i, self.x)
                self.win.addstr("─" * width, attr)
                if right is not None:
                    self.win.addstr("┤", right)
