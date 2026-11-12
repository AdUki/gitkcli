"""gitk's list rows: refs, diff-stat and diff lines, reset modes. The generic
rows (text, separators, input fields, menu entries) come from tuikit.items.
Rows reach the App struct through their view (`get_app()`).
"""

from __future__ import annotations

import re
import typing
from functools import partial

from tuikit.items import TextListItem

from gitk.screen import Screen
from gitk.segments import ref_color_and_title


class RefListItem(TextListItem):
    def __init__(self, data):
        super().__init__("")
        self.data = data

    def get_text(self):
        return self.data["name"]

    def draw_line(self, win, offset, width, selected, matched, marked):
        self.color, _ = ref_color_and_title(
            self.data, self.get_app().git_log.head_branch
        )
        super().draw_line(win, offset, width, selected, matched, marked)

    def activate(self) -> bool:
        app = self.get_app()
        if app.git_log.jump_to_commit(self.data["id"]):
            app.git_log.show()
        else:
            app.log.warning(f"Commit with hash {self.data['id']} not found")
        return True


class StatListItem(TextListItem):
    def __init__(
        self, txt: str, color: int, stat_file_path: str, untracked: bool = False
    ):
        self.stat_file_path = stat_file_path
        # An untracked path (git ls-files --others): a file, or a directory
        # ("sub/", an embedded repository) that has no diff of its own.
        self.untracked = untracked
        super().__init__(txt, color)

    def jump_to_file(self):
        app = self.get_app()
        app.jump_list.record()
        target = self.file_row()
        if target is not None:
            app.git_diff.set_selected(target, "top")

    def file_row(self) -> typing.Optional[int]:
        """Row of this file's "diff" header: the first one after this stat
        row."""
        # Escape the path: filenames can legally contain regex metacharacters
        # ('[', '(', '+', '.', ...); without re.escape a name like "test[1].txt"
        # makes re.compile raise (crash) and benign metachars mis-match the line.
        pattern = re.compile(f"diff.*{re.escape(self.stat_file_path)}")
        items = self._view.items
        start = items.index(self) if self in items else 0
        return next(
            (i for i in range(start, len(items)) if pattern.match(items[i].get_text())),
            None,
        )

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
        inner_color: typing.Optional[int] = None,
        dim: bool = False,
    ):
        self.line = line
        # Set on a line of a patch file: column 0 keeps `color` (this diff's
        # marker), the rest is drawn in `inner_color` (the patch's own marker).
        self.inner_color = inner_color
        self.old_file_line = old_file_line
        self.old_file_path = old_file_path
        self.new_file_line = new_file_line
        self.new_file_path = new_file_path
        super().__init__(txt, color, dim=dim)

    def color_spans(self):
        if self.inner_color is None:
            return super().color_spans()
        txt = self.get_text()
        return [(txt[:1], self.color), (txt[1:], self.inner_color)]

    def jump_to_origin(self):
        from gitk.jobs import (
            Job,
        )  # late import: jobs imports items, so avoid a load-time cycle

        app = self.get_app()
        blame_revision = app.git_diff.blame_revision()
        if not (self.old_file_path and self.old_file_line and blame_revision):
            return
        # Capture the departure point up front: the blame below may not resolve,
        # and by then the cursor has moved off the line it started on.
        point = app.jump_list.capture()

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
        if result.returncode != 0:
            return

        # e.g. "a42cadebfe42d85cbf36f4887be166b34077b3e2 test test.txt 1 1) aaa"
        match = re.search(r"^(\S+) ([^)]+) ([0-9]+) ", result.stdout)
        if not match:
            return
        id, file_path, file_line = match.group(1), match.group(2), int(match.group(3))

        # A '^' prefix marks git blame's boundary (initial) commit; its id is one
        # char short, so resolve it back to a full sha.
        if id.startswith("^"):
            id = Job.run_job(app, ["git", "rev-parse", id]).stdout.lstrip("^").rstrip()

        row = app.git_log.find_row(id)
        if row is None:
            return
        # Record the blamed line before the cursor moves off it, so the jump
        # list comes back here and not to the origin commit.
        app.jump_list.record(point)
        # select_row, not select_commit: the log cursor must not load the origin
        # commit into the diff pane behind the show_commit below.
        app.git_log.select_row(row)

        diff = app.git_diff
        diff.show_commit(
            id, on_finished=partial(diff.select_line, file_path, file_line)
        )

    def activate(self) -> bool:
        self.jump_to_origin()
        return True


class ResetModeItem(TextListItem):
    def __init__(self, dialog, mode, txt, color=Screen.C_NORMAL):
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
