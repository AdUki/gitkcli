"""List items: the rows a ListView renders.

Covers plain items (separators, text, refs, diff/stat rows, context-menu and
user-input rows) and the SegmentedListItem family (button rows, the window top
bar, commit / uncommitted-changes rows, preference rows). Items reach the App
struct through their owning view (`get_app()`); they depend on segments, the
colour palette (Screen), the clipboard helper, and a few input constants.
"""

from __future__ import annotations

import curses
import re
import time
import typing

from gitk.config import copy_to_clipboard
from gitk.input import (ENTER_KEYS, KEY_CTRL_BACKSPACE, KEY_CTRL_DEL,
                        KEY_CTRL_LEFT, KEY_CTRL_RIGHT)
from gitk.screen import Screen
from gitk.segments import (ref_color_and_title, Segment, FillerSegment,
                           TextSegment, RefSegment, ButtonSegment)

class Item:
    def __init__(self):
        self.is_selectable = True
        self.is_separator = False
        # Back-reference to the owning ListView, set when the item is added
        # (ListView.append / .items.insert / set_header_item). Lets the item
        # reach the App struct via get_app().
        self._view = None

    def get_app(self):
        """The App struct this item belongs to, reached through its view.
        None only for a transient item not yet added to a view."""
        return self._view.app if self._view is not None else None

    def get_text(self) -> str:
        return ''

    def copy_text_to_clipboard(self):
        copy_to_clipboard(self.get_text(), self.get_app())

    def set_text(self, txt:str):
        pass

    def draw_line(self, win, offset, width, selected, matched, marked):
        pass

    def activate(self) -> bool:
        """Default action on Enter / double-click. Override in subclasses."""
        return False

    def handle_input(self, keyboard) -> bool:
        if keyboard.key in ENTER_KEYS:
            return self.activate()
        return False

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'double-click':
            return self.activate()
        if mouse.event_type == 'right-click':
            return self.get_app().context_menu.show_context_menu(self)
        return False

class SeparatorItem(Item):
    def __init__(self):
        super().__init__()
        self.is_selectable = False
        self.is_separator = True

class RefListItem(Item):
    def __init__(self, data):
        super().__init__()
        self.data = data

    def get_text(self):
        return self.data['name']

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.get_text()[offset:]
        color, _ = ref_color_and_title(self.data, self.get_app().git_log.head_branch)
        if selected or marked:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]

        win.addstr(line, Screen.color(color, selected, marked, matched))
        win.clrtoeol()

    def activate(self) -> bool:
        app = self.get_app()
        if app.git_log.select_commit(self.data['id']):
            app.git_log.show()
        else:
            app.log.warning(f"Commit with hash {self.data['id']} not found")
        return True

class TextListItem(Item):
    def __init__(self, txt, color = 1, expand = False, selectable = True, dim = False):
        super().__init__()
        self.txt = txt
        self.color = color
        self.expand = expand
        self.is_selectable = selectable
        self.dim = dim

    def get_text(self):
        return self.txt

    def set_text(self, txt:str):
        self.txt = txt

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.get_text()[offset:]
        clear = True
        if selected or marked or self.expand:
            line += ' ' * (width - len(line))
            clear = False
        if len(line) >= width:
            line = line[:width]
            clear = False

        win.addstr(line, Screen.color(self.color, selected, marked, matched, dim = self.dim))
        if clear:
            win.clrtoeol()

class SpacerListItem(Item):
    def __init__(self):
        super().__init__()
        self.is_selectable = False

    def draw_line(self, win, offset, width, selected, matched, marked):
        win.clrtoeol()

class StatListItem(TextListItem):
    def __init__(self, txt:str, color:int, stat_file_path:str):
        self.stat_file_path = stat_file_path
        super().__init__(txt, color)

    def jump_to_file(self):
        app = self.get_app()
        diff = app.git_diff
        app.git_log.add_to_jump_list(diff.commit_id, diff._selected, diff._offset_y)
        diff.set_selected(re.compile(f'diff.*{self.stat_file_path}'), 'top')
        app.git_log.add_to_jump_list(diff.commit_id, diff._selected, diff._offset_y)

    def activate(self) -> bool:
        self.jump_to_file()
        return True

