"""Popup dialogs built on ListView.

- `Dialog`: a modal popup window with a title in its top border and, usually,
  a row of buttons (`set_button_row`); `show_message()` lays out a content-sized
  message box.
- `ConfirmDialog` / `ErrorDialog`: red message boxes (yes/cancel, ok).
- `InputDialog`: a header row, a one-line text field with history (Up/Down),
  and buttons; Enter runs `execute()`.
- `SearchDialog`: an InputDialog that a ListView searches with (case /
  regexp toggles on F1 / F2); attach it with `ListView.set_search_dialog`.
- `Menu`: a popup list of MenuItems placed at a screen position, tracking the
  hovered row while open (press-drag-release works with the right button).

Dialogs are meant to be built once and reused: reset per-open state in
`on_activated()` or in the method that opens them.
"""

from __future__ import annotations

import curses
import re
import typing

from tuikit import colors
from tuikit import keys as K
from tuikit.items import SpacerListItem, TextListItem, UserInputListItem
from tuikit.list_view import ListView
from tuikit.segmented import SegmentedListItem, button_row
from tuikit.segments import ButtonSegment, FillerSegment, TextSegment, ToggleSegment
from tuikit.view import MODE_WINDOW

MIN_MESSAGE_WIDTH = 40


class Dialog(ListView):
    """A modal popup window. `title` is drawn inset in the top border."""

    def __init__(self, app, id, title="", height=None, width=None, title_color=None):
        super().__init__(app, id, MODE_WINDOW, height=height, width=width)
        self.is_popup = True
        self.set_header_item(
            TextListItem(
                title, colors.TITLE if title_color is None else title_color, expand=True
            )
        )
        self._button_row = None

    def set_title(self, title):
        self.header_item.set_text(title)

    def set_button_row(self, row):
        """Append `row` (see button_row()) as the dialog's buttons."""
        self._button_row = row
        self.append(row)
        return row

    def focus_button_row(self, focus="first"):
        """Make the button row the only navigable item and select it.
        focus='last' defaults to the final button - for destructive
        confirmations, so a bare Enter lands on [Cancel]."""
        for item in self.items:
            item.is_selectable = False
        self._button_row.is_selectable = True
        if focus == "last":
            self._button_row.focus_last()
        else:
            self._button_row.reset_focus()
        self._selected = len(self.items) - 1

    def show_message(self, lines, buttons, focus="first"):
        """Lay out a content-sized message box and show it: a spacer, `lines`
        (each a str or a (text, color) tuple, indented two spaces), a spacer,
        then `buttons` (a button row) - the only navigable item. Sized to the
        widest of the title, the lines and the buttons, and centred."""
        self.clear()
        self.append(SpacerListItem())
        content = len(self.header_item.get_text())
        for line in lines:
            text, color = line if isinstance(line, tuple) else (line, colors.NORMAL)
            self.append(TextListItem("  " + text, color, is_selectable=False))
            content = max(content, len(text) + 2)  # + 2 for the left indent
        self.append(SpacerListItem())
        self.set_button_row(buttons)
        content = max(content, len(buttons.get_text()))
        self.focus_button_row(focus)
        # + 2 right margin so text doesn't touch the border, + 2 box sides;
        # x/y None centres the popup.
        self.set_dimensions(
            None, None, len(self.items) + 2, max(MIN_MESSAGE_WIDTH, content + 4)
        )
        self.show()


class MessageBox(Dialog):
    """A red message box: red border and title, for warnings and errors."""

    def __init__(self, app, id, title=""):
        super().__init__(app, id, title, title_color=colors.BANNER)

    def border_color(self):
        return colors.attr(colors.ERROR)


