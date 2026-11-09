"""GitDiffView: the commit/diff pane."""

from __future__ import annotations

import curses
import re
import typing
from functools import partial

from tuikit import keys as K
from tuikit.dialogs import SearchDialog
from tuikit.list_view import ListView
from tuikit.segmented import WindowTopBarItem
from tuikit.segments import (
    ButtonSegment,
    DynamicTextSegment,
    HighlightButtonSegment,
    TextSegment,
)

from gitk.diff_target import (
    CommitTarget,
    DiffOptions,
    RangeTarget,
    TagTarget,
    WorktreeTarget,
)
from gitk.ids import ID_GIT_DIFF, ID_GIT_DIFF_SEARCH
from gitk.items import DiffListItem
from gitk.jobs import GitDiffJob
from gitk.screen import Screen


class GitDiffView(ListView):
    def __init__(self, app):
        super().__init__(app, ID_GIT_DIFF, "fullscreen")

        self.context_size = 3
        self.rename_limit = 1570
        self.ignore_whitespace = False
        self.job = GitDiffJob(self.app)

        # Identity of the content currently shown; None once cleared. Replaces
        # the old commit_id/is_diff field pair - both are derived from it.
        self.target = None
        # Last target ever shown. Unlike `target`, this survives clear()/reload
        # (change_context, change_ignore_whitespace), so option reloads and
        # jump_to_origin's blame keep working after the view has "forgotten"
        # what it's showing.
        self._last_target = None
        # view_key -> (selected line, offset_y), so revisiting the same commit
        # or worktree diff restores where the user left it.
        self.position_map = {}

        self.set_header_item(
            WindowTopBarItem(
                "Git commit diff",
                [
                    TextSegment("Context:", Screen.C_TITLE),
                    DynamicTextSegment(
                        lambda: self.app.git_diff.context_size, Screen.C_TITLE
                    ),
                    ButtonSegment(
                        "[+]", lambda: self.change_context(+1), Screen.C_TITLE
                    ),
                    ButtonSegment(
                        "[-]", lambda: self.change_context(-1), Screen.C_TITLE
                    ),
                    HighlightButtonSegment(
                        "[Ignore whitespace]",
                        lambda: self.app.git_diff.ignore_whitespace,
                        lambda: self.app.git_diff.change_ignore_whitespace(),
                        Screen.C_TITLE,
                    ),
                    ButtonSegment(
                        "[<-]",
                        lambda: self.app.jump_list.move(+1),
                        Screen.C_TITLE,
                    ),
                    ButtonSegment(
                        "[->]",
                        lambda: self.app.jump_list.move(-1),
                        Screen.C_TITLE,
                    ),
                ],
                title_color=Screen.C_DATA,
            )
        )

        self.set_search_dialog(SearchDialog(app, ID_GIT_DIFF_SEARCH))

    def show_commit(self, commit_id: str, on_finished=None):
        """Load 'git show -m COMMIT'."""
        self._show_target(CommitTarget(commit_id), on_finished)

    def show_diff(self, old_commit_id: str, new_commit_id: str):
        """Load a two-revision range diff. Never position-tracked, so it is not
        a place the jump list can bring you back to."""
        self._show_target(RangeTarget(old_commit_id, new_commit_id))

    def show_worktree(self, staged: bool):
        """Load uncommitted changes: the index if staged, else the working tree."""
        self._show_target(WorktreeTarget(staged))

    def show_tag_annotation(self, tag_id: str):
        """Load an annotated tag's body via 'git cat-file -p' and raise this pane."""
        self._show_target(TagTarget(tag_id))
        self.show()

    def shows(self, view_key: str) -> bool:
        """True iff the pane currently shows `view_key` as revisitable,
        position-tracked content (a commit as a commit view, or a worktree
        diff - not a range diff or tag annotation, even if the key matches)."""
        return (
            self.target is not None
            and self.target.view_key == view_key
            and self.target.tracks_position
        )

    @property
    def view_key(self) -> str:
        """The identity key of the content currently shown, or "" once
        cleared/forgotten (e.g. after a context/whitespace reload)."""
        return self.target.view_key if self.target else ""

    def blame_revision(self) -> typing.Optional[str]:
        """Git revision for the 'old' (---) side of the current diff, used as
        the blame base for jump-to-origin. Reads the *last* target shown, so
        it keeps working even after a reload made the view forget its
        identity."""
        return self._last_target.blame_revision() if self._last_target else None

    def remember_position(self, view_key: str, line, offset_y):
        """Seed/overwrite the saved scroll position for `view_key` (used by
        the jump list to pre-seed a target before triggering its load)."""
        self.position_map[view_key] = (line, offset_y)

    def _diff_options(self) -> DiffOptions:
        return DiffOptions(
            self.context_size, self.width, self.rename_limit, self.ignore_whitespace
        )

    def _show_target(self, target, on_finished=None):
        self.clear()
        self.target = target
        self._last_target = target
        self.header_item.set_title(target.title())
        if on_finished is None and target.tracks_position:
            entry = self.position_map.get(target.view_key)
            if entry:
                line, offset_y = entry
                on_finished = partial(self.restore_view_position, line, offset_y)
        self.job.start_job(target.git_args(self._diff_options()), on_finished)

    def clear(self):
        self.target = None
        super().clear()

    def _tracks_position(self) -> bool:
        return self.target is not None and self.target.tracks_position

    def search(self, backward: bool = False, repeat: bool = False) -> bool:
        """A search that lands on a match is a jump: record where it started."""
        point = self.app.jump_list.capture()
        if not super().search(backward, repeat):
            return False
        self.app.jump_list.record(point)
        return True

    def set_selected(self, what: int | str | re.Pattern, visible_mode="center") -> bool:
        ret = super().set_selected(what, visible_mode)
        if self._tracks_position():
            self.position_map[self.target.view_key] = (
                self._selected,
                self._offset_y,
            )
        return ret

    def restore_view_position(self, line: int, offset_y: typing.Optional[int] = None):
        self.set_selected(line)
        if offset_y is not None:
            self._offset_y = offset_y
            if self._tracks_position():
                self.position_map[self.target.view_key] = (
                    self._selected,
                    self._offset_y,
                )

    def select_line(self, file: str, line: int):
        match = next(
            (
                item
                for item in self.items
                if isinstance(item, DiffListItem)
                and item.new_file_path == file
                and item.new_file_line == line
            ),
            None,
        )
        if match is not None:
            self.set_selected(match.line)

    def _reload_diff(self):
        """Re-run the last shown target with fresh options (context size,
        ignore-whitespace). The view forgets its identity in the process (no
        position tracking until the reload finishes and a fresh target is
        shown); a no-op if nothing has ever been shown."""
        self.clear()
        self.position_map.clear()
        if self._last_target is not None:
            self.job.start_job(self._last_target.git_args(self._diff_options()))

    def change_context(self, size: int):
        self.context_size = max(0, self.context_size + size)
        self._reload_diff()

    def change_ignore_whitespace(self, val: typing.Optional[bool] = None):
        self.ignore_whitespace = not self.ignore_whitespace if val is None else val
        self._reload_diff()

    def handle_key(self, event) -> bool:
        key = event.key
        if self.app.split.split_active() and key in (ord("q"), curses.KEY_EXIT):
            # Esc/q in split view steps back to the log pane and stays split,
            # rather than collapsing the split.
            self.app.git_log.show()
            return True
        if key == ord("+"):
            # +/- adjust the context size from the keyboard (README), like the
            # [+]/[-] header buttons.
            self.change_context(+1)
        elif key == ord("-"):
            self.change_context(-1)
        elif key == K.ctrl("n"):
            self.app.git_log.handle_key(K.KeyEvent(curses.KEY_DOWN))
        elif key == K.ctrl("p"):
            self.app.git_log.handle_key(K.KeyEvent(curses.KEY_UP))
        elif key in (ord("g"), ord("G"), curses.KEY_HOME, curses.KEY_END):
            self.app.jump_list.record()
            return super().handle_key(event)
        else:
            return super().handle_key(event)
        return True
