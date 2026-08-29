"""JumpList: the back/forward history shared by the log and the diff pane.

A jump point is a place the user *left*: which pane had the focus, what the
diff pane was showing there - a commit sha or a `local-*` worktree key, which
is also the id of the matching log row, so one key restores both panes - and,
for a point inside the diff, the line and scroll offset within it.

Only explicit jumps record a point: opening a commit's diff, jump-to-file,
blame-to-origin, g/G/Home/End, a search match, a jump to a ref or to the marked
commit. Walking the cursor and switching panes (q, Ctrl+W) do not, so the list
stays a list of jumps rather than a scroll history - vim's jumplist rule.

`move` then walks the list restoring the pane along with the position: stepping
back out of a diff opened from the log lands on the log row it was opened from,
and the forward step returns to the diff at the line it was left at.
"""

from __future__ import annotations

import dataclasses
import typing

from gitk.ids import ID_GIT_DIFF, ID_GIT_LOG

# self.index while the current position is the live one, newer than every
# recorded entry (nothing has been stepped back to yet).
LIVE = -1


@dataclasses.dataclass(frozen=True)
class JumpPoint:
    pane: str  # ID_GIT_LOG / ID_GIT_DIFF: the pane to restore the focus to
    view_key: str  # commit sha or local-* key: the log row and the diff content
    line: typing.Optional[int] = None  # position inside the diff, None in the log
    offset_y: typing.Optional[int] = None


class JumpList:
    def __init__(self, app):
        self.app = app
        # Newest first; entries[index] is the entry currently stood on.
        self.entries: typing.List[JumpPoint] = []
        self.index = LIVE

    def capture(self) -> typing.Optional[JumpPoint]:
        """The current location, or None when there is nothing restorable to
        come back to (a range diff or tag body, an empty log)."""
        diff = self.app.git_diff
        if self._focused_pane() is diff:
            if diff.target is None or not diff.target.tracks_position:
                return None
            return JumpPoint(ID_GIT_DIFF, diff.view_key, diff._selected, diff._offset_y)
        view_key = getattr(self.app.git_log.get_selected(), "id", "")
        return JumpPoint(ID_GIT_LOG, view_key) if view_key else None

    def record(self, point: typing.Optional[JumpPoint] = None):
        """Put the place a jump departs from on the list. Call it *before* the
        jump moves anything; for a jump that may not happen (a search that
        finds nothing), capture() the point up front and pass it here once it
        did."""
        if point is None:
            point = self.capture()
            if point is None:
                return
        if self.index > 0:
            # Jumping while stood in the middle of the list discards the
            # entries walked back through - they are the forward history.
            del self.entries[: self.index]
        self.index = LIVE
        if not (self.entries and self.entries[0] == point):
            self.entries.insert(0, point)

    def move(self, jump: int) -> bool:
        """Walk the list: +1 back (older), -1 forward (newer). Always True -
        the key is consumed even when there is nowhere left to go."""
        index = self.index
        if index == LIVE:
            if jump < 0 or not self.entries:
                return True  # nothing is newer than the live position
            point = self.capture()
            if point is not None:
                # Keep the live position as the newest entry, so the matching
                # forward step comes back here.
                if self.entries[0] != point:
                    self.entries.insert(0, point)
                index = 0
            # A live position that cannot be returned to does not go on the
            # list; stepping back off LIVE (-1) then lands on entries[0].
        new_index = index + jump
        if not (0 <= new_index < len(self.entries)):
            return True
        self.index = new_index
        if not self._restore(self.entries[new_index]):
            # The row is gone (a reload dropped the commit): skip the entry.
            return self.move(jump)
        return True

    def _restore(self, point: JumpPoint) -> bool:
        """Put the panes back where `point` says and focus the one it names.
        False when its log row is not loaded, so move() can skip the entry."""
        log, diff = self.app.git_log, self.app.git_diff
        index = log.find_row(point.view_key)
        if index is None:
            return False
        if point.line is not None:
            # Seed the position before the load below, so the diff lands on the
            # line of the point rather than the one last visited there.
            diff.remember_position(point.view_key, point.line, point.offset_y)
        # Move the cursor directly: GitLogView.set_selected would load the row
        # into the diff pane behind our back.
        log.select_row(index)
        to_diff = point.pane == ID_GIT_DIFF
        if diff.shows(point.view_key):
            if point.line is not None:
                diff.restore_view_position(point.line, point.offset_y)
        elif to_diff or self.app.screen.is_view_visible(diff):
            # Only pay for the reload when the diff pane is (about to be) on
            # screen; a hidden one is refreshed by whatever raises it next.
            log.items[index].load_to_view()
        (diff if to_diff else log).show()
        return True

    def _focused_pane(self):
        """The log/diff pane focused last. Read off the view stack rather than
        from the active view, so a popup on top of it (search dialog, context
        menu) does not change which pane a jump departs from."""
        panes = (self.app.git_log, self.app.git_diff)
        for view in reversed(self.app.screen.showed_views):
            if view in panes:
                return view
        return self.app.git_log