class DiffListItem(TextListItem):
    def __init__(self, line:int, txt:str, color:int,
                 old_file_path:typing.Optional[str] = None, old_file_line:typing.Optional[int] = None,
                 new_file_path:typing.Optional[str] = None, new_file_line:typing.Optional[int] = None):
        self.line = line
        self.old_file_line = old_file_line
        self.old_file_path = old_file_path
        self.new_file_line = new_file_line
        self.new_file_path = new_file_path
        super().__init__(txt, color)

    def jump_to_origin(self):
        from gitkcli import Job  # late import: Job not yet extracted
        app = self.get_app()
        blame_revision = app.git_diff.job.get_old_revision()
        if self.old_file_path and self.old_file_line and blame_revision:
            args = ['git', 'blame', '-lsfn', '-L',
                    f'{self.old_file_line},{self.old_file_line}',
                    blame_revision,
                    '--', self.old_file_path]

            result = Job.run_job(app, args)
            if result.returncode == 0:
                # Example output:
                # a42cadebfe42d85cbf36f4887be166b34077b3e2 test test.txt 1 1) aaa
                match = re.search(r'^(\S+) ([^)]+) ([0-9]+) ', result.stdout)
                if match:
                    id = str(match.group(1))
                    file_path = str(match.group(2))
                    file_line = int(match.group(3))

                    # When commit id starts with '^' it means this is initial git-id and is 1 char shorer
                    # ^1af87e6c2614c1aea4a81476df0deb8206d5489 451)         except Exception:
                    if id.startswith('^'):
                        id = Job.run_job(app, ['git', 'rev-parse', id]).stdout.lstrip('^').rstrip()
                    commit = app.git_log.select_commit(id)
                    if commit:
                        diff = app.git_diff
                        app.git_log.add_to_jump_list(diff.commit_id, diff._selected, diff._offset_y)

                        def on_finished():
                            diff.select_line(file_path, file_line)
                            app.git_log.add_to_jump_list(commit.id, diff._selected, diff._offset_y)

                        diff.job.show_commit(commit.id, on_finished=on_finished, add_to_jump_list=False)

    def activate(self) -> bool:
        self.jump_to_origin()
        return True

class SegmentedListItem(Item):
    def __init__(self, segments = [], bg_color = 1):
        super().__init__()
        self.segment_separator = ' '
        # Character used for the FillerSegment and the trailing fill. Defaults to
        # a space (an ordinary row); the rule-line title bar overrides it to '─'.
        self.fill_char = ' '
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
        if 'left-click' == mouse.event_type or 'double-click' == mouse.event_type:
            self.clicked_segment = segment
        elif self.clicked_segment:
            if 'release' in mouse.event_type:
                self.clicked_segment = None
            if 'move-in' in mouse.event_type and self.clicked_segment != self.get_segment_on_offset(mouse.x):
                mouse.event_type = mouse.event_type.replace('in', 'out')
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
            self.fill_width = max(0, int((width - len(self.get_text())) / fillers_count))
            return self.fill_width * self.fill_char
        return ''

    def _segment_selected(self, index, selected):
        # Per-segment highlight flag. Base: whole row follows the row selection.
        return selected

    def _bg_selected(self, selected):
        # Highlight flag for separators/fillers/trailing fill (the row background).
        return selected

    def draw_line(self, win, offset, width, selected, matched, marked):
        draw_separator = False
        remaining_width = width
        bg_selected = self._bg_selected(selected)
        for index, segment in enumerate(self.get_segments()):
            if draw_separator and self.segment_separator:
                draw_separator = False
                remaining_width -= len(self.segment_separator)
                win.addstr(self.segment_separator, Screen.color(self.bg_color, bg_selected, marked, matched))
            if isinstance(segment, FillerSegment):
                txt = self.get_fill_txt(width)
                win.addstr(txt, Screen.color(self.bg_color, bg_selected, marked, matched))
                length = len(txt)
            else:
                length = segment.draw(win, offset, remaining_width, self._segment_selected(index, selected), matched, marked)
                txt = segment.get_text()
            draw_separator = length > 0
            remaining_width -= length
            if remaining_width <= 0:
                break
            offset -= len(txt) - length

        if remaining_width > 0:
            if bg_selected or marked:
                win.addstr(' ' * remaining_width, Screen.color(self.bg_color, bg_selected, marked, matched))
            elif self.fill_char != ' ':
                # rule-line bar: trail the title with its fill character ('─')
                win.addstr(self.fill_char * remaining_width, Screen.color(self.bg_color, bg_selected, marked, matched))
            else:
                win.clrtoeol()

