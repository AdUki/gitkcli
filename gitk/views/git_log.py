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
from gitk.screen import Screen
from gitk.segmented_items import (
    CommitListItem,
    UncommittedChangesListItem,
    WindowTopBarItem,
)
from gitk.segments import ButtonSegment, SplitButtonSegment
from gitk.split_layout import SPLIT_OFF
from gitk.view import MODE_FULLSCREEN


def _split_pathspec(args: typing.List[str]):
    """Split git arguments at the first `--`: (options and revisions, paths),
    paths being None when there is no `--`."""
    if "--" in args:
        i = args.index("--")
        return args[:i], args[i + 1 :]
    return list(args), None


class GitLogView(ListView):
    def __init__(self, app, git_args: typing.List, cmd_args: typing.List):
        super().__init__(app, ID_GIT_LOG, MODE_FULLSCREEN)
        self.commits = {}  # map: git_id --> { parents, date, author, title }

        self.marked_commit_id = ""
        self.head_branch = ""
        # Every commit HEAD has pointed at while the app ran (see the head_id
        # setter); search_args turns the loaded ones into search revisions.
        self._seen_heads: typing.Dict[str, None] = {}  # insertion-ordered set
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
        # cursor where the user left it). One-shot, like _pending_focus_head.
        self._pending_select_id = ""
        # One-shot: scroll to HEAD the first time it can be located after launch,
        # so it is visible even when it is not at the top (uncommitted rows above
        # it, an unrelated revision arg, or a detached/old HEAD deep in the list).
        self._pending_focus_head = True

        self.show_commit_id = True
        self.show_commit_date = True
        self.show_commit_author = True

        self._cli_args = git_args + cmd_args
        self.pref_flags = ""
        self.job = GitLogJob(self.app, ID_GIT_LOG, list(self._cli_args))
        self.job_refresh_head = GitRefreshHeadJob(self.app)
        self.job_search = GitSearchJob(self.app)
        self.reset_dialog = ResetDialogPopup(app)

    @property
    def head_id(self) -> str:
        return self._head_id

    @head_id.setter
    def head_id(self, id: str):
        self._head_id = id
        if id:
            self._seen_heads[id] = None

    def search_args(self, args: typing.List[str]) -> typing.List[str]:
        """Arguments for the `git log` behind a non-[Txt] search: the view's own
        arguments (CLI + preference flags, so e.g. an `--all` in the prefs is
        searched too) plus `args`, the per-search options and an optional
        `-- <pathspec>`.

        Without an explicit revision the view shows the commits reachable from
        HEAD *as it was when the log loaded*, and F5 / a checkout / a reset then
        add commits reachable from each new HEAD. A bare `git log` would only
        search from the current HEAD, so after HEAD moved (to another branch, or
        back with a reset) the commits still shown above or beside it could
        never be found. So also name every HEAD the loaded commits came from as
        an extra revision. Only the loaded ones: a HEAD outside the view (e.g.
        the log was opened on another branch) would merely widen the search to
        commits matches() discards anyway, and a diff search over them is slow.
        """
        view_opts, view_paths = _split_pathspec(
            [a for a in self.job.args if a != "--graph"]
        )
        search_opts, search_paths = _split_pathspec(args)
        heads = [h for h in self._seen_heads if h in self.commits]
        # A search pathspec is more specific than the view's; matches() keeps
        # the result inside the view either way.
        paths = search_paths if search_paths is not None else view_paths
        return (
            view_opts
            + heads
            + search_opts
            + (["--"] + paths if paths is not None else [])
        )

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
                    SplitButtonSegment(Screen.C_TITLE),
                    ButtonSegment(
                        "[<-]", lambda: self.app.jump_list.move(+1), Screen.C_TITLE
                    ),
                    ButtonSegment(
                        "[->]", lambda: self.app.jump_list.move(-1), Screen.C_TITLE
                    ),
                ],
                title_color=Screen.C_DATA,
            )
        )

        self.set_search_dialog(GitSearchDialogPopup(self.app))

    def add_commit(self, id, commit):
        if id in self.commits:
            return False
        self.commits[id] = commit
        return True

    def refresh_head(self):
        """Pull in commits made since the view last saw HEAD. When HEAD merely
        moved to a commit that is already loaded (checkout, reset to an existing
        commit) there is nothing to fetch - and in --graph mode the lane art is
        unchanged because the commit set is unchanged - so just move the HEAD
        marker and re-place the uncommitted pseudo-rows. In --graph mode a
        genuinely new commit cannot be appended with correct art, so reload the
        full log instead. Otherwise fetch just the new commits cheaply with
        `old..HEAD`."""
        new_head = Job.run_job(self.app, ["git", "rev-parse", "HEAD"]).stdout.strip()
        if new_head and new_head in self.commits:
            self.head_id = new_head
            self.check_uncommitted_changes()
            return
        if self._graph_mode:
            self.head_id = new_head
            self.reload_commits()
            return
        if self.head_id:
            # The job's in-view guard needs the OLD head, so start it before
            # advancing head_id below.
            self.job_refresh_head.start_job(["--reverse", f"{self.head_id}..HEAD"])
        self.head_id = new_head
        self.check_uncommitted_changes()

    def reload_commits(self):
        # Re-select the same commit once it streams back in, so a reload (prefs
        # change, a new commit in --graph mode, Shift+F5) does not dump the
        # cursor at the top.
        self._pending_select_id = self.get_selected_commit_id()
        self.clear()
        # The silent up-front phase can take a long time on a big repo
        # (--graph implies --topo-order, computed over the whole set before
        # git emits the first line): show the in-progress bar until the first
        # row streams in (cleared in GitLogJob.process_item; on_finished is
        # the fallback for an empty or failed load). Also covers the start-up
        # path (launch_curses) - the initial load is the same job.
        self.app.screen.show_working("Working: loading commits ...", dim=False)
        self.job.start_job(on_finished=self.app.screen.clear_working)
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
        self._has_staged = self._diff_reports_changes(["--cached"])
        self._has_working = self._diff_reports_changes([])
        self._place_uncommitted_rows()

    def _diff_reports_changes(self, args):
        """Whether `git diff --quiet` found differences. Exit 1 means "differs"
        and 0 means clean; anything above that is git itself failing (128/129
        outside a repository), which must not be read as a dirty tree."""
        return Job.run_job(self.app, ["git", "diff", "--quiet"] + args).returncode == 1

    def _place_uncommitted_rows(self):
        """Single source of truth for the uncommitted pseudo-rows: drop any that
        exist and reinsert the ones we need directly above the HEAD commit row
        (working above staged), keeping the cursor on the same screen line.
        Idempotent, so it can be re-fired whenever HEAD or the rows change."""
        selected_id = getattr(self.get_selected(), "id", None)
        old_selected = self._selected

        self._strip_pseudo_rows()
        insert_at, graph_prefix = self._pseudo_insert_pos()

        rows = []
        if self._has_working:
            rows.append(UncommittedChangesListItem(staged=False))
        if self._has_staged:
            rows.append(UncommittedChangesListItem(staged=True))
        for n, row in enumerate(rows):
            row.graph_prefix = graph_prefix
            row._view = self
            self.items.insert(insert_at + n, row)

        self._restore_selection(selected_id, old_selected)
        self.dirty = True

    def _strip_pseudo_rows(self):
        """Rebuild self.items without the uncommitted pseudo-rows."""
        self.items = [
            it for it in self.items if not isinstance(it, UncommittedChangesListItem)
        ]

    def _pseudo_insert_pos(self):
        """Where to insert the pseudo-rows, and the graph-art prefix to mirror
        onto them. Insert directly above the HEAD row; fall back to the top of
        the real-commit list when HEAD is not (yet) in view. Mirror HEAD's
        graph art onto the rows only when HEAD is the topmost commit, so they
        render as nodes in HEAD's lane; never splice a node into unrelated
        lanes when HEAD is buried (e.g. --all). Empty when not in --graph mode.
        """
        first_commit = head_index = None
        for i, it in enumerate(self.items):
            if isinstance(it, CommitListItem):
                if first_commit is None:
                    first_commit = i
                if it.id == self.head_id:
                    head_index = i
                    break
        insert_at = head_index if head_index is not None else (first_commit or 0)

        graph_prefix = ""
        if head_index is not None and head_index == first_commit:
            graph_prefix = self.commits.get(self.head_id, {}).get("prefix", "")

        return insert_at, graph_prefix

    def _restore_selection(self, selected_id, old_selected):
        """Keep the previously-selected row on the same screen line. Real
        commits keep their identity across the rebuild; pseudo-rows are
        recreated, so we match by id (a selected pseudo-row that vanished
        falls through to clamp)."""
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
            self._pending_focus_head
            and self.head_id
            and self.select_commit(self.head_id)
        ):
            self._pending_focus_head = False
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

    def find_row(self, view_key: str) -> typing.Optional[int]:
        """Index of the row `view_key` names - a commit sha, or an uncommitted
        pseudo-row id - or None when it is not (or no longer) loaded."""
        return next(
            (
                i
                for i, item in enumerate(self.items)
                if isinstance(item, (CommitListItem, UncommittedChangesListItem))
                and item.id == view_key
            ),
            None,
        )

    def select_row(self, index: int):
        """Move the cursor without loading the row into the diff pane, for the
        jump list - which drives that pane itself."""
        super().set_selected(index)

    def jump_to_commit(self, commit_id: str) -> bool:
        """Select `commit_id` as an explicit jump (M, "Return to mark", a ref
        activation), so the jump list can bring the cursor back here."""
        point = self.app.jump_list.capture()
        if not self.select_commit(commit_id):
            return False
        self.app.jump_list.record(point)
        return True

    def search(self, backward: bool = False, repeat: bool = False) -> bool:
        """A search that lands on a match is a jump: record where it started."""
        point = self.app.jump_list.capture()
        if not super().search(backward, repeat):
            return False
        self.app.jump_list.record(point)
        return True

    def get_selected_commit_id(self):
        selected_item = self.get_selected()
        # Pseudo-rows have no real sha; report none so callers never feed a
        # 'local-*' id into a git command.
        if selected_item and not isinstance(selected_item, UncommittedChangesListItem):
            return selected_item.id
        return ""

    def _resolve_commit(self, commit_id, action_msg, reject_local=False):
        """`commit_id` or the current selection; None (after warning the user)
        if neither yields a usable commit for `action_msg`."""
        commit_id = commit_id or self.get_selected_commit_id()
        if not commit_id or (reject_local and commit_id.startswith("local")):
            self.app.log.warning(f"Select a commit to {action_msg}")
            return None
        return commit_id

    def cherry_pick(self, commit_id=None):
        commit_id = self._resolve_commit(commit_id, "cherry-pick")
        if not commit_id:
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
        commit_id = self._resolve_commit(commit_id, "revert")
        if not commit_id:
            return
        self.app.run_git(
            ["git", "revert", "--no-edit", "-m", "1", commit_id],
            ok=f"Commit {commit_id} reverted successfully",
            err="Error during revert",
            refresh_head=True,
            reload_refs=True,
        )

    def confirm_reset(self, commit_id=None):
        commit_id = self._resolve_commit(
            commit_id, "reset the current branch to", reject_local=True
        )
        if not commit_id:
            return
        self.reset_dialog.open(commit_id)

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
            args = ["git", "reset"]
        else:
            args = ["git", "restore", "."]
        self.app.run_git(
            args,
            err=f"Error cleaning {'staged' if staged else 'unstaged'} changes",
            reload_refs=True,
            check_uncommitted=True,
        )

    def mark_commit(self, commit_id=None):
        commit_id = commit_id or self.get_selected_commit_id()
        self.marked_commit_id = commit_id
        self.dirty = True

    def diff_commits(self, old_commit_id, new_commit_id):
        self.app.git_diff.show_diff(old_commit_id, new_commit_id)
        self.app.git_diff.show()

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == ord("q"):
            self.app.exit_program()
        elif key == curses.KEY_EXIT:
            if self.app.split.split_active():
                self.app.split.set_split_mode(
                    SPLIT_OFF
                )  # Esc on the log pane leaves split view
            else:
                self.app.exit_program()
        elif key == ord("b"):
            self.app.git_refs.new_ref_dialog.create_ref(self.get_selected_commit_id())
        elif key in (ord("r"), ord("R")):
            self.confirm_reset()
        elif key == ord("c"):
            self.cherry_pick()
        elif key == ord("v"):
            self.revert()
        elif key == ord("m"):
            self.mark_commit()
        elif key == ord("M"):
            self.jump_to_commit(self.marked_commit_id)
        elif key in (ord("g"), ord("G"), curses.KEY_HOME, curses.KEY_END):
            self.app.jump_list.record()
            return super().handle_input(keyboard)
        else:
            return super().handle_input(keyboard)
        return True
