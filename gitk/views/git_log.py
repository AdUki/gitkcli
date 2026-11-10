"""GitLogView: the commit log pane."""

from __future__ import annotations

import curses
import os
import re
import shlex
import typing

from tuikit.list_view import ListView
from tuikit.segmented import WindowTopBarItem
from tuikit.segments import ButtonSegment, RunsSegment
from tuikit.split import SplitButtonSegment
from tuikit.view import MODE_FULLSCREEN

from gitk.diff_target import LOCAL_STAGED_ID, LOCAL_WORKING_ID
from gitk.dialogs import GitSearchDialogPopup, ResetDialogPopup
from gitk.graph import CUT, DOT, CommitGraph
from gitk.ids import ID_GIT_LOG
from gitk.jobs import GitLogJob, GitRefreshHeadJob, GitSearchJob, Job
from gitk.keymap import handle_copy_key
from gitk.screen import Screen
from gitk.segmented_items import CommitListItem, UncommittedChangesListItem


def _split_pathspec(args: typing.List[str]):
    """Split git arguments at the first `--`: (options and revisions, paths),
    paths being None when there is no `--`."""
    if "--" in args:
        i = args.index("--")
        return args[:i], args[i + 1 :]
    return list(args), None


class GraphRows:
    """The log's rows in --graph mode: a read-only sequence view of the
    commit graph's display order, standing in for ListView.items. Commits are
    filed into the graph in whatever order git streams them and may land
    anywhere, so rows are looked up by number on demand instead of being
    inserted into a list. Row items are created lazily and cached by id, so a
    commit keeps one item (and its identity) wherever it moves."""

    def __init__(self, view: "GitLogView", graph: CommitGraph):
        self.view = view
        self.graph = graph
        self._items: typing.Dict[str, typing.Any] = {}

    def item(self, id: str):
        it = self._items.get(id)
        if it is None:
            if id in self.graph.fake:
                it = UncommittedChangesListItem(staged=id == LOCAL_STAGED_ID)
            else:
                it = CommitListItem(id)
            it._view = self.view
            self._items[id] = it
        return it

    def forget(self, id: str):
        self._items.pop(id, None)

    def __len__(self) -> int:
        return self.graph.num_rows

    def __bool__(self) -> bool:
        return self.graph.num_rows > 0

    def __getitem__(self, i):
        n = self.graph.num_rows
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(n))]
        if i < 0:
            i += n
        id = self.graph.commit_on_row(i)
        if id is None:
            raise IndexError(i)
        return self.item(id)

    def __iter__(self):
        for r in range(self.graph.num_rows):
            yield self[r]

    def index(self, item) -> int:
        r = self.graph.row_of(getattr(item, "id", None))
        if r is None:
            raise ValueError(item)
        return r


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
        # --graph mode: the lanes are laid out by gitk.graph (a port of gitk's
        # arc model), which also orders the rows, so self.items is a GraphRows
        # over `graph`. None when not in graph mode. Recreated on each reload
        # (see clear); --graph may come from the command line or the prefs.
        self._graph_mode = "--graph" in git_args
        self.graph: typing.Optional[CommitGraph] = None
        self.graph_dot = DOT  # from the config's git_log.graph_dot
        # The commit the cursor is on and the row it was on when last drawn:
        # rows inserted above it move it down, and the cursor (and scroll
        # position) follow it, like gitk's targetid/targetrow.
        self._target_id = ""
        self._target_row = 0
        # Commit to re-select once it streams back in after a reload (keeps the
        # cursor where the user left it). One-shot, like _pending_focus_head.
        self._pending_select_id = ""
        self._pending_select_line = 0
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
        graph = self.graph
        if graph is not None:
            parents = commit["parents"]
            if "--first-parent" in self.job.args:
                parents = parents[:1]
            graph.add_commit(id, parents, commit["ctime"])
            self.dirty = True
            if graph.fake and self.head_id in parents:
                # HEAD gained a child after its pseudo-rows were placed: that
                # splits HEAD's arc and may move it, so re-place them.
                self._place_uncommitted_rows()
        return True

    def graph_finished(self):
        """The log finished streaming: rows near the end can be laid out for
        good now (gitk: viewcomplete)."""
        self.graph.complete = True
        self.graph.reset_layout()
        self.dirty = True

    def clear(self):
        super().clear()
        self._target_id = ""
        self._target_row = 0
        if self._graph_mode:
            self.graph = CommitGraph(date_mode="--date-order" in self.job.args)
            self.graph.dot = self.graph_dot
            self.items = GraphRows(self, self.graph)
        else:
            self.graph = None

    def graph_segments(self, id: str):
        """The graph art of row `id` as a RunsSegment (one colour run per
        lane), in a list: [] when not in graph mode or not on a row."""
        graph = self.graph
        if graph is None:
            return []
        row = graph.row_of(id)
        if row is None:
            return []
        text, owners = graph.render_row(row)
        if not text:
            return []
        on_click = lambda x: self.follow_cut_line(row, x, text, owners)  # noqa: E731
        runs = []
        start = 0
        for x in range(1, len(text) + 1):
            if x == len(text) or owners[x] != owners[start]:
                color = Screen.C_LANE + graph.lane_color(
                    owners[start], Screen.LANE_COLORS
                )
                runs.append((text[start:x], color))
                start = x
        return [RunsSegment(runs, on_click)]

    def _sync_target(self):
        """Keep the cursor on the commit it was on (and the screen scrolled
        with it) when rows were inserted or removed above it."""
        if self.graph is None or not self._target_id:
            return
        row = self.graph.row_of(self._target_id)
        if row is None or row == self._target_row:
            return
        delta = row - self._target_row
        self._selected = max(0, min(self._selected + delta, len(self.items) - 1))
        self._offset_y = max(0, self._offset_y + delta)
        self._target_row = row
        self.dirty = True

    def _remember_target(self):
        if self.graph is None:
            return
        item = self.get_selected()
        self._target_id = getattr(item, "id", "") or ""
        self._target_row = self._selected

    def draw(self):
        if self.graph is not None:
            self._sync_target()
            self.graph.layout(self._offset_y, self._offset_y + self.height)
        super().draw()
        self._remember_target()

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
        if self.graph is not None and self.head_id in self.commits:
            # The graph files new commits wherever they belong, so fetch just
            # the new ones, as below. The pseudo-rows hang off the old HEAD:
            # take them down until the new HEAD has streamed in.
            self._remove_fake_rows()
            self.job_refresh_head.start_job(
                [f"{self.head_id}..HEAD"], on_finished=self._place_uncommitted_rows
            )
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
        # ... on the same screen line (graph mode, see select_if_pending)
        self._pending_select_line = self._selected - self._offset_y
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
        if self.graph is not None:
            self._place_fake_rows()
            return
        selected_id = getattr(self.get_selected(), "id", None)
        old_selected = self._selected

        self._strip_pseudo_rows()
        insert_at = self._pseudo_insert_pos()

        rows = []
        if self._has_working:
            rows.append(UncommittedChangesListItem(staged=False))
        if self._has_staged:
            rows.append(UncommittedChangesListItem(staged=True))
        for n, row in enumerate(rows):
            row._view = self
            self.items.insert(insert_at + n, row)

        self._restore_selection(selected_id, old_selected)
        self.dirty = True

    def _remove_fake_rows(self):
        for id in (LOCAL_WORKING_ID, LOCAL_STAGED_ID):
            if self.graph.remove_fake_row(id):
                self.items.forget(id)

    def _place_fake_rows(self):
        """Graph mode: the pseudo-rows are fake commits in the graph (as in
        gitk), children of HEAD - staged above HEAD, working above that - so
        they sit directly above HEAD with real lanes. The cursor follows its
        commit through _sync_target; a vanished pseudo-row keeps the index."""
        # HEAD's block (the pseudo-rows and HEAD) at the top of the view stays
        # there, pseudo-rows included, as they come and go.
        at_top = self._head_block_top() == self._offset_y
        self._remove_fake_rows()
        parent = self.head_id
        if parent in self.graph:
            if self._has_staged and self.graph.insert_fake_row(LOCAL_STAGED_ID, parent):
                parent = LOCAL_STAGED_ID
            if self._has_working:
                self.graph.insert_fake_row(LOCAL_WORKING_ID, parent)
        if self.items:
            self._selected = max(0, min(self._selected, len(self.items) - 1))
        self._sync_target()
        top = self._head_block_top()
        if at_top and top is not None:
            self._offset_y = top
        self.dirty = True

    def _head_block_top(self) -> typing.Optional[int]:
        """Graph mode: the row of the topmost of HEAD and its pseudo-rows."""
        rows = [
            self.graph.row_of(id)
            for id in (LOCAL_WORKING_ID, LOCAL_STAGED_ID, self.head_id)
            if id
        ]
        rows = [r for r in rows if r is not None]
        return min(rows) if rows else None

    def follow_cut_line(self, row: int, x: int, text: str, owners) -> bool:
        """A double click at column `x` of the graph art of `row`: on a cut
        line (┆), select the row with the line's other dashed end - as a jump,
        so the jump list can come back. Elsewhere it is not ours (False), and
        the double click opens the row as usual."""
        if not (0 <= x < len(text)) or text[x] != CUT or not owners[x]:
            return False
        target = self.graph.cut_line_target(row, owners[x])
        if target is None:
            return False
        point = self.app.jump_list.capture()
        self.set_selected(target)
        self._remember_target()
        self.app.jump_list.record(point)
        return True

    def _strip_pseudo_rows(self):
        """Rebuild self.items without the uncommitted pseudo-rows."""
        self.items = [
            it for it in self.items if not isinstance(it, UncommittedChangesListItem)
        ]

    def _pseudo_insert_pos(self):
        """Where to insert the pseudo-rows: directly above the HEAD row, or at
        the top of the real-commit list while HEAD is not (yet) in view."""
        first_commit = head_index = None
        for i, it in enumerate(self.items):
            if isinstance(it, CommitListItem):
                if first_commit is None:
                    first_commit = i
                if it.id == self.head_id:
                    head_index = i
                    break
        return head_index if head_index is not None else (first_commit or 0)

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
        # Also at the top row: a new commit lands above the view rather than
        # shifting what is on screen.
        if insert_at <= self._offset_y:
            self._offset_y += 1
        self.dirty = True

    def select_commit(self, id: str) -> typing.Optional[CommitListItem]:
        if self.graph is not None:
            row = self.graph.row_of(id) if id not in self.graph.fake else None
            if row is None:
                return None
            self.set_selected(row)
            self._remember_target()
            return self.items[row]
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
            if self.graph is not None:
                # Graph mode files commits where they belong as they stream
                # in, so put HEAD (and the uncommitted rows over it) at the
                # top of the view: newer commits are inserted above, out of
                # view, and older ones appended below, while the cursor and
                # screen follow HEAD (_sync_target).
                self._offset_y = self._head_block_top()
                self.dirty = True
                return
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
            if self.graph is not None:
                # back on the screen line it was on before the reload
                self._offset_y = max(0, self._selected - self._pending_select_line)
                self.dirty = True

    def find_row(self, view_key: str) -> typing.Optional[int]:
        """Index of the row `view_key` names - a commit sha, or an uncommitted
        pseudo-row id - or None when it is not (or no longer) loaded."""
        if self.graph is not None:
            return self.graph.row_of(view_key)
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

    def handle_key(self, event):
        # Rows may have moved under the cursor since the last frame.
        self._sync_target()
        key = event.key
        if key == ord("q"):
            self.app.exit_program()
        elif key == curses.KEY_EXIT:
            if self.app.split.split_active():
                self.app.split.unsplit()  # Esc on the log pane leaves split view
            else:
                self.app.exit_program()
        elif key in (ord("g"), ord("G"), curses.KEY_HOME, curses.KEY_END):
            self.app.jump_list.record()
            return super().handle_key(event)
        elif self.app.keymap.action_for("log", key):
            self.run_action(self.app.keymap.action_for("log", key))
        elif not handle_copy_key(self, key):
            return super().handle_key(event)
        return True

    def run_action(self, action_id):
        """Run a "log" keymap action on the selected commit."""
        commit_id = self.get_selected_commit_id()
        new_ref = self.app.git_refs.new_ref_dialog.create_ref
        if action_id == "log.new_branch":
            new_ref(commit_id)
        elif action_id == "log.new_tag":
            new_ref(commit_id, "tag")
        elif action_id == "log.cherry_pick":
            self.cherry_pick()
        elif action_id == "log.revert":
            self.revert()
        elif action_id == "log.reset":
            self.confirm_reset()
        elif action_id in ("log.diff_to_marked", "log.diff_from_marked"):
            self.diff_with_mark(action_id == "log.diff_from_marked")
        elif action_id == "log.mark":
            self.mark_commit()
        elif action_id == "log.return_to_mark":
            self.jump_to_commit(self.marked_commit_id)

    def diff_with_mark(self, from_mark=False):
        """Diff the selected commit against the marked one: selected --> mark,
        or mark --> selected with `from_mark`."""
        commit_id = self._resolve_commit(None, "diff against the mark")
        if not commit_id:
            return
        if not self.marked_commit_id:
            self.app.screen.show_flash("No marked commit to diff against")
            return
        pair = [commit_id, self.marked_commit_id]
        self.diff_commits(*(reversed(pair) if from_mark else pair))