class ButtonRowItem(SegmentedListItem):
    """A row of buttons navigable with Left/Right (or h/l); Enter activates the
    focused button. Only the focused button is highlighted, not the whole row."""
    def __init__(self, segments = [], bg_color = 1):
        super().__init__(segments, bg_color)
        self.is_selectable = True
        indices = self._button_indices()
        self.focused = indices[0] if indices else 0

    def _button_indices(self):
        return [i for i, s in enumerate(self.segments) if hasattr(s, 'activate')]

    def reset_focus(self):
        # Back to the default (first/primary) button. Reused dialogs call this on
        # open so focus doesn't linger on whatever was picked last time.
        indices = self._button_indices()
        self.focused = indices[0] if indices else 0

    def focus_last(self):
        # Focus the last (rightmost) button. Destructive confirm dialogs default
        # here so a stray Enter hits the safe (cancel) button.
        indices = self._button_indices()
        self.focused = indices[-1] if indices else 0

    def _move_focus(self, direction):
        indices = self._button_indices()
        if not indices:
            return
        pos = indices.index(self.focused) if self.focused in indices else 0
        self.focused = indices[(pos + direction) % len(indices)]

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_LEFT or key == ord('h'):
            self._move_focus(-1)
            return True
        if key == curses.KEY_RIGHT or key == ord('l'):
            self._move_focus(1)
            return True
        if key in ENTER_KEYS:
            if 0 <= self.focused < len(self.segments) and hasattr(self.segments[self.focused], 'activate'):
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
    LINE_ACTIVE, LINE_INACTIVE = 5, 18
    TEXT_ACTIVE, TEXT_INACTIVE = 1, 18
    CLOSE_COLOR = 2  # red [X] close button, in both active and inactive bars

    def __init__(self, title:str, additional_segments = [], title_color = None):
        # title_color overrides the title's text colour when active. The bar
        # shows a live "[current/total]" line counter after the title, updated
        # generically by View.draw_header from the owning view's state.
        self._base_title = title
        self.title_segment = TextSegment(title, self.TEXT_ACTIVE)
        self._title_color = title_color
        self._leading = TextSegment('─', self.LINE_ACTIVE)
        self._close_segment = ButtonSegment("[X]", lambda: self.get_app().screen.hide_active_view(), self.TEXT_ACTIVE)
        segments = [self._leading,
                    self.title_segment,
                    FillerSegment()]
        segments.extend(additional_segments)
        segments.append(self._close_segment)
        super().__init__(segments, self.LINE_INACTIVE)
        self.fill_char = '─'

    def set_title(self, txt:str):
        self._base_title = txt
        self.title_segment.set_text(txt)

    def set_counter(self, current:int, total:int):
        self.title_segment.set_text(f'{self._base_title} [{current}/{total}]')

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
        if 'double-click' == mouse.event_type:
            self.get_app().screen.get_active_view().toggle_window_mode()
            return True
        return False

class UncommittedChangesListItem(SegmentedListItem):
    def __init__(self, staged:bool = False):
        super().__init__()
        self._staged = staged
        self.id = 'local-staged' if staged else 'local-working'
        # Graph art mirrored from the HEAD row when in --graph mode; set by
        # GitLogView._place_uncommitted_rows, empty otherwise.
        self.graph_prefix = ''
        if self._staged:
            self.txt, self.color = 'Uncommitted changes (staged)', 3
        else:
            self.txt, self.color = 'Uncommitted changes (working directory)', 2

    def get_segments(self):
        segments = []
        if self.graph_prefix:
            segments.append(TextSegment(self.graph_prefix))
        segments.append(TextSegment(self.txt, self.color))
        return segments

    def load_to_view(self):
        diff = self.get_app().git_diff
        if diff.commit_id == self.id:
            return
        diff.job.show_diff('HEAD', cached = self._staged, title = self.txt,
                           view_id = self.id, add_to_jump_list = True)

    def activate(self) -> bool:
        self.load_to_view()
        self.get_app().git_diff.show()
        return True

class CommitListItem(SegmentedListItem):
    def __init__(self, id:str):
        super().__init__()
        self.id = id

    def get_segments(self):
        app = self.get_app()
        commit = app.git_log.commits[self.id]
        segments = []

        if commit['prefix']:
            segments.append(TextSegment(commit['prefix']))
        if app.git_log.show_commit_id:
            segments.append(TextSegment(self.id[:7], 4))
        if app.git_log.show_commit_date:
            segments.append(TextSegment(commit['date'].strftime("%Y-%m-%d %H:%M"), 5))
        if app.git_log.show_commit_author:
            segments.append(TextSegment(commit['author'], 6))
        segments.append(TextSegment(commit['title']))

        head_position = len(segments) + 1 # +1, because we want to skip 'HEAD ->' segment
        for ref in app.git_refs.refs.get(self.id, []):
            segments.insert(head_position if ref['name'] == app.git_log.head_branch else len(segments),
                            RefSegment(ref, app.git_log.head_branch))

        # These segments are rebuilt each call (not the wired self.segments), so
        # back-wire them so they can reach the app via get_app() too.
        for segment in segments:
            segment._item = self
        return segments

    def draw_line(self, win, offset, width, selected, matched, marked):
        super().draw_line(win, offset, width, selected, matched, self.get_app().git_log.marked_commit_id == self.id)

    def load_to_view(self):
        diff = self.get_app().git_diff
        if diff.commit_id != self.id or diff.is_diff:
            diff.job.show_commit(self.id)

    def activate(self) -> bool:
        self.load_to_view()
        self.get_app().git_diff.show()
        return True

