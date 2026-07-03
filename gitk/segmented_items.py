"""Composite list items built from segments (the SegmentedListItem family).

SegmentedListItem renders a row as a sequence of segments; subclasses are the
button row, the window top bar, commit / uncommitted-changes rows, and the
preferences row. They subclass Item (from gitk.items) and reach the app via
get_app() like any item.
"""

from __future__ import annotations

import curses

from gitk.diff_target import (
    LOCAL_STAGED_ID,
    LOCAL_WORKING_ID,
    STAGED_TITLE,
    WORKING_TITLE,
)
from gitk.ids import ID_GIT_LOG
from gitk.input import ENTER_KEYS
from gitk.items import Item
from gitk.screen import Screen
from gitk.segments import ButtonSegment, FillerSegment, RefSegment, Segment, TextSegment


class SegmentedListItem(Item):
    def __init__(self, segments=[], bg_color=Screen.C_NORMAL):
        super().__init__()
        self.segment_separator = " "
        # Character used for the FillerSegment and the trailing fill. Defaults to
        # a space (an ordinary row); the rule-line title bar overrides it to '─'.
        self.fill_char = " "
        self.segments = segments
        # Wire each segment back to this item so segments can reach the App
        # struct (segment -> item -> view -> app) via get_app().
        for segment in self.segments:
            segment._item = self
        self.bg_color = bg_color
        self.clicked_segment = None

    def get_segments(self):
        return self.segments

    def get_text(self):
        return self.segment_separator.join(s.get_text() for s in self.get_segments())

    def get_row_context_menu(self):
        """The whole-row context menu (what a right-click on a plain part of the
        row opens), as a (menu_item, view_id) pair, or None. Listed first when
        F7 cycles the row's menus."""
        return None

    def get_context_menu_targets(self):
        """Ordered (menu_item, view_id, x) targets F7 cycles through on this row:
        the row's own menu first (x=0), then every segment that declares one,
        each at the column where its text begins (mirrors get_segment_on_offset
        so the menu pops up under that segment)."""
        targets = []
        row_menu = self.get_row_context_menu()
        if row_menu is not None:
            targets.append((*row_menu, 0))
        segment_pos = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                length = getattr(self, "fill_width", 0)
            else:
                length = len(segment.get_text())
            menu = segment.get_context_menu()
            if menu is not None:
                targets.append((*menu, segment_pos))
            segment_pos += length + len(self.segment_separator)
        return targets

    def get_segment_on_offset(self, offset) -> Segment:
        segment_pos = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                length = self.fill_width
            else:
                length = len(segment.get_text())
            if segment_pos <= offset < segment_pos + length:
                return segment
            segment_pos += length + len(self.segment_separator)
        return Segment()

    def handle_mouse_input(self, mouse) -> bool:
        segment = self.clicked_segment or self.get_segment_on_offset(mouse.x)
        if mouse.event_type == "left-click" or mouse.event_type == "double-click":
            self.clicked_segment = segment
        elif self.clicked_segment:
            if "release" in mouse.event_type:
                self.clicked_segment = None
            if (
                "move-in" in mouse.event_type
                and self.clicked_segment != self.get_segment_on_offset(mouse.x)
            ):
                mouse.event_type = mouse.event_type.replace("in", "out")
        if segment and segment.handle_mouse_input(mouse):
            return True
        return super().handle_mouse_input(mouse)

    def get_fill_txt(self, width):
        fillers_count = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                fillers_count += 1
        if fillers_count:
            # Clamp to 0: a negative width (content wider than the window) would
            # otherwise rewind segment_pos in get_segment_on_offset and misroute
            # clicks on the buttons that follow the filler.
            self.fill_width = max(
                0, int((width - len(self.get_text())) / fillers_count)
            )
            return self.fill_width * self.fill_char
        return ""

    def _segment_selected(self, index, selected):
        # Per-segment highlight flag. Base: whole row follows the row selection.
        return selected

    def _bg_selected(self, selected):
        # Highlight flag for separators/fillers/trailing fill (the row background).
        return selected

    def draw_line(self, win, offset, width, selected, matched, marked):
        remaining_width = width
        bg_selected = self._bg_selected(selected)
        sep = self.segment_separator
        prev_visible = False  # did the previous segment render any columns?
        for index, segment in enumerate(self.get_segments()):
            if index > 0 and sep:
                # The inter-segment separator occupies a column in get_text()
                # space (which `offset`/_offset_x are measured against), so it
                # must participate in the horizontal-scroll walk: consume it from
                # `offset` when it's scrolled off the left, otherwise draw it (but
                # only after a segment that showed text — no separator after an
                # empty/zero-width one). At offset 0 this reduces to the original
                # "draw a separator before each segment that follows a visible one".
                if offset >= len(sep):
                    offset -= len(sep)
                else:
                    visible_sep = sep[offset:]
                    offset = 0
                    if prev_visible:
                        remaining_width -= len(visible_sep)
                        win.addstr(
                            visible_sep,
                            Screen.color(self.bg_color, bg_selected, marked, matched),
                        )
            if isinstance(segment, FillerSegment):
                txt = self.get_fill_txt(width)
                win.addstr(
                    txt, Screen.color(self.bg_color, bg_selected, marked, matched)
                )
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
                win.addstr(
                    " " * remaining_width,
                    Screen.color(self.bg_color, bg_selected, marked, matched),
                )
            elif self.fill_char != " ":
                # rule-line bar: trail the title with its fill character ('─')
                win.addstr(
                    self.fill_char * remaining_width,
                    Screen.color(self.bg_color, bg_selected, marked, matched),
                )
            else:
                win.clrtoeol()


