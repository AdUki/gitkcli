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
import typing

from gitk.config import copy_to_clipboard
from gitk.input import (
    ENTER_KEYS,
    KEY_CTRL_BACKSPACE,
    KEY_CTRL_DEL,
    KEY_CTRL_LEFT,
    KEY_CTRL_RIGHT,
)
from gitk.screen import Screen
from gitk.segments import ref_color_and_title


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
        return ""

    def copy_text_to_clipboard(self):
        copy_to_clipboard(self.get_text(), self.get_app())

    def set_text(self, txt: str):
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
        if mouse.event_type == "double-click":
            return self.activate()
        if mouse.event_type == "right-click":
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
        return self.data["name"]

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.get_text()[offset:]
        color, _ = ref_color_and_title(self.data, self.get_app().git_log.head_branch)
        if selected or marked:
            line += " " * (width - len(line))
        if len(line) > width:
            line = line[:width]

        win.addstr(line, Screen.color(color, selected, marked, matched))
        win.clrtoeol()

    def activate(self) -> bool:
        app = self.get_app()
        if app.git_log.select_commit(self.data["id"]):
            app.git_log.show()
        else:
            app.log.warning(f"Commit with hash {self.data['id']} not found")
        return True


class TextListItem(Item):
    def __init__(self, txt, color=1, expand=False, selectable=True, dim=False):
        super().__init__()
        self.txt = txt
        self.color = color
        self.expand = expand
        self.is_selectable = selectable
        self.dim = dim

    def get_text(self):
        return self.txt

    def set_text(self, txt: str):
        self.txt = txt

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.get_text()[offset:]
        clear = True
        if selected or marked or self.expand:
            line += " " * (width - len(line))
            clear = False
        if len(line) >= width:
            line = line[:width]
            clear = False

        win.addstr(
            line, Screen.color(self.color, selected, marked, matched, dim=self.dim)
        )
        if clear:
            win.clrtoeol()


class SpacerListItem(Item):
    def __init__(self):
        super().__init__()
        self.is_selectable = False

    def draw_line(self, win, offset, width, selected, matched, marked):
        win.clrtoeol()


class StatListItem(TextListItem):
    def __init__(self, txt: str, color: int, stat_file_path: str):
        self.stat_file_path = stat_file_path
        super().__init__(txt, color)

    def jump_to_file(self):
        app = self.get_app()
        diff = app.git_diff
        app.git_log.add_to_jump_list(diff.commit_id, diff._selected, diff._offset_y)
        # Escape the path: filenames can legally contain regex metacharacters
        # ('[', '(', '+', '.', ...); without re.escape a name like "test[1].txt"
        # makes re.compile raise (crash) and benign metachars mis-match the line.
        diff.set_selected(re.compile(f"diff.*{re.escape(self.stat_file_path)}"), "top")
        app.git_log.add_to_jump_list(diff.commit_id, diff._selected, diff._offset_y)

    def activate(self) -> bool:
        self.jump_to_file()
        return True


class DiffListItem(TextListItem):
    def __init__(
        self,
        line: int,
        txt: str,
        color: int,
        old_file_path: typing.Optional[str] = None,
        old_file_line: typing.Optional[int] = None,
        new_file_path: typing.Optional[str] = None,
        new_file_line: typing.Optional[int] = None,
    ):
        self.line = line
        self.old_file_line = old_file_line
        self.old_file_path = old_file_path
        self.new_file_line = new_file_line
        self.new_file_path = new_file_path
        super().__init__(txt, color)

    def jump_to_origin(self):
        from gitk.jobs import (
            Job,
        )  # late import: jobs imports items, so avoid a load-time cycle

        app = self.get_app()
        blame_revision = app.git_diff.job.get_old_revision()
        if self.old_file_path and self.old_file_line and blame_revision:
            args = [
                "git",
                "blame",
                "-lsfn",
                "-L",
                f"{self.old_file_line},{self.old_file_line}",
                blame_revision,
                "--",
                self.old_file_path,
            ]

            result = Job.run_job(app, args)
            if result.returncode == 0:
                # Example output:
                # a42cadebfe42d85cbf36f4887be166b34077b3e2 test test.txt 1 1) aaa
                match = re.search(r"^(\S+) ([^)]+) ([0-9]+) ", result.stdout)
                if match:
                    id = str(match.group(1))
                    file_path = str(match.group(2))
                    file_line = int(match.group(3))

                    # When commit id starts with '^' it means this is initial git-id and is 1 char shorer
                    # ^1af87e6c2614c1aea4a81476df0deb8206d5489 451)         except Exception:
                    if id.startswith("^"):
                        id = (
                            Job.run_job(app, ["git", "rev-parse", id])
                            .stdout.lstrip("^")
                            .rstrip()
                        )
                    commit = app.git_log.select_commit(id)
                    if commit:
                        diff = app.git_diff
                        app.git_log.add_to_jump_list(
                            diff.commit_id, diff._selected, diff._offset_y
                        )

                        def on_finished():
                            diff.select_line(file_path, file_line)
                            app.git_log.add_to_jump_list(
                                commit.id, diff._selected, diff._offset_y
                            )

                        diff.job.show_commit(
                            commit.id, on_finished=on_finished, add_to_jump_list=False
                        )

    def activate(self) -> bool:
        self.jump_to_origin()
        return True


