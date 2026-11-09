"""gitk's segmented rows: the commit rows of the log (graph art, id, date,
author, title, refs) and the uncommitted-changes pseudo-rows. Their segments
are rebuilt from live data on every get_segments() call.
"""

from __future__ import annotations

from tuikit.segmented import SegmentedListItem
from tuikit.segments import TextSegment

from gitk.diff_target import (
    LOCAL_STAGED_ID,
    LOCAL_WORKING_ID,
    STAGED_TITLE,
    WORKING_TITLE,
)
from gitk.ids import ID_GIT_LOG
from gitk.screen import Screen
from gitk.segments import RefSegment


class UncommittedChangesListItem(SegmentedListItem):
    def __init__(self, staged: bool = False):
        super().__init__()
        self._staged = staged
        self.id = LOCAL_STAGED_ID if staged else LOCAL_WORKING_ID
        if self._staged:
            self.txt, self.color = STAGED_TITLE, Screen.C_STATUS
        else:
            self.txt, self.color = WORKING_TITLE, Screen.C_ERROR

    def get_row_context_menu(self):
        return (self, ID_GIT_LOG)

    def get_segments(self):
        segments = self.get_app().git_log.graph_segments(self.id)
        segments.append(TextSegment(self.txt, self.color))
        return self.adopt(segments)

    def load_to_view(self):
        diff = self.get_app().git_diff
        if not diff.shows(self.id):
            diff.show_worktree(self._staged)

    def activate(self) -> bool:
        app = self.get_app()
        # Opening the diff is a jump, so the list can bring the cursor back to
        # this row - and, from the diff pane, back to the log pane with it.
        app.jump_list.record()
        self.load_to_view()
        app.git_diff.show()
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
            return self.adopt([TextSegment(self.id[:7], Screen.C_GIT_ID)])
        segments = app.git_log.graph_segments(self.id)
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

        # Rebuilt each call (not the wired self.segments): wire them to the row.
        return self.adopt(segments)

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
        app = self.get_app()
        # Opening the diff is a jump, so the list can bring the cursor back to
        # this row - and, from the diff pane, back to the log pane with it.
        app.jump_list.record()
        self.load_to_view()
        app.git_diff.show()
        return True