class ContextMenuItem(TextListItem):
    def __init__(self, text, action, args=[], is_selectable=True):
        super().__init__(text, selectable = is_selectable, dim = not is_selectable)
        self.action = action
        self.args = args if args else []

    def activate(self) -> bool:
        if self.is_selectable:
            self.get_app().screen.hide_active_view()
            self.action(*self.args)
        return True

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type in ('left-click', 'double-click', 'right-release'):
            return self.activate()
        return super().handle_mouse_input(mouse)

class UserInputListItem(Item):
    def __init__(self, color = 1):
        super().__init__()
        self.txt = ''
        self.offset = 0
        self.cursor_pos = 0
        self.color = color

    def clear(self):
        self.txt = ''
        self.offset = 0
        self.cursor_pos = 0

    def get_text(self):
        return self.txt

    def set_text(self, txt:str):
        self.txt = txt
        self.offset = 0
        self.cursor_pos = len(txt)

    def prev_word_pos(self):
        pos = self.cursor_pos
        while pos > 0 and self.txt[pos-1].isspace():
            pos -= 1
        while pos > 0 and not self.txt[pos-1].isspace():
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

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_BACKSPACE or key == 127:  # Backspace
            if self.cursor_pos > 0:
                self.txt = self.txt[:self.cursor_pos-1] + self.txt[self.cursor_pos:]
                self.cursor_pos -= 1

        elif key == KEY_CTRL_BACKSPACE:  # Ctrl+Backspace: delete previous word
            start = self.prev_word_pos()
            self.txt = self.txt[:start] + self.txt[self.cursor_pos:]
            self.cursor_pos = start

        elif key == curses.KEY_DC:  # Delete key
            if self.cursor_pos < len(self.txt):
                self.txt = self.txt[:self.cursor_pos] + self.txt[self.cursor_pos+1:]

        elif key == KEY_CTRL_DEL:  # Ctrl+Delete: clear whole text
            self.txt = ''
            self.cursor_pos = 0
            self.offset = 0

        elif key == curses.KEY_LEFT:  # Left arrow
            if self.cursor_pos > 0:
                self.cursor_pos -= 1

        elif key == curses.KEY_RIGHT:  # Right arrow
            if self.cursor_pos < len(self.txt):
                self.cursor_pos += 1

        elif key == KEY_CTRL_LEFT:  # Ctrl+Left: previous word
            self.cursor_pos = self.prev_word_pos()

        elif key == KEY_CTRL_RIGHT:  # Ctrl+Right: next word
            self.cursor_pos = self.next_word_pos()

        elif key == curses.KEY_HOME:  # Home key
            self.cursor_pos = 0
            
        elif key == curses.KEY_END:  # End key
            self.cursor_pos = len(self.txt)
            
        elif 32 <= key <= 126:  # Printable characters
            self.txt = self.txt[:self.cursor_pos] + chr(key) + self.txt[self.cursor_pos:]
            self.cursor_pos += 1

        else:
            return super().handle_input(keyboard)

        return True

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'left-click' or mouse.event_type == 'double-click':
            self.cursor_pos = mouse.x if mouse.x < len(self.txt) else len(self.txt)
            return True
        else:
            return super().handle_mouse_input(mouse)

    def draw_line(self, win, offset, width, selected, matched, marked):
        # TODO: update self.offset according to offset so that cursor is always visible

        left_txt = self.txt[self.offset:self.offset+self.cursor_pos]
        right_txt = self.txt[self.offset+self.cursor_pos:self.offset+width-1]

        win.addstr(left_txt, Screen.color(self.color, selected, marked, matched))
        win.addch(ord(' '), curses.A_REVERSE | curses.A_BLINK)
        win.addstr(right_txt, Screen.color(self.color, selected, marked, matched))
        win.addstr(' ' * (width - len(left_txt) - len(right_txt) - 1), Screen.color(self.color, selected, marked, matched))

class ResetModeItem(TextListItem):
    def __init__(self, dialog, mode, txt, color = 1):
        super().__init__(txt, color = color)
        self.dialog = dialog
        self.mode = mode

    def activate(self) -> bool:
        self.dialog.hide()
        self.get_app().git_log.reset(self.mode, self.dialog.commit_id)
        return True

    def draw_line(self, win, offset, width, selected, matched, marked):
        # Keep the chosen mode highlighted even when focus moves to the buttons
        # (ListView.draw always passes marked=False, so we can't use that flag).
        if self.dialog.selected_mode == self.mode:
            selected = True
        return super().draw_line(win, offset, width, selected, matched, marked)

class PreferenceRow(SegmentedListItem):
    """A label + interactive control (toggle/choice). Enter activates the control."""
    def __init__(self, label, control):
        super().__init__([TextSegment(f'  {label}  '), FillerSegment(), control, TextSegment('  ')])
        self.control = control

    def activate(self) -> bool:
        self.control.activate()
        return True

