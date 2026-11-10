"""List items: the rows a ListView renders.

An item reaches its view through `_view` (set when it is appended or made a
header) and the app through `get_app()`. The view calls
`draw_line(win, offset, width, selected, matched, marked)` with the cursor at
the row start; `offset` is the horizontal scroll, `width` the columns to fill.

Input: `handle_key(event)` / `handle_mouse(event)` return True when consumed.
The defaults run `activate()` on Enter / double-click and open the app's
context menu on a right-click.
"""

from __future__ import annotations

import curses
import typing

from tuikit import colors
from tuikit import keys as K
from tuikit import mouse as M


class Item:
    def __init__(self):
        self.is_selectable = True
        self.is_separator = False
        # Back-reference to the owning view, set when the item is added
        # (ListView.append / insert / set_header_item).
        self._view = None

    def get_app(self):
        """The app this item belongs to, reached through its view. None only
        for a transient item not yet added to a view."""
        return self._view.app if self._view is not None else None

    def get_text(self) -> str:
        return ""

    def natural_width(self) -> int:
        """Columns this row wants to show all of itself (what `pack()` sizes
        a window to)."""
        return len(self.get_text())

    def copy_text_to_clipboard(self):
        self.get_app().copy_to_clipboard(self.get_text())

    def set_text(self, txt: str):
        pass

    def draw_line(self, win, offset, width, selected, matched, marked):
        pass

    def activate(self) -> bool:
        """Default action on Enter / double-click. Override in subclasses."""
        return False

    def handle_key(self, event) -> bool:
        if event.key in K.ENTER_KEYS:
            return self.activate()
        return False

    def handle_mouse(self, event) -> bool:
        if event.type == M.DOUBLE_CLICK:
            return self.activate()
        if event.type == M.RIGHT_CLICK:
            return self.get_app().show_context_menu(self)
        return False


class SeparatorItem(Item):
    """A horizontal rule; joins onto the view's box border when it has one."""

    def __init__(self):
        super().__init__()
        self.is_selectable = False
        self.is_separator = True


class SpacerListItem(Item):
    """An empty, unselectable row."""

    def __init__(self):
        super().__init__()
        self.is_selectable = False

    def draw_line(self, win, offset, width, selected, matched, marked):
        win.clrtoeol()


class TextListItem(Item):
    """A row of text in one colour. `expand` paints the colour out to the full
    width even when not selected (a solid title band); `dim` dims it."""

    def __init__(
        self, txt, color=colors.NORMAL, expand=False, is_selectable=True, dim=False
    ):
        super().__init__()
        self.txt = txt
        self.color = color
        self.expand = expand
        self.is_selectable = is_selectable
        self.dim = dim

    def get_text(self):
        return self.txt

    def set_text(self, txt: str):
        self.txt = txt

    def color_spans(self) -> typing.List[typing.Tuple[str, int]]:
        """The text as (chunk, colour) runs; one run unless a subclass splits it."""
        return [(self.get_text(), self.color)]

    def draw_line(self, win, offset, width, selected, matched, marked):
        def attr(color):
            return colors.attr(color, selected, marked, matched, dim=self.dim)

        room = width
        for chunk, color in self.color_spans():
            if offset >= len(chunk):  # scrolled off to the left
                offset -= len(chunk)
                continue
            chunk = chunk[offset:][:room]
            offset = 0
            if chunk:
                win.addstr(chunk, attr(color))
                room -= len(chunk)
            if room <= 0:
                return
        if selected or marked or self.expand:
            win.addstr(" " * room, attr(self.color))
        else:
            win.clrtoeol()


class MenuItem(TextListItem):
    """A menu entry: closes its menu, then calls `action(*args)`. A disabled
    entry (`is_selectable=False`) is drawn dimmed and does nothing. Fires on a
    click, or on releasing a right-drag over it (press-drag-release menus)."""

    def __init__(self, text, action, args=(), is_selectable=True):
        super().__init__(text, is_selectable=is_selectable, dim=not is_selectable)
        self.action = action
        self.args = list(args) if args else []

    def activate(self) -> bool:
        if self.is_selectable:
            self._view.close()
            self.action(*self.args)
        return True

    def handle_mouse(self, event) -> bool:
        if event.type in (M.LEFT_CLICK, M.DOUBLE_CLICK, M.RIGHT_RELEASE):
            return self.activate()
        return super().handle_mouse(event)