class ButtonRowItem(SegmentedListItem):
    """A row of buttons navigable with Left/Right (or h/l); Enter activates the
    focused button. Only the focused button is highlighted, not the whole row."""

    def __init__(self, segments=[], bg_color=Screen.C_NORMAL):
        super().__init__(segments, bg_color)
        self.is_selectable = True
        self.reset_focus()

    def _button_indices(self):
        return [i for i, s in enumerate(self.segments) if hasattr(s, "activate")]

    def _focus_button(self, pos):
        indices = self._button_indices()
        self.focused = indices[pos] if indices else 0

    def reset_focus(self):
        # Back to the default (first/primary) button. Reused dialogs call this on
        # open so focus doesn't linger on whatever was picked last time.
        self._focus_button(0)

    def focus_last(self):
        # Focus the last (rightmost) button. Destructive confirm dialogs default
        # here so a stray Enter hits the safe (cancel) button.
        self._focus_button(-1)

    def _move_focus(self, direction):
        indices = self._button_indices()
        if not indices:
            return
        pos = indices.index(self.focused) if self.focused in indices else 0
        self.focused = indices[(pos + direction) % len(indices)]

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_LEFT or key == ord("h"):
            self._move_focus(-1)
            return True
        if key == curses.KEY_RIGHT or key == ord("l"):
            self._move_focus(1)
            return True
        if key in ENTER_KEYS:
            if 0 <= self.focused < len(self.segments) and hasattr(
                self.segments[self.focused], "activate"
            ):
                self.segments[self.focused].activate()
            return True
        return False

    def _segment_selected(self, index, selected):
        return selected and index == self.focused

    def _bg_selected(self, selected):
        # Never band the whole row; only the focused button is highlighted.
        return False


def button_row(*buttons):
    """A centered, keyboard-navigable row of buttons (fillers pad both ends)."""
    return ButtonRowItem([FillerSegment(), *buttons, FillerSegment()])


class WindowTopBarItem(SegmentedListItem):
    """Top title bar of a main view, rendered as a horizontal rule line with the
    title and buttons inset: ``─ Title ─────── [buttons] [X]``. Fullscreen and
    split-pane views show it with no surrounding box; a floated window draws it
    between its box's top corners (``┌─ Title …[X]─┐``). Focus is shown by
    colouring the active view's line (blue line + white text) versus a dim grey
    line when inactive."""

    # Line and text colours, by active state. The ── fill uses the line colour;
    # the title and buttons use the text colour. Selected/highlight backgrounds
    # are intentionally bypassed (see draw_line) so the bar stays a thin line.
    LINE_ACTIVE, LINE_INACTIVE = Screen.C_DATA, Screen.C_DIM
    TEXT_ACTIVE, TEXT_INACTIVE = Screen.C_NORMAL, Screen.C_DIM
    CLOSE_COLOR = (
        Screen.C_ERROR
    )  # red [X] close button, in both active and inactive bars

    def __init__(self, title: str, additional_segments=[], title_color=None):
        # title_color overrides the title's text colour when active. The bar
        # shows a live "[current/total]" line counter after the title, updated
        # generically by View.draw_header from the owning view's state.
        self._base_title = title
        self.title_segment = TextSegment(title, self.TEXT_ACTIVE)
        self._title_color = title_color
        self._leading = TextSegment("─", self.LINE_ACTIVE)
        self._close_segment = ButtonSegment(
            "[X]", lambda: self.get_app().screen.hide_active_view(), self.TEXT_ACTIVE
        )
        segments = [self._leading, self.title_segment, FillerSegment()]
        segments.extend(additional_segments)
        segments.append(self._close_segment)
        super().__init__(segments, self.LINE_INACTIVE)
        self.fill_char = "─"

    def set_title(self, txt: str):
        self._base_title = txt
        self.title_segment.set_text(txt)

    def set_counter(self, current: int, total: int):
        self.title_segment.set_text(f"{self._base_title} [{current}/{total}]")

    def get_fill_txt(self, width):
        # Reserve one trailing column so the rule always ends "…[X]─": a dash
        # sits between the last button and the right edge / box corner / split
        # divider, instead of the button being flush against it.
        return super().get_fill_txt(width - 1)

    def draw_line(self, win, offset, width, selected, matched, marked):
        # `selected` is the view's active state (passed by View.draw). Recolour
        # every segment for that state, then draw with selected=False so the
        # row never gets a highlight band — it must read as a single line.
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

    def handle_mouse_input(self, mouse) -> bool:
        if super().handle_mouse_input(mouse):
            return True
        if mouse.event_type == "double-click":
            self.get_app().screen.get_active_view().toggle_window_mode()
            return True
        return False


