"""GitDiffView: the commit/diff pane."""

from __future__ import annotations

import curses
import re
import typing

from gitk.config import KEY_CTRL
from gitk.dialogs import SearchDialogPopup
from gitk.ids import ID_GIT_DIFF, ID_GIT_DIFF_SEARCH
from gitk.input import KeyboardState
from gitk.items import DiffListItem
from gitk.jobs import GitDiffJob
from gitk.list_view import ListView, _raise_split_sibling
from gitk.segmented_items import WindowTopBarItem
from gitk.segments import (
    ButtonSegment,
    DynamicTextSegment,
    HighlightToggleSegment,
    TextSegment,
)


class GitDiffView(ListView):
    def __init__(self, app):
        super().__init__(app, ID_GIT_DIFF, "fullscreen")

        self.context_size = 3
        self.rename_limit = 1570
        self.ignore_whitespace = False
        self.job = GitDiffJob(self.app)

        self.commit_id = ""
        self.is_diff = False

        self.set_header_item(
            WindowTopBarItem(
                "Git commit diff",
                [
                    TextSegment("Context:", 30),
                    DynamicTextSegment(lambda: self.app.git_diff.context_size, 30),
                    ButtonSegment("[+]", lambda: self.change_context(+1), 30),
                    ButtonSegment("[-]", lambda: self.change_context(-1), 30),
                    HighlightToggleSegment(
                        "[Ignore whitespace]",
                        lambda: self.app.git_diff.ignore_whitespace,
                        lambda: self.app.git_diff.change_ignore_whitespace(),
                        30,
                    ),
                    ButtonSegment(
                        "[<-]", lambda: self.app.git_log.move_in_jump_list(+1), 30
                    ),
                    ButtonSegment(
                        "[->]", lambda: self.app.git_log.move_in_jump_list(-1), 30
                    ),
                ],
                title_color=5,
            )
        )

        self.set_search_dialog(SearchDialogPopup(app, ID_GIT_DIFF_SEARCH))

    def clear(self):
        self.commit_id = ""
        self.is_diff = False
        super().clear()

    def show(self):
        _raise_split_sibling(self, self.app.git_log)
        super().show()

    def _tracks_position(self) -> bool:
        return bool(self.commit_id) and (
            not self.is_diff or self.commit_id.startswith("local-")
        )

    def set_selected(self, what: int | str | re.Pattern, visible_mode="center") -> bool:
        ret = super().set_selected(what, visible_mode)
        if self._tracks_position():
            self.job.selected_line_map[self.commit_id] = (
                self._selected,
                self._offset_y,
            )
        return ret

    def restore_view_position(self, line: int, offset_y: typing.Optional[int] = None):
        self.set_selected(line)
        if offset_y is not None:
            self._offset_y = offset_y
            if self._tracks_position():
                self.job.selected_line_map[self.commit_id] = (
                    self._selected,
                    self._offset_y,
                )

    def select_line(self, file: str, line: int):
        for item in self.items:
            if (
                isinstance(item, DiffListItem)
                and item.new_file_path == file
                and item.new_file_line == line
            ):
                self.set_selected(item.line)
                return  # first match is the target; stop (avoids extra position writes)

    def _reload_diff(self):
        self.clear()
        self.job.selected_line_map.clear()
        self.job.restart_job()

    def change_context(self, size: int):
        self.context_size = max(0, self.context_size + size)
        self._reload_diff()

    def change_ignore_whitespace(self, val: typing.Optional[bool] = None):
        self.ignore_whitespace = not self.ignore_whitespace if val is None else val
        self._reload_diff()

    def handle_input(self, keyboard) -> bool:
        key = keyboard.key
        if self.app.split.split_active() and (
            key == ord("q") or key == curses.KEY_EXIT
        ):
            # Esc/q in split view steps back to the log pane and stays split,
            # rather than collapsing the split.
            self.app.git_log.show()
            return True
        if key == ord("+"):
            # Documented diff keys (README); previously only the [+]/[-] header
            # buttons changed the context size.
            self.change_context(+1)
        elif key == ord("-"):
            self.change_context(-1)
        elif key == KEY_CTRL("n"):
            self.app.git_log.handle_input(KeyboardState(curses.KEY_DOWN))
        elif key == KEY_CTRL("p"):
            self.app.git_log.handle_input(KeyboardState(curses.KEY_UP))
        elif key in (ord("g"), ord("G"), curses.KEY_HOME, curses.KEY_END):
            track = self._tracks_position()
            if track:
                self.app.git_log.add_to_jump_list(
                    self.commit_id, self._selected, self._offset_y
                )
            ret = super().handle_input(keyboard)
            if track:
                self.app.git_log.add_to_jump_list(
                    self.commit_id, self._selected, self._offset_y
                )
            return ret
        else:
            return super().handle_input(keyboard)
        return True