class UserInputListItem(Item):
    """A one-line text field with a block cursor: editing keys, word motion
    (Ctrl+Left/Right), word delete (Ctrl+Backspace), clear (Ctrl+Del), click
    to place the cursor. Scrolls horizontally to keep the cursor visible."""

    def __init__(self, color=colors.NORMAL):
        super().__init__()
        self.txt = ""
        self.offset = 0
        self.cursor_pos = 0
        self.color = color

    def clear(self):
        self.txt = ""
        self.offset = 0
        self.cursor_pos = 0

    def get_text(self):
        return self.txt

    def natural_width(self) -> int:
        return len(self.txt) + 1  # + the cursor cell after the text

    def set_text(self, txt: str):
        self.txt = txt
        self.offset = 0
        self.cursor_pos = len(txt)

    def insert(self, txt: str):
        """Insert `txt` at the cursor and move the cursor past it."""
        pos = self.cursor_pos
        self.txt = self.txt[:pos] + txt + self.txt[pos:]
        self.cursor_pos = pos + len(txt)

    def prev_word_pos(self):
        pos = self.cursor_pos
        while pos > 0 and self.txt[pos - 1].isspace():
            pos -= 1
        while pos > 0 and not self.txt[pos - 1].isspace():
            pos -= 1
        return pos

    def next_word_pos(self):
        pos = self.cursor_pos
        length = len(self.txt)
        while pos < length and self.txt[pos].isspace():
            pos += 1
        while pos < length and not self.txt[pos].isspace():
            pos += 1
        return pos

    def handle_key(self, event):
        key = event.key
        if key in K.BACKSPACE_KEYS:
            if self.cursor_pos > 0:
                self.txt = self.txt[: self.cursor_pos - 1] + self.txt[self.cursor_pos :]
                self.cursor_pos -= 1
        elif key == K.KEY_CTRL_BACKSPACE:  # delete previous word
            start = self.prev_word_pos()
            self.txt = self.txt[:start] + self.txt[self.cursor_pos :]
            self.cursor_pos = start
        elif key == curses.KEY_DC:
            if self.cursor_pos < len(self.txt):
                self.txt = self.txt[: self.cursor_pos] + self.txt[self.cursor_pos + 1 :]
        elif key == K.KEY_CTRL_DEL:  # clear the whole field
            self.clear()
        elif key == curses.KEY_LEFT:
            if self.cursor_pos > 0:
                self.cursor_pos -= 1
        elif key == curses.KEY_RIGHT:
            if self.cursor_pos < len(self.txt):
                self.cursor_pos += 1
        elif key == K.KEY_CTRL_LEFT:
            self.cursor_pos = self.prev_word_pos()
        elif key == K.KEY_CTRL_RIGHT:
            self.cursor_pos = self.next_word_pos()
        elif key == curses.KEY_HOME:
            self.cursor_pos = 0
        elif key == curses.KEY_END:
            self.cursor_pos = len(self.txt)
        elif K.is_printable(key):
            self.insert(chr(key))
        else:
            return super().handle_key(event)
        return True

    def handle_mouse(self, event) -> bool:
        if event.is_left_press:
            # x is a column within the (possibly scrolled) field.
            self.cursor_pos = min(self.offset + event.x, len(self.txt))
            return True
        return super().handle_mouse(event)

    def draw_line(self, win, offset, width, selected, matched, marked):
        # Scroll so the cursor stays visible and nothing is written past
        # `width`: the cursor is a 1-col block between the text on its left and
        # right, so the text occupies `field` = width-1 columns.
        field = max(1, width - 1)
        if self.cursor_pos < self.offset:
            self.offset = self.cursor_pos
        elif self.cursor_pos - self.offset > field:
            self.offset = self.cursor_pos - field

        left_txt = self.txt[self.offset : self.cursor_pos]
        right_txt = self.txt[self.cursor_pos : self.offset + field]
        pad = max(0, width - len(left_txt) - len(right_txt) - 1)

        attr = colors.attr(self.color, selected, marked, matched)
        win.addstr(left_txt, attr)
        win.addch(ord(" "), curses.A_REVERSE | curses.A_BLINK)
        win.addstr(right_txt, attr)
        win.addstr(" " * pad, attr)
