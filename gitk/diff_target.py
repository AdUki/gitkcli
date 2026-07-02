"""DiffTarget: what the diff pane shows, modelled explicitly.

The diff pane can show a commit, a two-revision range, an uncommitted
worktree/index diff, or an annotated tag body. Each is a frozen dataclass
owning its own git invocation, header title, view-identity key (used to
detect "already showing this"), whether scroll position is tracked across
revisits, and the blame base revision for jump-to-origin. Pure: no curses/app
imports, so it is unit-testable and safe to import from anywhere.
"""

from __future__ import annotations

import dataclasses
import typing

# Single source for the worktree pseudo-rows' id/title, shared by
# UncommittedChangesListItem (the log row) and WorktreeTarget (the diff header).
LOCAL_STAGED_ID = "local-staged"
LOCAL_WORKING_ID = "local-working"
STAGED_TITLE = "Uncommitted changes (staged)"
WORKING_TITLE = "Uncommitted changes (working directory)"


@dataclasses.dataclass(frozen=True)
class DiffOptions:
    """Display options that parametrize the git invocation. Snapshotted fresh
    at every (re)load so --stat gets the current view width."""

    context_size: int
    stat_width: int
    rename_limit: int
    ignore_whitespace: bool

    def diff_flags(self) -> typing.List[str]:
        flags = [
            f"-U{self.context_size}",
            f"--stat={self.stat_width}",
            "--no-color",
            f"-l{self.rename_limit}",
        ]
        if self.ignore_whitespace:
            flags.append("-w")
        return flags


@dataclasses.dataclass(frozen=True)
class CommitTarget:
    """A single commit, shown via 'git show -m'. Position-tracked."""

    commit_id: str
    tracks_position: typing.ClassVar[bool] = True

    @property
    def view_key(self) -> str:
        return self.commit_id

    def title(self) -> str:
        return f"Commit {self.commit_id[:7]}"

    def git_args(self, opts: DiffOptions) -> typing.List[str]:
        return ["show", "-m", self.commit_id, *opts.diff_flags()]

    def blame_revision(self) -> typing.Optional[str]:
        return f"{self.commit_id}^"


@dataclasses.dataclass(frozen=True)
class RangeTarget:
    """A two-revision diff via 'git diff OLD NEW'. Never position-tracked:
    revisiting the same range is not detected as "already shown"."""

    old_commit_id: str
    new_commit_id: str
    tracks_position: typing.ClassVar[bool] = False

    @property
    def view_key(self) -> str:
        return self.old_commit_id

    def title(self) -> str:
        return f"Diff {self.old_commit_id[:7]} {self.new_commit_id[:7]}"

    def git_args(self, opts: DiffOptions) -> typing.List[str]:
        args = ["diff", self.old_commit_id]
        # A falsy new_commit_id (e.g. the context menu's unguarded "Diff this
        # -> selected" when the log cursor sits on the uncommitted-changes
        # pseudo-row, whose id resolves to "") degrades to a single-revision
        # diff against the working tree rather than passing git a bad
        # revision arg.
        if self.new_commit_id:
            args.append(self.new_commit_id)
        args.extend(opts.diff_flags())
        return args

    def blame_revision(self) -> typing.Optional[str]:
        return self.old_commit_id


@dataclasses.dataclass(frozen=True)
class WorktreeTarget:
    """Uncommitted changes (working tree or index) via 'git diff [--cached]
    HEAD'. Position-tracked."""

    staged: bool
    tracks_position: typing.ClassVar[bool] = True

    @property
    def view_key(self) -> str:
        return LOCAL_STAGED_ID if self.staged else LOCAL_WORKING_ID

    def title(self) -> str:
        return STAGED_TITLE if self.staged else WORKING_TITLE

    def git_args(self, opts: DiffOptions) -> typing.List[str]:
        args = ["diff"]
        if self.staged:
            args.append("--cached")
        args.append("HEAD")
        args.extend(opts.diff_flags())
        return args

    def blame_revision(self) -> typing.Optional[str]:
        return "HEAD"


@dataclasses.dataclass(frozen=True)
class TagTarget:
    """An annotated tag's body via 'git cat-file -p'. Display options do not
    apply; never position-tracked."""

    tag_id: str
    tracks_position: typing.ClassVar[bool] = False

    @property
    def view_key(self) -> str:
        return self.tag_id

    def title(self) -> str:
        return f"Tag {self.tag_id}"

    def git_args(self, opts: DiffOptions) -> typing.List[str]:
        return ["cat-file", "-p", self.tag_id]

    def blame_revision(self) -> typing.Optional[str]:
        return None


DiffTarget = typing.Union[CommitTarget, RangeTarget, WorktreeTarget, TagTarget]
