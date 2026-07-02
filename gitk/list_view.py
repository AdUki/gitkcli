"""ListView: a scrollable, selectable list of items with search and copy.

Subclasses View. `_raise_split_sibling` lives here too — it keeps the two split
panes adjacent on the deck and is called from the concrete views' show().
"""

from __future__ import annotations

import curses
import re
import typing

from gitk.config import KEY_CTRL, copy_to_clipboard
from gitk.items import SpacerListItem, TextListItem
from gitk.screen import Screen
from gitk.view import SPLIT_DIVIDER_COLOR, View


class ListView(View):
    def __init__(
        self,
        app,
        id: str,
        view_mode: str = "fullscreen",
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
        self.autoscroll: bool = False
        self._search_dialog: typing.Optional[SearchDialogPopup] = None

    def set_search_dialog(self, search_dialog: "SearchDialogPopup"):
        self._search_dialog = search_dialog
        self._search_dialog.parent_list_view = self

    def _focus_button_row(self, focus="first"):
        """Make only self._button_row navigable (Left/Right pick a button, Enter
        activates it) and select it. focus='last' defaults to the final button -
        used for destructive confirmations so a bare Enter lands on [Cancel]."""
        for item in self.items:
            item.is_selectable = False
        self._button_row.is_selectable = True
        (
            self._button_row.focus_last
            if focus == "last"
            else self._button_row.reset_focus
        )()
        self._selected = len(self.items) - 1

    def _show_message_box(self, lines, button_row_item, focus="first"):
        """Lay out a content-sized popup and show it: a spacer, the message
        `lines` (each a str or (text, color) tuple, indented two spaces), a
        spacer, then the button row - the only navigable item. Sizes to the
        widest of the header, the lines and the button row, then centres."""
        self.clear()
        self.append(SpacerListItem())
        content = len(self.header_item.get_text())
        for line in lines:
            text, color = line if isinstance(line, tuple) else (line, 1)
            self.append(TextListItem("  " + text, color, is_selectable=False))
            content = max(content, len(text) + 2)  # + 2 for the left indent
        self.append(SpacerListItem())
        self._button_row = button_row_item
        self.append(button_row_item)
        content = max(content, len(button_row_item.get_text()))
        self._focus_button_row(focus)
        # content + 2 (right margin so text doesn't touch the border) + 2 (box sides)
        # fixed_x/y = None centres the popup on screen.
        self.set_dimensions(None, None, len(self.items) + 2, max(40, content + 4))
        self.show()

    def copy_text_to_clipboard(self):
        text = "\n".join(item.get_text() for item in self.items)
        if text:
            copy_to_clipboard(text, self.app)

    def copy_text_range_to_clipboard(self, to_item):
        lines = []
        found = False
        for i, item in enumerate(self.items):
            if not found and item == to_item:
                found = True
            if found or i >= self._selected:
                lines.append(item.get_text())
            if found and i >= self._selected:
                break
        # join (not repeated "\n" + ...) so the clipboard has no leading blank
        # line, matching copy_text_to_clipboard.
        copy_to_clipboard("\n".join(lines), self.app)

    def append(self, item):
        """Add item to end of list"""
        item._view = self
        self.items.append(item)
        # The new row is at index len-1, i.e. screen row (len-1) - offset_y, so
        # it is on-screen when (len - offset_y) <= height. Using '<' here left an
        # item landing exactly on the last visible row marked off-screen, so the
        # bottom row stayed blank (only a header redraw fired) until some later
        # full redraw — an intermittent blank bottom line as rows streamed in.
        if len(self.items) - self._offset_y <= self.height:
            self.dirty = True
        else:
            # The new row is off-screen, so the body need not be redrawn — but
            # the header's "[current/total]" counter changed, so request a cheap
            # header-only redraw to keep it current while items stream in.
            self.header_dirty = True
        if self.autoscroll:
            # Follow the tail. When the list overflows, this scrolls the view, so
            # the body must be redrawn — but the on-screen check above ran against
            # the OLD offset and would only have set header_dirty, leaving the
            # autoscrolled body stale. Mark dirty whenever the offset moves.
            new_offset = max(0, len(self.items) - self.height)
            if new_offset != self._offset_y:
                self._offset_y = new_offset
                self.dirty = True

    def clear(self):
        self.app.log.debug(f"Clear view {self.id}")
        self.items = []
        self.set_selected(0)
        self._offset_y = 0
        self._offset_x = 0
        self.dirty = True

    def set_selected(self, what: int | str | re.Pattern, visible_mode="center") -> bool:
        new_index = None

        if isinstance(what, int):
            if (0 <= what < len(self.items)) or (what <= 0 and len(self.items) == 0):
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

        if new_index is not None:
            if self._selected != new_index:
                # The target row is non-selectable: land on the nearest
                # selectable row, preferring the travel direction. Search both
                # passes from the ORIGINAL target and stop at the first hit, so
                # the fallback (opposite) pass can't clobber a travel-direction
                # match. Each pass stops at the current selection (never crosses
                # it). If neither finds one, leave selection unchanged.
                direction = 1 if new_index > self._selected else -1
                if (
                    0 <= new_index < len(self.items)
                    and not self.items[new_index].is_selectable
                ):
                    target = new_index
                    for step in [direction, -direction]:
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
                    # do not change view offset when item is already visible
                    return True

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

        return False

    def get_selected(self) -> typing.Any:
        if 0 <= self._selected < len(self.items):
            return self.items[self._selected]
        else:
            return None

    def search(self, backward: bool = False, repeat: bool = False):
        if not self._search_dialog:
            return

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
                    return

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == "wheel-up":
            self._offset_y = max(0, self._offset_y - 5)
            return True
        if mouse.event_type == "wheel-down":
            self._offset_y = min(
                self._offset_y + 5, max(0, len(self.items) - self.height)
            )
            return True

        if not self.resize_mode:
            view_x = mouse.x - self.x
            view_y = mouse.y - self.y
            index = self._offset_y + view_y

            if (
                0 <= view_y < self.height
                and 0 <= view_x < self.width
                and 0 <= index < len(self.items)
            ):
                selected = False
                if "move" in mouse.event_type:
                    if self._selected == index:
                        return False  # do not redraw when hovering over same item
                if (
                    mouse.event_type == "left-click"
                    or mouse.event_type == "double-click"
                    or (
                        "move" in mouse.event_type
                        and self in self.app.mouse.movement_capture
                    )
                ):
                    if self.items[index].is_selectable:
                        self.set_selected(index)
                        selected = True
                item = self.items[index]
                # hand the item its own coordinates, then restore the view-relative
                # ones so a fall-through to super() still sees the right position
                saved_x, saved_y = mouse.x, mouse.y
                mouse.x = view_x + self._offset_x
                mouse.y = index
                handled = item.handle_mouse_input(mouse)
                if handled and (
                    "left-click" == mouse.event_type
                    or "double-click" == mouse.event_type
                ):
                    self.app.mouse.clicked_item = item
                if selected or handled:
                    return True
                mouse.x, mouse.y = saved_x, saved_y

        return super().handle_mouse_input(mouse)

    def handle_input(self, keyboard):
        key = keyboard.key
        if not self.items:
            return super().handle_input(keyboard)

        selected_item = self.get_selected()
        if selected_item and selected_item.handle_input(keyboard):
            self.dirty = True
            return True

        if key == curses.KEY_UP or key == ord("k"):
            self.set_selected(self._selected - 1, visible_mode="top")
        elif key == curses.KEY_DOWN or key == ord("j"):
            self.set_selected(self._selected + 1, visible_mode="bottom")
        elif key == curses.KEY_LEFT or key == ord("h"):
            self._offset_x = max(0, self._offset_x - 1)
        elif key == curses.KEY_RIGHT or key == ord("l"):
            max_length = 0
            for i in range(
                self._offset_y, min(self._offset_y + self.height, len(self.items))
            ):
                length = len(self.items[i].get_text())
                if length > max_length:
                    max_length = length
            if self._offset_x + self.width < max_length:
                self._offset_x += 1
        elif key == curses.KEY_PPAGE or key == KEY_CTRL("b"):
            self._offset_y = max(0, self._offset_y - self.height)
            self.set_selected(max(0, self._selected - self.height))
        elif key == curses.KEY_NPAGE or key == KEY_CTRL("f"):
            self._offset_y = min(
                self._offset_y + self.height, max(0, len(self.items) - self.height)
            )
            self.set_selected(
                min(self._selected + self.height, max(0, len(self.items) - 1))
            )
        elif key == curses.KEY_HOME or key == ord("g"):
            self.set_selected(0)
        elif key == curses.KEY_END or key == ord("G"):
            self.set_selected(max(0, len(self.items) - 1))
        elif key == ord("/"):
            if self._search_dialog:
                self._search_dialog.clear()
                self._search_dialog.show()
        elif key == ord("n"):
            # repeat=True so 'next' wraps past the last match back to the first
            # (less/vim/gitk behaviour), instead of silently stopping at the end.
            self.search(repeat=True)
        elif key == ord("N"):
            self.search(backward=True, repeat=True)
        else:
            return super().handle_input(keyboard)

        return True

    def draw(self):
        separator_items = []
        for i in range(0, min(self.height, len(self.items) - self._offset_y)):
            idx = i + self._offset_y
            item = self.items[idx]
            selected = idx == self._selected
            matched = (
                self._search_dialog.matches(item) if self._search_dialog else False
            )

            # curses throws exception if you want to write a character in bottom left corner
            width = self.width
            if i == self.height - 1:
                width -= 1

            if item.is_separator:
                separator_items.append((i, width))
            else:
                self.win.move(self.y + i, self.x)
                item.draw_line(
                    self.win, self._offset_x, width, selected, matched, False
                )

        self.win.clrtobot()
        super().draw()

        if separator_items:
            color = 5 if self.is_active() else 16
            # Joins onto the neutral split divider use its colour, not the pane's.
            join = Screen.color(SPLIT_DIVIDER_COLOR)
            sides = self.split_border_sides()
            if sides is not None:
                # split pane: join only the borders that are actually drawn
                left = join if "left" in sides else None
                right = join if "right" in sides else None
            elif self.view_mode == "window":
                left = right = Screen.color(color)
            else:
                left = right = None
            for pair in separator_items:
                i, width = pair
                if left is not None:
                    self.win.move(self.y + i, self.x - 1)
                    self.win.addstr("├", left)
                else:
                    self.win.move(self.y + i, self.x)
                self.win.addstr("─" * width, Screen.color(color))
                if right is not None:
                    self.win.addstr("┤", right)


def _raise_split_sibling(view, sibling):
    """Keep both split panes adjacent on top of the stack with `view` focused.

    Focusing a pane (click, F1, F3, ...) goes through View.show(); in split view
    we first raise the sibling so the side-by-side / stacked layout is restored
    even after a fullscreen view (logs, refs) temporarily covered it.
    """
    if not view.app.split.split_active() or view.app.split._raising_split_sibling:
        return
    views = view.app.screen.showed_views
    if len(views) >= 2 and views[-1] is view and views[-2] is sibling:
        return  # already the top two in the right order
    view.app.split._raising_split_sibling = True
    try:
        sibling.show()
    finally:
        view.app.split._raising_split_sibling = False
