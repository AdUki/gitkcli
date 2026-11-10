"""ListView: a scrollable, selectable list of items with search and copy.

Keys: Up/Down (k/j), PgUp/PgDn (Ctrl+b/f), Home/End (g/G), Left/Right (h/l)
and Shift+Left/Right (H/L) to scroll sideways, `/` to search, n/N for the
next/previous match. The selected item sees every key first. Mouse: click to
select, wheel to scroll; the row under the cursor gets the event in its own
coordinates (x = text column, y = row index).

Text selection (views that set `text_selection`): a left drag selects text
character-wise, Shift+Up/Down select whole lines from the cursor; Esc or
plain cursor movement drops it. `selected_text()` / `selected_rows()` read it.
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

# Keys that move the cursor without extending the selection: they drop it.
_PLAIN_MOVES = (
    curses.KEY_UP,
    curses.KEY_DOWN,
    ord("k"),
    ord("j"),
    curses.KEY_PPAGE,
    curses.KEY_NPAGE,
    curses.KEY_HOME,
    curses.KEY_END,
    ord("g"),
    ord("G"),
)


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
        # Text selection, when `text_selection` is on: the fixed end (anchor)
        # and the moving end (head), each (row, text column); `_sel_lines`
        # selects whole lines (keyboard), `_sel_drag` is a mouse drag in
        # progress.
        self.text_selection = False
        self._sel_anchor = None
        self._sel_head = None
        self._sel_lines = False
        self._sel_drag = False

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
        self.clear_selection()
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

    # -- text selection -------------------------------------------------------

    def has_selection(self) -> bool:
        return (
            self._sel_anchor is not None
            and self._sel_head is not None
            and (self._sel_lines or self._sel_anchor != self._sel_head)
        )

    def clear_selection(self) -> bool:
        """Drop the selection; True if there was one."""
        had = self.has_selection()
        self._sel_anchor = self._sel_head = None
        self._sel_lines = self._sel_drag = False
        if had:
            self.dirty = True
        return had

    def select_rows(self, first: int, last: int):
        """Select rows `first`..`last` (inclusive) as whole lines; the cursor
        goes to `last`."""
        self._sel_anchor = (first, 0)
        self._sel_head = (last, 0)
        self._sel_lines = True
        self.dirty = True

    def selection_bounds(self):
        """((row, col), (row, col)) of the selection, start inclusive, end
        exclusive (a col of None = to the end of the row); None when there is
        no selection."""
        if not self.has_selection():
            return None
        (r1, c1), (r2, c2) = sorted((self._sel_anchor, self._sel_head))
        if self._sel_lines:
            return (r1, 0), (r2, None)
        # The cell under the pointer is part of it, at whichever end.
        return (r1, c1), (r2, c2 + 1)

    def selected_rows(self) -> typing.Optional[typing.Tuple[int, int]]:
        """(first, last) row indexes the selection touches, or None."""
        bounds = self.selection_bounds()
        if bounds is None:
            return None
        (r1, _), (r2, _) = bounds
        last = len(self.items) - 1
        return min(r1, last), min(r2, last)

    def selected_text(self) -> str:
        bounds = self.selection_bounds()
        if bounds is None:
            return ""
        (r1, c1), (r2, c2) = bounds
        lines = []
        for row in range(r1, min(r2, len(self.items) - 1) + 1):
            text = self.items[row].get_text()
            start = c1 if row == r1 else 0
            end = c2 if row == r2 else None
            lines.append(text[start:end])
        return "\n".join(lines)

    def _extend_line_selection(self, step: int):
        """Shift+Up/Down: grow or shrink a whole-line selection by moving the
        cursor one row."""
        if not (self.has_selection() and self._sel_lines):
            self._sel_anchor = (self._selected, 0)
            self._sel_lines = True
        self.set_selected(
            self._selected + step, visible_mode="top" if step < 0 else "bottom"
        )
        self._sel_head = (self._selected, 0)
        self.dirty = True

    def _drag_selection(self, event) -> bool:
        """A left press starts a selection, moves while held extend it (to
        any row: past the top or bottom edge the list scrolls), the release
        ends the drag."""
        view_x = event.x - self.x
        view_y = event.y - self.y
        if event.is_left_press:
            index = self._offset_y + view_y
            if 0 <= view_y < self.height and 0 <= index < len(self.items):
                self.clear_selection()
                self._sel_anchor = (index, max(0, view_x + self._offset_x))
                self._sel_drag = True
            return False  # the click itself goes on as usual
        if not self._sel_drag or not self.items:
            return False
        if event.is_release:
            self._sel_drag = False
            return self.has_selection()
        if not event.is_move:
            return False
        if view_y < 0:
            self._scroll_y(-1)
            view_y = 0
        elif view_y >= self.height:
            self._scroll_y(1)
            view_y = self.height - 1
        index = max(0, min(self._offset_y + view_y, len(self.items) - 1))
        self._sel_head = (index, max(0, view_x + self._offset_x))
        self.dirty = True
        return True

    def _draw_selection(self, i: int, idx: int, width: int, bounds):
        """Repaint the selected cells of screen row `i` (item `idx`) in the
        selection colour, keeping each cell's character and foreground."""
        (r1, c1), (r2, c2) = bounds
        if not (r1 <= idx <= r2):
            return
        text_len = len(self.items[idx].get_text())
        start = c1 if idx == r1 else 0
        if self._sel_lines:
            end = self._offset_x + width  # a whole-line band
        elif idx == r2:
            end = c2
        else:
            end = max(text_len, start + 1)  # past the text: the line break
        x0 = max(0, start - self._offset_x)
        x1 = min(width, end - self._offset_x)
        y = self.y + i
        for x in range(x0, x1):
            cell = self.win.inch(y, self.x + x)
            role = colors.role_of_pair(curses.pair_number(cell & curses.A_COLOR))
            self.win.chgat(y, self.x + x, 1, colors.selection_attr(role))

    # -- clipboard ------------------------------------------------------------

    def copy_text_to_clipboard(self):
        text = "\n".join(item.get_text() for item in self.items)
        if text:
            self.app.copy_to_clipboard(text)

    def copy_selection_to_clipboard(self) -> bool:
        """Copy the selected text; False when nothing is selected."""
        text = self.selected_text()
        if not text:
            return False
        self.app.copy_to_clipboard(text)
        return True

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
        if event.type == M.WHEEL_LEFT:
            self._scroll_x(-HSCROLL_BIG_STEP)
            return True
        if event.type == M.WHEEL_RIGHT:
            self._scroll_x(HSCROLL_BIG_STEP)
            return True

        if self.text_selection and not self.resize_mode:
            if self._drag_selection(event):
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

        if self.text_selection:
            if key == K.KEY_ESC and self.clear_selection():
                return True
            if key in (K.KEY_SHIFT_UP, K.KEY_SHIFT_DOWN):
                self._extend_line_selection(-1 if key == K.KEY_SHIFT_UP else 1)
                return True
            if key in _PLAIN_MOVES:
                self.clear_selection()

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
        bounds = self.selection_bounds() if self.text_selection else None
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
                if bounds is not None:
                    self._draw_selection(i, idx, width, bounds)

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
