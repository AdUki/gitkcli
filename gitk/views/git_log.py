"""GitLogView: the commit log pane."""

from __future__ import annotations

import curses
import os
import re
import shlex
import typing

from gitk.dialogs import GitSearchDialogPopup, ResetDialogPopup
from gitk.ids import ID_GIT_LOG
from gitk.jobs import GitLogJob, GitRefreshHeadJob, GitSearchJob, Job
from gitk.list_view import ListView, _raise_split_sibling
from gitk.segmented_items import (
    CommitListItem,
    UncommittedChangesListItem,
    WindowTopBarItem,
)
from gitk.segments import ButtonSegment, SplitButtonSegment


class GitLogView(ListView):
    def __init__(self, app, git_args: typing.List, cmd_args: typing.List):
        super().__init__(app, ID_GIT_LOG, "fullscreen")
        self.commits = {}  # map: git_id --> { parents, date, author, title }

        self.marked_commit_id = ""
        self.jump_list = []
        self.jump_index = 0
        self.head_branch = ""
        self.head_id = ""
        # Whether the working tree / index currently differ; recomputed by
        # check_uncommitted_changes and consumed by _place_uncommitted_rows.
        self._has_working = False
        self._has_staged = False
        # In --graph mode git computes the lane art over the whole commit set, so
        # we cannot append a single commit's art incrementally - HEAD changes must
        # trigger a full reload instead. Recomputed in set_pref_flags.
        self._graph_mode = "--graph" in git_args
        # Commit to re-select once it streams back in after a reload (keeps the
        # cursor where the user left it). One-shot, like _focus_head_pending.
        self._pending_select_id = ""
        # One-shot: scroll to HEAD the first time it can be located after launch,
        # so it is visible even when it is not at the top (uncommitted rows above
        # it, an unrelated revision arg, or a detached/old HEAD deep in the list).
        self._focus_head_pending = True

        self.show_commit_id = True
        self.show_commit_date = True
        self.show_commit_author = True

        self._cli_args = git_args + cmd_args
        self.pref_flags = ""
        self.job = GitLogJob(self.app, ID_GIT_LOG, list(self._cli_args))
        self.job_git_refresh_head = GitRefreshHeadJob(self.app)
        self.job_git_search = GitSearchJob(self.app, cmd_args)
        self.view_reset = ResetDialogPopup(app)

    def set_pref_flags(self, flags: str):
        self.pref_flags = flags
        # Parse like a shell so quoted values survive, e.g. --author="Bob Brown"
        # or --since="2 weeks ago". Fall back to a plain split on an unbalanced
        # quote so a typo in the prefs field can't crash the reload.
        try:
            parsed_flags = shlex.split(flags)
        except ValueError:
            self.app.log.warning(
                f"Could not parse git log flags (unbalanced quote?): {flags}"
            )
            parsed_flags = flags.split()
        self.job.args = list(self._cli_args) + parsed_flags
        # --graph may also arrive via the preferences "flags" field.
        self._graph_mode = "--graph" in self.job.args

        repo_name = os.path.basename(
            Job.run_job(
                self.app, ["git", "rev-parse", "--show-toplevel"]
            ).stdout.strip()
        )
        self.set_header_item(
            WindowTopBarItem(
                repo_name,
                [
                    SplitButtonSegment(30),
                    ButtonSegment("[<-]", lambda: self.move_in_jump_list(+1), 30),
                    ButtonSegment("[->]", lambda: self.move_in_jump_list(-1), 30),
                ],
                title_color=5,
            )
        )

        self.set_search_dialog(GitSearchDialogPopup(self.app))

    def add_commit(self, id, commit):
        if id in self.commits:
            return False
        self.commits[id] = commit
        return True

    def refresh_head(self):
        """Pull in commits made since the view last saw HEAD. In --graph mode the
        lane art is computed by git over the whole commit set, so a single new
        commit cannot be appended with correct art - reload the full log instead.
        Otherwise fetch just the new commits cheaply with `old..HEAD`."""
        new_head = Job.run_job(self.app, ["git", "rev-parse", "HEAD"]).stdout.strip()
        if self._graph_mode:
            self.head_id = new_head
            self.reload_commits()
            return
        if self.head_id:
            # The job's in-view guard needs the OLD head, so start it before
            # advancing head_id below.
            self.job_git_refresh_head.start_job(["--reverse", f"{self.head_id}..HEAD"])
        self.head_id = new_head
        self.check_uncommitted_changes()

    def reload_commits(self):
        # Re-select the same commit once it streams back in, so a reload (prefs
        # change, F5 in --graph mode, Shift+F5) does not dump the cursor at the top.
        self._pending_select_id = self.get_selected_commit_id()
        self.clear()
        self.job.start_job()
        self.check_uncommitted_changes()

    def show(self):
        _raise_split_sibling(self, self.app.git_diff)
        super().show()

    def set_selected(self, what: int | str | re.Pattern, visible_mode="center") -> bool:
        ret = super().set_selected(what, visible_mode)
        if self.app.screen.is_view_visible(self.app.git_diff):
            item = self.get_selected()
            if item:
                item.load_to_view()
        return ret

    def check_uncommitted_changes(self):
        """Probe the working tree / index and (re)place the pseudo-rows."""
        self._has_staged = (
            Job.run_job(self.app, ["git", "diff", "--cached", "--quiet"]).returncode
            != 0
        )
        self._has_working = (
            Job.run_job(self.app, ["git", "diff", "--quiet"]).returncode != 0
        )
        self._place_uncommitted_rows()

    def _place_uncommitted_rows(self):
        """Single source of truth for the uncommitted pseudo-rows: drop any that
        exist and reinsert the ones we need directly above the HEAD commit row
        (working above staged), keeping the cursor on the same screen line.
        Idempotent, so it can be re-fired whenever HEAD or the rows change."""
        selected_id = getattr(self.get_selected(), "id", None)
        old_selected = self._selected

        # Rebuild the list without the pseudo-rows.
        self.items = [
            it for it in self.items if not isinstance(it, UncommittedChangesListItem)
        ]

        # Insert directly above the HEAD row; fall back to the top of the
        # real-commit list when HEAD is not (yet) in view.
        first_commit = head_index = None
        for i, it in enumerate(self.items):
            if isinstance(it, CommitListItem):
                if first_commit is None:
                    first_commit = i
                if it.id == self.head_id:
                    head_index = i
                    break
        insert_at = head_index if head_index is not None else (first_commit or 0)

        # Mirror HEAD's graph art onto the rows only when HEAD is the topmost
        # commit, so they render as nodes in HEAD's lane; never splice a node into
        # unrelated lanes when HEAD is buried (e.g. --all). Empty when not in
        # --graph mode.
        graph_prefix = ""
        if head_index is not None and head_index == first_commit:
            graph_prefix = self.commits.get(self.head_id, {}).get("prefix", "")

        rows = []
        if self._has_working:
            rows.append(UncommittedChangesListItem(staged=False))
        if self._has_staged:
            rows.append(UncommittedChangesListItem(staged=True))
        for n, row in enumerate(rows):
            row.graph_prefix = graph_prefix
            row._view = self
            self.items.insert(insert_at + n, row)

        # Keep the previously-selected row on the same screen line. Real commits
        # keep their identity across the rebuild; pseudo-rows are recreated, so we
        # match by id (a selected pseudo-row that vanished falls through to clamp).
        new_index = next(
            (
                i
                for i, it in enumerate(self.items)
                if getattr(it, "id", None) == selected_id
            ),
            None,
        )
        if selected_id is not None and new_index is not None:
            self._selected = new_index
            self._offset_y = max(0, self._offset_y + (new_index - old_selected))
        if self.items:
            self._selected = max(0, min(self._selected, len(self.items) - 1))
        self.dirty = True

    def prepend_commit(self, item):
        """Insert a freshly-discovered real commit at the front of the
        real-commit sequence, below any leading uncommitted pseudo-rows."""
        insert_at = 0
        while insert_at < len(self.items) and isinstance(
            self.items[insert_at], UncommittedChangesListItem
        ):
            insert_at += 1
        item._view = self
        self.items.insert(insert_at, item)
        if insert_at <= self._selected:
            self._selected += 1
        if insert_at <= self._offset_y:
            self._offset_y += 1
        self.dirty = True

    def select_commit(self, id: str) -> typing.Optional[CommitListItem]:
        for idx, item in enumerate(self.items):
            if isinstance(item, CommitListItem) and id == item.id:
                self.set_selected(idx)
                return item
        return None

    def focus_head_if_pending(self):
        """Select HEAD once on startup as soon as both its id and its row are
        available. The id (from the refs job) and the commit rows (from the log
        job) arrive asynchronously, so this is called from both sides; whichever
        completes the pair wins, then the one-shot flag disables it."""
        if (
            self._focus_head_pending
            and self.head_id
            and self.select_commit(self.head_id)
        ):
            self._focus_head_pending = False
            # At this instant HEAD is the last row streamed in, so select_commit's
            # center clamp (min(sel - h/2, len - h)) pins it to the bottom of the
            # screen. Re-place it half a screen from the top WITHOUT clamping to
            # the rows loaded so far; the older commits stream in below and fill
            # the lower half, leaving HEAD centered. This must happen now, not only
            # when the log finishes loading: with `git log --all` on a huge repo
            # the load never finishes promptly, so deferring left HEAD stuck at the
            # bottom for the whole session.
            if self.height > 0:
                self._offset_y = max(0, self._selected - self.height // 2)
                self.dirty = True

    def select_if_pending(self, id: str):
        """Re-select a commit remembered across a reload (reload_commits), once its
        row streams back in. One-shot, so it clears itself after restoring."""
        if (
            self._pending_select_id
            and id == self._pending_select_id
            and self.select_commit(id)
        ):
            self._pending_select_id = ""

    def add_to_jump_list(
        self,
        commit_id: str,
        line: typing.Optional[int] = None,
        offset_y: typing.Optional[int] = None,
    ):
        # Drop any forward history; the current position is now at index 0, so
        # jump_index must be 0 regardless of which path we take below (resetting
        # it only after the insert left it stale on the dedup early-return).
        self.jump_list = self.jump_list[self.jump_index :]
        self.jump_index = 0
        entry = (commit_id, line, offset_y)
        if self.jump_list and self.jump_list[0] == entry:
            return
        self.jump_list.insert(0, entry)

    def move_in_jump_list(self, jump: int):
        if not self.jump_list:
            return True
        new_index = self.jump_index + jump
        if not (0 <= new_index < len(self.jump_list)):
            return True

        self.jump_index = new_index
        commit_id, line, offset_y = self.jump_list[new_index]
        is_local = commit_id.startswith("local-")

        # Locate the item in git_log; skip the entry if not found
        idx = None
        for i, item in enumerate(self.items):
            if (
                isinstance(item, (CommitListItem, UncommittedChangesListItem))
                and item.id == commit_id
            ):
                idx = i
                break
        if idx is None:
            self.move_in_jump_list(jump)
            return True

        if line is not None:
            self.app.git_diff.job.selected_line_map[commit_id] = (line, offset_y)

        was_same_commit = self.app.git_diff.commit_id == commit_id and (
            not self.app.git_diff.is_diff or is_local
        )

        # Move the git_log cursor without going through GitLogView.set_selected →
        # *ListItem.load_to_view → show_commit/show_diff, which would re-push to
        # the jumplist and clobber forward entries.
        super().set_selected(idx)

        if was_same_commit:
            if line is not None:
                self.app.git_diff.restore_view_position(line, offset_y)
        elif is_local:
            item = self.items[idx]
            self.app.git_diff.job.show_diff(
                "HEAD",
                cached=item._staged,
                title=item.txt,
                view_id=item.id,
                add_to_jump_list=False,
            )
        else:
            self.app.git_diff.job.show_commit(commit_id, add_to_jump_list=False)
        return True

    def get_selected_commit_id(self):
        selected_item = self.get_selected()
        # Pseudo-rows have no real sha; report none so callers never feed a
        # 'local-*' id into a git command.
        if selected_item and not isinstance(selected_item, UncommittedChangesListItem):
            return selected_item.id
        return ""

    def cherry_pick(self, commit_id=None):
        commit_id = commit_id or self.get_selected_commit_id()
        if not commit_id:
            self.app.log.warning("Select a commit to cherry-pick")
            return
        Job.run_job(self.app, ["git", "cherry-pick", "--abort"])
        self.app.run_git(
            ["git", "cherry-pick", "-m", "1", commit_id],
            ok=f"Commit {commit_id} cherry picked successfully",
            err="Error during cherry-pick",
            refresh_head=True,
            reload_refs=True,
        )

    def revert(self, commit_id=None):
        commit_id = commit_id or self.get_selected_commit_id()
        if not commit_id:
            self.app.log.warning("Select a commit to revert")
            return
        self.app.run_git(
            ["git", "revert", "--no-edit", "-m", "1", commit_id],
            ok=f"Commit {commit_id} reverted successfully",
            err="Error during revert",
            refresh_head=True,
            reload_refs=True,
        )

    def confirm_reset(self, commit_id=None):
        commit_id = commit_id or self.get_selected_commit_id()
        if not commit_id or commit_id.startswith("local"):
            self.app.log.warning("Select a commit to reset the current branch to")
            return
        self.view_reset.open(commit_id)

    def reset(self, mode, commit_id=None):
        commit_id = commit_id or self.get_selected_commit_id()
        # refresh_head so --graph mode reloads the log (HEAD moved); it also
        # re-probes uncommitted changes, so check_uncommitted is not needed.
        self.app.run_git(
            ["git", "reset", mode, commit_id],
            ok=f"{mode[2:].capitalize()} reset to {commit_id[:8]}",
            err=f"Error during {mode} reset",
            refresh_head=True,
            reload_refs=True,
        )

    def clean_uncommitted_changes(self, staged: bool = False):
        if staged:
            # Unstage everything (index -> HEAD), keeping the working tree. This
            # replaces a stash --keep-index / reset --hard / stash pop dance that
            # was exactly equivalent but carried a reset --hard data-loss window
            # (and a stash-pop conflict risk) between its steps.
            result = Job.run_job(self.app, ["git", "reset"])
        else:
            result = Job.run_job(self.app, ["git", "restore", "."])
        if result.returncode == 0:
            self.app.git_refs.reload_refs()
            self.app.git_log.check_uncommitted_changes()
        else:
            self.app.log.error(
                f"Error cleaning {'staged' if staged else 'unstaged'} changes: {result.stderr}"
            )

    def mark_commit(self, commit_id=None):
        commit_id = commit_id or self.get_selected_commit_id()
        self.marked_commit_id = commit_id
        self.dirty = True

    def diff_commits(self, old_commit_id, new_commit_id):
        self.app.git_diff.job.show_diff(old_commit_id, new_commit_id)
        self.app.git_diff.show()

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == ord("q"):
            self.app.exit_program()
        elif key == curses.KEY_EXIT:
            if self.app.split.split_active():
                self.app.split.set_split_mode(
                    "off"
                )  # Esc on the log pane leaves split view
            else:
                self.app.exit_program()
        elif key == ord("b"):
            self.app.git_refs.view_new_ref.create_ref(self.get_selected_commit_id())
        elif key in (ord("r"), ord("R")):
            self.confirm_reset()
        elif key == ord("c"):
            self.cherry_pick()
        elif key == ord("v"):
            self.revert()
        elif key == ord("m"):
            self.mark_commit()
        elif key == ord("M"):
            self.select_commit(self.marked_commit_id)
        else:
            return super().handle_input(keyboard)
        return True
