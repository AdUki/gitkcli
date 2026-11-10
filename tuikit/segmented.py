"""Rows composed of segments: `SegmentedListItem` and its library subclasses -
a keyboard-navigable button row, the window title bar, and a label + control
row for settings dialogs.
"""

from __future__ import annotations

import curses

from tuikit import colors
from tuikit import keys as K
from tuikit import mouse as M
from tuikit.items import Item
from tuikit.segments import ButtonSegment, FillerSegment, Segment, TextSegment


class SegmentedListItem(Item):
    """A row rendered as a sequence of segments separated by
    `segment_separator`. Mouse events go to the segment under the cursor (the
    pressed segment keeps the drag until release). Subclasses whose segments
    depend on live data override `get_segments()` and wire each new segment
    with `self.adopt(segments)`."""

    def __init__(self, segments=(), bg_color=colors.NORMAL):
        super().__init__()
        self.segment_separator = " "
        # Character for FillerSegments and the trailing fill: a space for an
        # ordinary row, '─' for the rule-line title bar.
        self.fill_char = " "
        # Keep the caller's list (not a copy): some rows extend it later.
        self.segments = self.adopt(
            segments if isinstance(segments, list) else list(segments)
        )
        self.bg_color = bg_color
        self.clicked_segment = None
        self.fill_width = 0

    def adopt(self, segments):
        """Wire `segments` back to this row (so they reach the app) and return
        them."""
        for segment in segments:
            segment._item = self
        return segments

    def set_segments(self, segments):
        self.segments = self.adopt(list(segments))

    def get_segments(self):
        return self.segments

    def get_text(self):
        return self.segment_separator.join(s.get_text() for s in self.get_segments())

    def get_row_context_menu(self):
        """The whole-row context menu (a right-click on a plain part of the
        row), as a (menu_item, view_id) pair, or None. Listed first when the
        keyboard cycles the row's menus."""
        return None

    def _segment_spans(self):
        """(segment, start column, length) for each segment, in row text space."""
        pos = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                length = self.fill_width
            else:
                length = len(segment.get_text())
            yield segment, pos, length
            pos += length + len(self.segment_separator)

    def get_context_menu_targets(self):
        """Ordered (menu_item, view_id, x) targets the keyboard cycles through on
        this row: the row's own menu first (x=0), then every segment that
        declares one, at the column where its text begins."""
        targets = []
        row_menu = self.get_row_context_menu()
        if row_menu is not None:
            targets.append((*row_menu, 0))
        for segment, pos, _ in self._segment_spans():
            menu = segment.get_context_menu()
            if menu is not None:
                targets.append((*menu, pos))
        return targets

    def get_segment_on_offset(self, offset) -> Segment:
        for segment, pos, length in self._segment_spans():
            if pos <= offset < pos + length:
                return segment
        return Segment()

    def _segment_start(self, segment):
        for seg, pos, _ in self._segment_spans():
            if seg is segment:
                return pos
        return 0

    def handle_mouse(self, event) -> bool:
        segment = self.clicked_segment or self.get_segment_on_offset(event.x)
        if event.is_left_press:
            self.clicked_segment = segment
        elif self.clicked_segment:
            if event.is_release:
                self.clicked_segment = None
            if (
                event.type == M.LEFT_MOVE_IN
                and self.clicked_segment is not self.get_segment_on_offset(event.x)
            ):
                # still on the row, but dragged off the pressed segment
                event = event.with_type(M.LEFT_MOVE_OUT)
        # The segment sees the event in its own columns.
        local = event.at(event.x - self._segment_start(segment), event.y)
        if segment and segment.handle_mouse(local):
            return True
        return super().handle_mouse(event)

    def get_fill_txt(self, width):
        fillers_count = sum(isinstance(s, FillerSegment) for s in self.get_segments())
        if fillers_count:
            # Clamp to 0: a negative width (content wider than the window) would
            # otherwise rewind the span walk and misroute clicks on the
            # segments after the filler.
            self.fill_width = max(
                0, int((width - len(self.get_text())) / fillers_count)
            )
            return self.fill_width * self.fill_char
        return ""

    def _segment_selected(self, index, selected):
        """Per-segment highlight flag. Base: the whole row follows selection."""
        return selected

    def _bg_selected(self, selected):
        """Highlight flag for separators/fillers/trailing fill (the row background)."""
        return selected

    def draw_line(self, win, offset, width, selected, matched, marked):
        remaining_width = width
        bg_selected = self._bg_selected(selected)
        bg_attr = colors.attr(self.bg_color, bg_selected, marked, matched)
        sep = self.segment_separator
        prev_visible = False  # did the previous segment render any columns?
        for index, segment in enumerate(self.get_segments()):
            if index > 0 and sep:
                # The separator occupies columns in get_text() space (which
                # `offset` is measured in), so it takes part in the scroll walk:
                # consumed from `offset` while scrolled off the left, otherwise
                # drawn - but only after a segment that showed text.
                if offset >= len(sep):
                    offset -= len(sep)
                else:
                    visible_sep = sep[offset:]
                    offset = 0
                    if prev_visible:
                        remaining_width -= len(visible_sep)
                        win.addstr(visible_sep, bg_attr)
            if isinstance(segment, FillerSegment):
                txt = self.get_fill_txt(width)
                win.addstr(txt, bg_attr)
                length = len(txt)
            else:
                length = segment.draw(
                    win,
                    offset,
                    remaining_width,
                    self._segment_selected(index, selected),
                    matched,
                    marked,
                )
                txt = segment.get_text()
            prev_visible = length > 0
            remaining_width -= length
            if remaining_width <= 0:
                break
            offset -= len(txt) - length

        if remaining_width > 0:
            if bg_selected or marked:
                win.addstr(" " * remaining_width, bg_attr)
            elif self.fill_char != " ":
                # rule-line bar: trail the title with its fill character ('─')
                win.addstr(self.fill_char * remaining_width, bg_attr)
            else:
                win.clrtoeol()