class ContextMenuItem(TextListItem):
    def __init__(self, text, action, args=[], is_selectable=True):
        super().__init__(text, selectable=is_selectable, dim=not is_selectable)
        self.action = action
        self.args = args if args else []

    def activate(self) -> bool:
        if self.is_selectable:
            self.get_app().screen.hide_active_view()
            self.action(*self.args)
        return True

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type in ("left-click", "double-click", "right-release"):
            return self.activate()
        return super().handle_mouse_input(mouse)


class UserInputListItem(Item):
    def __init__(self, color=1):
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

    def set_text(self, txt: str):
        self.txt = txt
        self.offset = 0
        self.cursor_pos = len(txt)

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

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_BACKSPACE or key == 127:  # Backspace
            if self.cursor_pos > 0:
                self.txt = self.txt[: self.cursor_pos - 1] + self.txt[self.cursor_pos :]
                self.cursor_pos -= 1

        elif key == KEY_CTRL_BACKSPACE:  # Ctrl+Backspace: delete previous word
            start = self.prev_word_pos()
            self.txt = self.txt[:start] + self.txt[self.cursor_pos :]
            self.cursor_pos = start

        elif key == curses.KEY_DC:  # Delete key
            if self.cursor_pos < len(self.txt):
                self.txt = self.txt[: self.cursor_pos] + self.txt[self.cursor_pos + 1 :]

        elif key == KEY_CTRL_DEL:  # Ctrl+Delete: clear whole text
            self.txt = ""
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
            self.txt = (
                self.txt[: self.cursor_pos] + chr(key) + self.txt[self.cursor_pos :]
            )
            self.cursor_pos += 1

        else:
            return super().handle_input(keyboard)

        return True

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == "left-click" or mouse.event_type == "double-click":
            # mouse.x is a column within the (possibly scrolled) field.
            self.cursor_pos = min(self.offset + mouse.x, len(self.txt))
            return True
        else:
            return super().handle_mouse_input(mouse)

    def draw_line(self, win, offset, width, selected, matched, marked):
        # Scroll the field horizontally so the cursor stays visible and we never
        # addstr past `width`. The cursor is a 1-col block between the text to
        # its left and right, so the text occupies `field` = width-1 columns.
        # (At offset 0 with the cursor in view this matches the old rendering.)
        field = max(1, width - 1)
        if self.cursor_pos < self.offset:
            self.offset = self.cursor_pos
        elif self.cursor_pos - self.offset > field:
            self.offset = self.cursor_pos - field

        left_txt = self.txt[self.offset : self.cursor_pos]
        right_txt = self.txt[self.cursor_pos : self.offset + field]
        pad = max(0, width - len(left_txt) - len(right_txt) - 1)

        win.addstr(left_txt, Screen.color(self.color, selected, marked, matched))
        win.addch(ord(" "), curses.A_REVERSE | curses.A_BLINK)
        win.addstr(right_txt, Screen.color(self.color, selected, marked, matched))
        win.addstr(" " * pad, Screen.color(self.color, selected, marked, matched))


class ResetModeItem(TextListItem):
    def __init__(self, dialog, mode, txt, color=1):
        super().__init__(txt, color=color)
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
