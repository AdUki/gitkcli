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
    STAGED_TITLE,
    WORKING_TITLE,
    CommitTarget,
    DiffOptions,
    RangeTarget,
    TagTarget,
    WorktreeTarget,
)
from gitk.ids import ID_GIT_DIFF, ID_GIT_DIFF_SEARCH
from gitk.items import DiffListItem
from gitk.jobs import GitDiffJob
from gitk.keymap import handle_copy_key
from gitk.screen import Screen
from gitk.stage import (
    DISCARD,
    STAGE,
    UNSTAGE,
    Stager,
    content_anchor,
    find_anchor,
)

# keymap action -> (stage action, line only)
STAGE_ACTIONS = {
    "diff.stage": (STAGE, False),
    "diff.stage_line": (STAGE, True),
    "diff.unstage": (UNSTAGE, False),
    "diff.unstage_line": (UNSTAGE, True),
    "diff.discard": (DISCARD, False),
    "diff.discard_line": (DISCARD, True),
}


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

        self.text_selection = True
        self.stager = Stager(self)

        def context_segments():
            return [
                TextSegment("Context:", Screen.C_TITLE),
                DynamicTextSegment(
                    lambda: self.app.git_diff.context_size, Screen.C_TITLE
                ),
                ButtonSegment("[+]", lambda: self.change_context(+1), Screen.C_TITLE),
                ButtonSegment("[-]", lambda: self.change_context(-1), Screen.C_TITLE),
            ]

        def jump_segments():
            return [
                ButtonSegment(
                    "[<-]", lambda: self.app.jump_list.move(+1), Screen.C_TITLE
                ),
                ButtonSegment(
                    "[->]", lambda: self.app.jump_list.move(-1), Screen.C_TITLE
                ),
            ]

        self.diff_header = WindowTopBarItem(
            "Git commit diff",
            context_segments()
            + [
                HighlightButtonSegment(
                    "[Ignore whitespace]",
                    lambda: self.app.git_diff.ignore_whitespace,
                    lambda: self.app.git_diff.change_ignore_whitespace(),
                    Screen.C_TITLE,
                ),
            ]
            + jump_segments(),
            title_color=Screen.C_DATA,
        )

        # The uncommitted-changes (git gui) mode: staging buttons instead of
        # ignore-whitespace (a hunk shown without its whitespace changes would
        # not apply). The working directory's changes are staged or
        # discarded, the staged ones unstaged.
        def button(label, action):
            return ButtonSegment(label, action, Screen.C_TITLE)

        commit = button("[Commit]", self.open_commit_dialog)
        self.working_header = WindowTopBarItem(
            WORKING_TITLE,
            context_segments()
            + [
                button("[Stage]", lambda: self.stage_action(STAGE)),
                button("[Discard]", lambda: self.stage_action(DISCARD)),
                commit,
            ]
            + jump_segments(),
            title_color=Screen.C_DATA,
        )
        self.staged_header = WindowTopBarItem(
            STAGED_TITLE,
            context_segments()
            + [
                button("[Unstage]", lambda: self.stage_action(UNSTAGE)),
                button("[Commit]", self.open_commit_dialog),
            ]
            + jump_segments(),
            title_color=Screen.C_DATA,
        )
        self.set_header_item(self.diff_header)

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
        if not isinstance(target, WorktreeTarget):
            header = self.diff_header
        elif target.staged:
            header = self.staged_header
        else:
            header = self.working_header
        if self.header_item is not header:
            self.set_header_item(header)
        self.header_item.set_title(target.title())
        if on_finished is None and target.tracks_position:
            entry = self.position_map.get(target.view_key)
            if entry:
                line, offset_y = entry
                on_finished = partial(self.restore_view_position, line, offset_y)
        self._start(target, on_finished)

    def _start(self, target, on_finished=None):
        if isinstance(target, WorktreeTarget):
            steps = target.steps(self._diff_options())
            self.job.start_steps(steps, self.app.git_toplevel() or None, on_finished)
        else:
            self.job.start_job(target.git_args(self._diff_options()), on_finished)

    def shows_worktree(self) -> bool:
        """Whether the pane is in its uncommitted-changes (git gui) mode."""
        return isinstance(self.target, WorktreeTarget)

    def reload_worktree(self):
        """Re-read the uncommitted changes (after staging, a commit, F5) in
        place; a no-op unless they are shown. The old rows stay on screen
        while the new ones load, then are swapped for them in one go with the
        cursor and scroll position kept - no flash of the top of the list."""
        if not self.shows_worktree():
            return
        target = self.target
        steps = target.steps(self._diff_options())

        def swap():
            rows, self.job.buffer = self.job.buffer, None
            if rows is not None and self.target is target:
                self._replace_items(rows)

        self.job.start_steps(
            steps, self.app.git_toplevel() or None, swap, buffered=True
        )

    def _replace_items(self, rows):
        """Show `rows` instead of the current ones. The cursor stays on the
        same place in the content - the same distance into the same file of
        the same section (gitk.stage.content_anchor) - and on the same
        screen line, so the section being worked on does not move when the
        other one grows or shrinks above it. A cursor landing on an
        unselectable row moves to the nearest selectable one."""
        selected = self._selected
        screen_line = self._selected - self._offset_y
        anchored = find_anchor(rows, content_anchor(self.items, selected))
        if anchored is not None:
            selected = anchored
        self.app.mouse.drop_grab_of(self)
        self.clear_selection()
        for row in rows:
            row._view = self
        self.items = rows
        last = len(rows) - 1
        selected = max(0, min(selected, last))
        if rows and not rows[selected].is_selectable:
            near = sorted(
                (i for i, r in enumerate(rows) if r.is_selectable),
                key=lambda i: (abs(i - selected), i < selected),
            )
            if near:
                selected = near[0]
        self._selected = selected
        # Not clamped to len - height: the view may already show blank space
        # below the end (a section title scrolled to the top), and pulling it
        # back would move the rows under the user's eyes.
        # A list that fits the pane is shown whole.
        if len(rows) <= self.height:
            self._offset_y = 0
        else:
            self._offset_y = max(0, selected - screen_line)
        # keep the cursor on screen
        if not self._offset_y <= selected < self._offset_y + self.height:
            self._offset_y = max(0, selected - self.height // 2)
        if self._tracks_position():
            self.position_map[self.target.view_key] = (self._selected, self._offset_y)
        self.dirty = True

    def stage_action(self, action: str, line_only: bool = False):
        if not self.shows_worktree():
            self.app.screen.show_flash("Open the uncommitted changes to stage")
            return
        self.stager.run(action, line_only)
        self.dirty = True

    def stage_all(self):
        """Stage everything (working directory) / unstage everything (staged)."""
        if not self.shows_worktree():
            self.app.screen.show_flash("Open the uncommitted changes to stage")
            return
        self.stager.run_all()
        self.dirty = True

    def open_commit_dialog(self):
        self.app.commit_dialog.open()

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
        # The content may have shrunk since (the staging view after a stage).
        line = max(0, min(line, len(self.items) - 1))
        if offset_y is not None:
            offset_y = max(0, min(offset_y, len(self.items) - self.height))
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
        if self.shows_worktree():
            # The staging view stays itself (its target, its buttons).
            self.position_map.clear()
            self._show_target(self.target)
            return
        self.clear()
        self.position_map.clear()
        if self._last_target is not None:
            self._start(self._last_target)

    def change_context(self, size: int):
        self.context_size = max(0, self.context_size + size)
        self._reload_diff()

    def change_ignore_whitespace(self, val: typing.Optional[bool] = None):
        self.ignore_whitespace = not self.ignore_whitespace if val is None else val
        self._reload_diff()

    def handle_key(self, event) -> bool:
        key = event.key
        if key == curses.KEY_EXIT and self.clear_selection():
            return True
        if self.app.split.split_active() and key in (ord("q"), curses.KEY_EXIT):
            # Esc/q in split view steps back to the log pane and stays split,
            # rather than collapsing the split.
            self.app.git_log.show()
            return True
        action = self.app.keymap.action_for("diff", key)
        if action == "diff.more_context":
            # like the [+]/[-] header buttons
            self.change_context(+1)
        elif action == "diff.less_context":
            self.change_context(-1)
        elif action == "diff.ignore_whitespace":
            self.change_ignore_whitespace()
        elif action in STAGE_ACTIONS:
            self.stage_action(*STAGE_ACTIONS[action])
        elif action == "diff.all":
            self.stage_all()
        elif action == "diff.commit":
            self.open_commit_dialog()
        elif key == K.ctrl("n"):
            self.app.git_log.handle_key(K.KeyEvent(curses.KEY_DOWN))
        elif key == K.ctrl("p"):
            self.app.git_log.handle_key(K.KeyEvent(curses.KEY_UP))
        elif key in (ord("g"), ord("G"), curses.KEY_HOME, curses.KEY_END):
            self.app.jump_list.record()
            return super().handle_key(event)
        elif not handle_copy_key(self, key):
            return super().handle_key(event)
        return True