class ConfirmDialog(MessageBox):
    """Yes/cancel popup. y confirms, n/q/Esc cancels; focus defaults to
    [Cancel] (these are usually destructive confirmations)."""

    def __init__(self, app, id="confirm"):
        super().__init__(app, id)
        self._on_confirm = lambda: None

    def confirm(
        self, title, lines, on_confirm, confirm_label="[Yes]", cancel_label="[Cancel]"
    ):
        self._on_confirm = on_confirm
        self.set_title(title)
        self.show_message(
            lines,
            button_row(
                ButtonSegment(confirm_label, self._confirm, colors.ERROR),
                TextSegment("   "),
                ButtonSegment(cancel_label, self.hide),
            ),
            focus="last",
        )

    def _confirm(self):
        self.hide()
        self._on_confirm()
        return True

    def handle_key(self, event):
        key = event.key
        if key in (ord("y"), ord("Y")):
            self._confirm()
        elif key in (curses.KEY_EXIT, ord("n"), ord("N"), ord("q")):
            self.hide()
        else:
            # Left/Right/Tab move between the buttons; Enter activates one.
            super().handle_key(event)
        # Modal: swallow every other key, so global shortcuts cannot bury this
        # popup behind another view while its callback is still armed.
        return True


class ErrorDialog(MessageBox):
    """Red alert with a single [Ok]. Errors that arrive while it is open are
    coalesced into it (each distinct line once) instead of stacking popups."""

    MAX_LINES = 12

    def __init__(self, app, id="error", title=" Error"):
        super().__init__(app, id, title)
        self._lines = []

    def show_error(self, message):
        incoming = [line for line in message.splitlines() if line.strip()] or [message]
        if not self.is_active():
            self._lines = []
        for line in incoming:
            if line not in self._lines and len(self._lines) < self.MAX_LINES:
                self._lines.append(line)
        self.show_message(
            [(line, colors.ERROR) for line in self._lines],
            button_row(ButtonSegment("[Ok]", self.hide, colors.ERROR)),
        )

    def handle_key(self, event):
        if event.key in K.ENTER_KEYS or event.key in (
            curses.KEY_EXIT,
            ord("o"),
            ord("O"),
            ord("q"),
        ):
            self.hide()
        else:
            super().handle_key(event)
        return True  # modal


class InputDialog(Dialog):
    """A compact three-row prompt: `header_item` (a label / flags row), the
    text field, and `bottom_item` (default: [Execute] [Cancel]). Enter hides
    the dialog and calls `execute()`; Esc cancels; Up/Down (Ctrl+p/n) walk
    the history of executed inputs."""

    def __init__(
        self,
        app,
        id: str,
        title: str,
        header_item,
        bottom_item=None,
        width=60,
    ):
        super().__init__(app, id, title, height=5, width=width)
        self.input = UserInputListItem()
        self.history_queries = []
        self.history_index = -1  # -1 == the live (not-from-history) input
        self.pending_input = ""  # live input stashed while browsing history

        if not bottom_item:
            bottom_item = SegmentedListItem(
                [
                    FillerSegment(),
                    ButtonSegment(
                        "[Execute]",
                        lambda: self.handle_key(K.KeyEvent(curses.KEY_ENTER)),
                    ),
                    ButtonSegment(
                        "[Cancel]",
                        lambda: self.handle_key(K.KeyEvent(curses.KEY_EXIT)),
                    ),
                    FillerSegment(),
                ]
            )
            bottom_item.is_selectable = False

        header_item.is_selectable = False
        self.append(header_item)
        self.append(self.input)
        self.append(bottom_item)
        self._selected = 1

    def add_query_to_history(self):
        if self.input.txt and (
            not self.history_queries or self.history_queries[0] != self.input.txt
        ):
            self.history_queries.insert(0, self.input.txt)

    def execute(self):
        self.add_query_to_history()

    def clear(self):
        self.input.clear()
        self.history_index = -1
        self.pending_input = ""

    def handle_key(self, event):
        key = event.key
        if key in K.ENTER_KEYS:
            self.hide()
            self.execute()
        elif key == curses.KEY_EXIT:
            self.hide()
        elif key in (curses.KEY_DOWN, K.ctrl("n")):
            # Toward newer entries; below index 0 is the live input again.
            if self.history_index >= 0:
                self.history_index -= 1
                self.input.set_text(
                    self.pending_input
                    if self.history_index < 0
                    else self.history_queries[self.history_index]
                )
        elif key in (curses.KEY_UP, K.ctrl("p"), K.ctrl("o")):
            if self.history_index + 1 < len(self.history_queries):
                if self.history_index < 0:
                    self.pending_input = self.input.txt  # so Down can restore it
                self.history_index += 1
                self.input.set_text(self.history_queries[self.history_index])
        else:
            return super().handle_key(event)
        return True