class ButtonRowItem(SegmentedListItem):
    """A row of buttons navigable with Left/Right (h/l, Tab/Shift+Tab); Enter
    activates the focused button. Only the focused button is highlighted."""

    def __init__(self, segments=(), bg_color=colors.NORMAL):
        super().__init__(segments, bg_color)
        self.is_selectable = True
        self.reset_focus()

    def _button_indices(self):
        return [i for i, s in enumerate(self.segments) if hasattr(s, "activate")]

    def _focus_button(self, pos):
        indices = self._button_indices()
        self.focused = indices[pos] if indices else 0

    def reset_focus(self):
        """Focus the first (primary) button. Reused dialogs call this on open
        so focus does not linger on what was picked last time."""
        self._focus_button(0)

    def focus_last(self):
        """Focus the last button - destructive confirmations default here so a
        stray Enter hits the safe (cancel) one."""
        self._focus_button(-1)

    def _move_focus(self, direction):
        indices = self._button_indices()
        if not indices:
            return
        pos = indices.index(self.focused) if self.focused in indices else 0
        self.focused = indices[(pos + direction) % len(indices)]

    def handle_key(self, event):
        key = event.key
        if key in (curses.KEY_LEFT, ord("h"), curses.KEY_BTAB):
            self._move_focus(-1)
            return True
        if key in (curses.KEY_RIGHT, ord("l"), K.KEY_TAB):
            self._move_focus(1)
            return True
        if key in K.ENTER_KEYS:
            if 0 <= self.focused < len(self.segments) and hasattr(
                self.segments[self.focused], "activate"
            ):
                self.segments[self.focused].activate()
            return True
        return False

    def _segment_selected(self, index, selected):
        return selected and index == self.focused

    def _bg_selected(self, selected):
        return False  # never band the whole row


def button_row(*buttons):
    """A centred, keyboard-navigable row of buttons (fillers pad both ends)."""
    return ButtonRowItem([FillerSegment(), *buttons, FillerSegment()])


class WindowTopBarItem(SegmentedListItem):
    """A main view's title bar, drawn as a rule line with the title and buttons
    inset: ``─ Title [3/120] ────── [buttons] [X]─``. Borderless views show it
    bare; a floated window draws it between its box corners. The active view's
    line is blue with white text, an inactive one dim grey. [X] closes the
    view, a double-click on the bar toggles fullscreen/window. The bar shows
    a live ``[current/total]`` counter when its view is a list."""

    LINE_ACTIVE, LINE_INACTIVE = colors.ACCENT, colors.DIM
    TEXT_ACTIVE, TEXT_INACTIVE = colors.NORMAL, colors.DIM
    CLOSE_COLOR = colors.ERROR  # red [X], active or not

    def __init__(self, title: str, additional_segments=(), title_color=None):
        # title_color overrides the title's text colour when active.
        self._base_title = title
        self.title_segment = TextSegment(title, self.TEXT_ACTIVE)
        self._title_color = title_color
        self._leading = TextSegment("─", self.LINE_ACTIVE)
        self._close_segment = ButtonSegment("[X]", self._close, self.TEXT_ACTIVE)
        segments = [self._leading, self.title_segment, FillerSegment()]
        segments.extend(additional_segments)
        segments.append(self._close_segment)
        super().__init__(segments, self.LINE_INACTIVE)
        self.fill_char = "─"

    def _close(self):
        self._view.close()
        return True

    def set_title(self, txt: str):
        self._base_title = txt
        self.title_segment.set_text(txt)

    def set_counter(self, current: int, total: int):
        self.title_segment.set_text(f"{self._base_title} [{current}/{total}]")

    def get_fill_txt(self, width):
        # Reserve one trailing column so the rule always ends "…[X]─".
        return super().get_fill_txt(width - 1)

    def draw_line(self, win, offset, width, selected, matched, marked):
        # `selected` is the view's active state (passed by View.draw_header).
        # Recolour the segments for it, then draw with selected=False so the
        # bar never gets a highlight band - it must read as a single line.
        active = selected
        line_color = self.LINE_ACTIVE if active else self.LINE_INACTIVE
        text_color = self.TEXT_ACTIVE if active else self.TEXT_INACTIVE
        self.bg_color = line_color
        for seg in self.segments:
            if seg is self._leading:
                seg.color = line_color
            elif active and seg is self.title_segment and self._title_color is not None:
                seg.color = self._title_color
            elif seg is self._close_segment:
                seg.color = self.CLOSE_COLOR
            else:
                seg.color = text_color
        super().draw_line(win, offset, width, False, matched, marked)

    def handle_mouse(self, event) -> bool:
        if super().handle_mouse(event):
            return True
        if event.type == M.DOUBLE_CLICK:
            self._view.toggle_window_mode()
            return True
        return False


class LabeledRow(SegmentedListItem):
    """`  label ........ control  ` for settings dialogs; Enter (or a
    double-click) activates the control (a toggle, choice, radio group,
    button...) and the control gets the row's other keys, so Left/Right
    change a switch, choice or radio group."""

    def __init__(self, label, control):
        super().__init__(
            [TextSegment(f"  {label}  "), FillerSegment(), control, TextSegment("  ")]
        )
        self.control = control

    def activate(self) -> bool:
        self.control.activate()
        return True

    def handle_key(self, event) -> bool:
        return self.control.handle_key(event) or super().handle_key(event)