class UncommittedChangesListItem(SegmentedListItem):
    def __init__(self, staged: bool = False):
        super().__init__()
        self._staged = staged
        self.id = LOCAL_STAGED_ID if staged else LOCAL_WORKING_ID
        # Graph art mirrored from the HEAD row when in --graph mode; set by
        # GitLogView._place_uncommitted_rows, empty otherwise.
        self.graph_prefix = ""
        if self._staged:
            self.txt, self.color = STAGED_TITLE, Screen.C_STATUS
        else:
            self.txt, self.color = WORKING_TITLE, Screen.C_ERROR

    def get_row_context_menu(self):
        return (self, ID_GIT_LOG)

    def get_segments(self):
        segments = []
        if self.graph_prefix:
            segments.append(TextSegment(self.graph_prefix))
        segments.append(TextSegment(self.txt, self.color))
        return segments

    def load_to_view(self):
        diff = self.get_app().git_diff
        if diff.shows(self.id):
            return
        diff.show_worktree(self._staged, add_to_jump_list=True)

    def activate(self) -> bool:
        self.load_to_view()
        self.get_app().git_diff.show()
        return True


class CommitListItem(SegmentedListItem):
    def __init__(self, id: str):
        super().__init__()
        self.id = id

    def get_row_context_menu(self):
        return (self, ID_GIT_LOG)

    def get_segments(self):
        app = self.get_app()
        commit = app.git_log.commits.get(self.id)
        if commit is None:
            # A stale row can outlive the reload that discarded its commit data
            # (a queued mouse event, a jump-list hop): degrade to the bare id
            # instead of raising KeyError from whatever touches the row next.
            segments = [TextSegment(self.id[:7], Screen.C_GIT_ID)]
            for segment in segments:
                segment._item = self
            return segments
        segments = []

        if commit["prefix"]:
            segments.append(TextSegment(commit["prefix"]))
        if app.git_log.show_commit_id:
            segments.append(TextSegment(self.id[:7], Screen.C_GIT_ID))
        if app.git_log.show_commit_date:
            segments.append(
                TextSegment(commit["date"].strftime("%Y-%m-%d %H:%M"), Screen.C_DATA)
            )
        if app.git_log.show_commit_author:
            segments.append(TextSegment(commit["author"], Screen.C_AUTHOR))
        segments.append(TextSegment(commit["title"]))

        head_position = (
            len(segments) + 1
        )  # +1, because we want to skip 'HEAD ->' segment
        for ref in app.git_refs.refs.get(self.id, []):
            segments.insert(
                head_position
                if ref["name"] == app.git_log.head_branch
                else len(segments),
                RefSegment(ref, app.git_log.head_branch),
            )

        # These segments are rebuilt each call (not the wired self.segments), so
        # back-wire them so they can reach the app via get_app() too.
        for segment in segments:
            segment._item = self
        return segments

    def draw_line(self, win, offset, width, selected, matched, marked):
        super().draw_line(
            win,
            offset,
            width,
            selected,
            matched,
            self.get_app().git_log.marked_commit_id == self.id,
        )

    def load_to_view(self):
        diff = self.get_app().git_diff
        if not diff.shows(self.id):
            diff.show_commit(self.id)

    def activate(self) -> bool:
        self.load_to_view()
        self.get_app().git_diff.show()
        return True


class PreferenceRow(SegmentedListItem):
    """A label + interactive control (toggle/choice). Enter activates the control."""

    def __init__(self, label, control):
        super().__init__(
            [TextSegment(f"  {label}  "), FillerSegment(), control, TextSegment("  ")]
        )
        self.control = control

    def activate(self) -> bool:
        self.control.activate()
        return True