class SearchDialog(InputDialog):
    """Search prompt for a ListView: Enter jumps to the next match, the
    buttons step next/previous; F1 toggles <Case>, F2 <Regexp>. Subclasses can
    prepend their own segments to `self.header.segments` and override
    `matches()`."""

    def __init__(self, app, id: str, width=60):
        self.parent_list_view: ListView = None
        self.case_sensitive = ToggleSegment("<Case>", True)
        self.use_regexp = ToggleSegment("<Regexp>")
        # One leading filler right-aligns the "Flags:" group.
        self.header = SegmentedListItem(
            [
                FillerSegment(),
                TextSegment("Flags:"),
                self.case_sensitive,
                self.use_regexp,
            ]
        )
        buttons = SegmentedListItem(
            [
                FillerSegment(),
                ButtonSegment("[Search Next]", lambda: self.do_search(backward=False)),
                ButtonSegment(
                    "[Search Previous]", lambda: self.do_search(backward=True)
                ),
                ButtonSegment("[Clear]", self.clear_input),
                FillerSegment(),
            ]
        )
        buttons.is_selectable = False
        super().__init__(app, id, " Search", self.header, buttons, width=width)

    def clear_input(self):
        self.clear()
        self.dirty = True
        self.parent_list_view.dirty = True

    def do_search(self, backward: bool):
        self.parent_list_view.search(backward, repeat=True)
        self.dirty = True
        super().execute()

    def matches(self, item):
        if not self.input.txt:
            return False
        text = item.get_text()
        if self.use_regexp.toggled:
            # Runs on every redraw and keystroke: a half-typed / invalid pattern
            # must not raise - it simply matches nothing until it is valid.
            try:
                return re.search(
                    self.input.txt,
                    text,
                    0 if self.case_sensitive.toggled else re.IGNORECASE,
                )
            except re.error:
                return None
        if self.case_sensitive.toggled:
            return self.input.txt in text
        return self.input.txt.lower() in text.lower()

    def handle_key(self, event):
        key = event.key
        if key == curses.KEY_DC or key in K.BACKSPACE_KEYS or K.is_printable(key):
            self.parent_list_view.dirty = True  # re-highlight the matches

        if key == curses.KEY_F1:
            self.case_sensitive.toggle()
        elif key == curses.KEY_F2:
            self.use_regexp.toggle()
        else:
            return super().handle_key(event)
        return True

    def execute(self):
        self.parent_list_view.search(repeat=True)
        super().execute()


class Menu(ListView):
    """A popup menu of MenuItems (and SeparatorItems). Fill it, then
    `open_at(x, y)`; while open it selects the row under the pointer."""

    def __init__(self, app, id="menu", width=30):
        super().__init__(app, id, MODE_WINDOW)
        self.is_popup = True
        self.menu_width = width

    def on_activated(self):
        super().on_activated()
        self.app.mouse.capture(self)

    def on_deactivated(self):
        super().on_deactivated()
        self.app.mouse.release(self)

    def open_at(self, x: int, y: int):
        """Size the menu to its items and show it with its corner at (x, y)
        (pulled back on-screen if it would overflow)."""
        self.set_dimensions(x, y, len(self.items) + 2, self.menu_width)
        self.show()

    def popup(self, entries: typing.Iterable, x: int, y: int):
        """Replace the contents with `entries` and open at (x, y), with no row
        selected."""
        self.clear()
        self._selected = -1
        for entry in entries:
            self.append(entry)
        self.open_at(x, y)
