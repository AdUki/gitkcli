"""Staging from the uncommitted-changes view, the git gui way.

The view (GitDiffView showing a WorktreeTarget) shows one of the two
uncommitted-changes pseudo-rows: the working directory (index -> working
tree, untracked files as new files) or the staged changes (HEAD -> index).
Its rows keep the lines exactly as git printed them (`raw`), so a patch for
any part of them can be rebuilt and fed to `git apply`:

- stage:   the unstaged patch, applied to the index      (apply --cached)
- unstage: the staged patch, reversed onto the index     (apply --cached -R)
- discard: the unstaged patch, reversed onto the files   (apply -R)

What an action covers depends on where it is aimed: the selected lines (a
text selection, or the cursor line), else the hunk under the cursor, the file
(on its header or diffstat row), or all the changes (on the diffstat
summary, above the files).
Whole files and sections use the plain commands (git add / reset / restore /
clean), which also handle binary files.

The patch building (`partial_hunk`, `file_header`) is pure; the rest reads
the view's rows and runs git.
"""

from __future__ import annotations

import re
import typing

from gitk.diff_target import SECTION_STAGED, SECTION_UNSTAGED
from gitk.items import DiffListItem, StatListItem
from gitk.jobs import Job

STAGE = "stage"
UNSTAGE = "unstage"
DISCARD = "discard"

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


# -- pure patch building ------------------------------------------------------


def partial_hunk(
    header: str,
    lines: typing.Sequence[str],
    selected: typing.Optional[typing.Collection[int]] = None,
    reverse: bool = False,
) -> typing.Optional[typing.List[str]]:
    """The hunk `header` + `lines` cut down to the `selected` changed lines
    (indexes into `lines`; None = all of them), as a list of patch lines
    starting with the recomputed "@@" header. None when no change is left.

    A change left out must not happen, so it becomes what the target of the
    patch already has: applied forward (stage), the target is the old side,
    so an unselected '-' stays as context and an unselected '+' goes; applied
    in reverse (unstage, discard), the target is the new side, so an
    unselected '+' stays as context and an unselected '-' goes."""
    m = _HUNK_RE.match(header)
    if not m:
        return None
    out = []
    old = new = 0
    changed = False
    kept = True  # whether the line a "\ No newline" note follows was kept
    skip_note = False
    for i, line in enumerate(lines):
        marker = line[:1]
        chosen = selected is None or i in selected
        if marker == "\\":
            if kept and not skip_note:
                out.append(line)
            skip_note = False
            continue
        if (
            marker == "-"
            and not chosen
            and not reverse
            and _last_without_newline(lines, i)
            and _adds_after(lines, i, selected)
        ):
            # The old file's last line, missing its newline, kept while lines
            # are added after it: as context it would stay unterminated and
            # run into them. It has to gain its newline - the pair below -
            # for the added lines to follow it.
            out += [line, lines[i + 1], "+" + line[1:]]
            old += 1
            new += 1
            skip_note = True
            continue
        if marker == "+":
            if chosen:
                out.append(line)
                new += 1
                changed = kept = True
            elif reverse:
                out.append(" " + line[1:])
                old += 1
                new += 1
                kept = True
            else:
                kept = False
        elif marker == "-":
            if chosen:
                out.append(line)
                old += 1
                changed = kept = True
            elif reverse:
                kept = False
            else:
                out.append(" " + line[1:])
                old += 1
                new += 1
                kept = True
        else:  # context ("" when git's trailing space got lost)
            out.append(line or " ")
            old += 1
            new += 1
            kept = True
    if not changed:
        return None
    old_start = _start(int(m.group(1)), int(m.group(2) or 1), old)
    new_start = _start(int(m.group(3)), int(m.group(4) or 1), new)
    return [f"@@ -{old_start},{old} +{new_start},{new} @@{m.group(5)}"] + out


def _last_without_newline(lines, i: int) -> bool:
    """Whether line `i` is followed by the "\\ No newline at end of file"
    note."""
    return i + 1 < len(lines) and lines[i + 1].startswith("\\")


def _adds_after(lines, i: int, selected) -> bool:
    """Whether an added line after line `i` is going into the patch."""
    return any(
        lines[k][:1] == "+" and (selected is None or k in selected)
        for k in range(i + 1, len(lines))
    )


def _start(start: int, count: int, new_count: int) -> int:
    """A side's start line for its new line count: an empty side names the
    line *before* the gap (unified diff convention)."""
    if count == 0 and new_count > 0:
        return start + 1
    if count > 0 and new_count == 0:
        return start - 1
    return start


def _swap_prefix(path: str, old: str, new: str) -> str:
    """'b/x' -> 'a/x' (also quoted: '"b/x"' -> '"a/x"')."""
    if path.startswith('"' + old):
        return '"' + new + path[len(old) + 1 :]
    if path.startswith(old):
        return new + path[len(old) :]
    return path


def file_header(
    header: typing.Sequence[str], partial: bool, reverse: bool
) -> typing.List[str]:
    """The file header lines for a patch of only part of the file's changes.

    A new file whose lines are only partly unstaged/discarded keeps some of
    them, so the patch must describe a modification of the file, not its
    creation (likewise a deleted file only partly staged); the index line's
    hashes describe the whole change, so it goes too."""
    if not partial:
        return list(header)
    new_file = any(h.startswith("new file mode") for h in header)
    deleted = any(h.startswith("deleted file mode") for h in header)
    old_path = next((h[4:] for h in header if h.startswith("--- ")), "")
    new_path = next((h[4:] for h in header if h.startswith("+++ ")), "")
    out = []
    for h in header:
        if h.startswith("index "):
            continue
        if reverse and new_file:
            if h.startswith("new file mode"):
                continue
            if h.startswith("--- "):
                h = "--- " + _swap_prefix(new_path, "b/", "a/")
        if not reverse and deleted:
            if h.startswith("deleted file mode"):
                continue
            if h.startswith("+++ "):
                h = "+++ " + _swap_prefix(old_path, "a/", "b/")
        out.append(h)
    return out


def diff_git_path(line: str) -> typing.Optional[str]:
    """The path of a "diff --git a/P b/P" line naming one unquoted path on
    both sides (no rename); None otherwise."""
    if not line.startswith("diff --git a/"):
        return None
    rest = line[len("diff --git ") :]
    if (len(rest) - 5) % 2:
        return None
    path = rest[2 : 2 + (len(rest) - 5) // 2]
    if rest != f"a/{path} b/{path}":
        return None
    return path


# -- the view's rows ----------------------------------------------------------


def _raw(item) -> str:
    return getattr(item, "raw", None) or ""


def is_hunk_header(item) -> bool:
    return isinstance(item, DiffListItem) and _raw(item).startswith("@@")


def is_hunk_line(item) -> bool:
    """A line inside a hunk: a context/added/removed line, or the "\\ No
    newline at end of file" note."""
    if isinstance(item, DiffListItem):
        return not _raw(item).startswith("@@")
    return _raw(item).startswith("\\")


def is_change(item) -> bool:
    return isinstance(item, DiffListItem) and _raw(item)[:1] in ("+", "-")


def is_file_start(item) -> bool:
    return not isinstance(item, DiffListItem) and _raw(item).startswith("diff ")


class FileBlock:
    """One file's rows: its header rows and its hunks (each the "@@" row and
    the rows of its lines)."""

    def __init__(self, items, start: int):
        self.start = start
        self.header: typing.List[int] = []
        self.hunks: typing.List[typing.Tuple[int, typing.List[int]]] = []
        i = start
        while i < len(items) and (i == start or not _ends_file(items[i])):
            item = items[i]
            if is_hunk_header(item):
                self.hunks.append((i, []))
            elif self.hunks and is_hunk_line(item):
                self.hunks[-1][1].append(i)
            elif not self.hunks and _raw(item):
                self.header.append(i)
            else:
                break  # past the file (e.g. the untracked files' list)
            i += 1
        self.end = i

    def hunk_of(self, row: int):
        for hunk in self.hunks:
            if hunk[0] == row or row in hunk[1]:
                return hunk
        return None


def _ends_file(item) -> bool:
    return is_file_start(item)


def file_block(items, row: int) -> typing.Optional[FileBlock]:
    """The file whose rows include `row`; None outside any (the diffstat)."""
    i = row
    while i >= 0:
        if is_file_start(items[i]):
            block = FileBlock(items, i)
            return block if row < block.end else None
        i -= 1
    return None


def content_anchor(items, row: int):
    """Where `row` is, in terms that survive a reload of the view: its
    file's "diff" header line (None outside a file), its distance from that
    header, and its row number."""
    if not 0 <= row < len(items):
        return None
    for i in range(row, -1, -1):
        if is_file_start(items[i]):
            return _raw(items[i]), row - i, row
    return None, 0, row


def find_anchor(items, anchor) -> typing.Optional[int]:
    """The row `anchor` (see content_anchor) points at in `items`: as far
    from the same file's header, else (the file is gone, and what followed
    moved up into its place) the same row number."""
    if anchor is None or not items:
        return None
    header, from_header, row = anchor
    if header is not None:
        base = next((i for i, it in enumerate(items) if _raw(it) == header), None)
        if base is not None:
            row = base + from_header
    return max(0, min(row, len(items) - 1))


def build_patch(items, rows: typing.Iterable[int], reverse: bool) -> str:
    """A patch of the changed lines among `rows` (whole hunks when every
    line of them is in), "" when they hold no change."""
    chosen = sorted(set(r for r in rows if is_change(items[r])))
    blocks: typing.Dict[int, typing.Tuple[FileBlock, typing.Set[int]]] = {}
    for row in chosen:
        block = file_block(items, row)
        if block is not None:
            blocks.setdefault(block.start, (block, set()))[1].add(row)
    out = []
    for start in sorted(blocks):
        block, rows_in = blocks[start]
        all_changes = [
            r for _, lines in block.hunks for r in lines if is_change(items[r])
        ]
        partial = len(rows_in) < len(all_changes)
        hunks = []
        for head, lines in block.hunks:
            picked = {k for k, r in enumerate(lines) if r in rows_in}
            if not picked:
                continue
            hunk = partial_hunk(
                _raw(items[head]), [_raw(items[r]) for r in lines], picked, reverse
            )
            if hunk:
                hunks.extend(hunk)
        if hunks:
            out.extend(
                file_header([_raw(items[r]) for r in block.header], partial, reverse)
            )
            out.extend(hunks)
    return "".join(line + "\n" for line in out)


def block_patch(items, block: FileBlock, hunk=None) -> str:
    """The patch of a whole file, or of one of its hunks."""
    out = [_raw(items[r]) for r in block.header]
    for head, lines in block.hunks if hunk is None else [hunk]:
        out.append(_raw(items[head]))
        out.extend(_raw(items[r]) for r in lines)
    return "".join(line + "\n" for line in out)


# -- running ------------------------------------------------------------------


def section_for(action: str) -> str:
    return SECTION_STAGED if action == UNSTAGE else SECTION_UNSTAGED


class Stager:
    """Runs stage / unstage / discard for the uncommitted-changes view."""

    def __init__(self, view):
        self.view = view
        self.app = view.app

    # git ---------------------------------------------------------------------

    def _git(self, args, input=None, ok=None) -> bool:
        top = self.app.git_toplevel()
        argv = ["git", "-C", top] + args if top else ["git"] + args
        result = self.app.run_git(
            argv, ok=ok, err="Error", input=input, check_uncommitted=True, quiet=True
        )
        self.view.reload_worktree()
        return result.returncode == 0

    def _apply(self, patch: str, action: str, what: str):
        if not patch:
            self.app.screen.show_flash(f"Nothing to {action} here")
            return
        args = ["apply", "--recount", "--whitespace=nowarn"]
        if action != DISCARD:
            args.append("--cached")
        if action != STAGE:
            args.append("--reverse")
        if self.view.context_size == 0:
            args.append("--unidiff-zero")
        self._git(args + ["-"], input=patch, ok=f"{_done(action)} {what}")

    def _has_head(self) -> bool:
        return (
            Job.run_job(
                self.app, ["git", "rev-parse", "--verify", "-q", "HEAD"]
            ).returncode
            == 0
        )

    # actions -----------------------------------------------------------------

    def run(self, action: str, line_only: bool = False):
        """Run `action` on what the cursor / selection aims at (see the
        module docstring); `line_only` narrows a cursor action to its line."""
        view = self.view
        items = view.items
        if view.target.section != section_for(action):
            self._wrong_place(action)
            return
        rows = view.selected_rows()
        if rows is None and line_only:
            rows = (view._selected, view._selected)
        if rows is not None:
            chosen = [r for r in range(rows[0], rows[1] + 1) if is_change(items[r])]
            if not chosen:
                self.app.screen.show_flash(f"No changed line to {action} here")
                return
            what = "line" if len(chosen) == 1 else f"{len(chosen)} lines"
            self._confirm_discard(
                action,
                f"the selected {what}",
                lambda: self._apply(
                    build_patch(items, chosen, action != STAGE), action, what
                ),
            )
            view.clear_selection()
            return

        item = view.get_selected()
        if item is None:
            return
        row = view._selected
        if isinstance(item, StatListItem):
            row = item.file_row()
            block = file_block(items, row) if row is not None else None
            if block is not None:
                self._file(action, block)
            return
        block = file_block(items, row)
        if block is None:
            # Above the files (the diffstat summary): all of them.
            self._section(action)
            return
        hunk = block.hunk_of(row)
        if hunk is None:
            self._file(action, block)
            return
        patch = block_patch(items, block, hunk)
        self._confirm_discard(
            action,
            "this hunk",
            lambda: self._apply(patch, action, "hunk"),
        )

    def run_all(self):
        """All the shown changes: stage them (working directory) or unstage
        them (staged)."""
        self._section(UNSTAGE if self.view.target.staged else STAGE)

    def _wrong_place(self, action: str):
        where = "staged" if action == UNSTAGE else "working directory"
        self.app.screen.show_flash(
            f"Nothing to {action} here: open the {where} changes"
        )

    def _confirm_discard(self, action: str, what: str, run):
        """Discarding loses work: confirm it first."""
        if action != DISCARD:
            run()
            return
        self.app.confirm_dialog.confirm(
            " Discard changes",
            [f"Discard {what} from the working tree?", "This cannot be undone."],
            run,
            confirm_label="[Discard]",
        )

    def _file(self, action: str, block: FileBlock):
        items = self.view.items
        path = diff_git_path(_raw(items[block.start]))
        if path is None:
            # A rename or a quoted path: the patch of the whole file.
            patch = block_patch(items, block)
            self._confirm_discard(
                action,
                "this file's changes",
                lambda: self._apply(patch, action, "file"),
            )
            return
        ok = f"{_done(action)} {path}"
        if action == STAGE:
            self._git(["add", "-A", "--", path], ok=ok)
        elif action == UNSTAGE:
            if self._has_head():
                self._git(["reset", "-q", "--", path], ok=ok)
            else:
                self._git(["rm", "--cached", "-q", "--", path], ok=ok)
        else:
            header = [_raw(items[r]) for r in block.header]
            untracked = any(h.startswith("new file mode") for h in header)
            args = (
                ["clean", "-f", "-q", "--", path]
                if untracked
                else ["restore", "--", path]
            )
            self._confirm_discard(
                action,
                f"{'the untracked file' if untracked else 'the changes to'} {path}",
                lambda: self._git(args, ok=ok),
            )

    def _section(self, action: str):
        if action == STAGE:
            self._git(["add", "-A", "."], ok="Staged all changes")
        elif action == UNSTAGE:
            if self._has_head():
                self._git(["reset", "-q"], ok="Unstaged all changes")
            else:
                self._git(
                    ["rm", "-r", "--cached", "-q", "."], ok="Unstaged all changes"
                )
        else:
            self._confirm_discard(
                action,
                "all unstaged changes to tracked files (untracked files stay)",
                lambda: self._git(["restore", "--", "."], ok="Discarded all changes"),
            )


def _done(action: str) -> str:
    return {STAGE: "Staged", UNSTAGE: "Unstaged", DISCARD: "Discarded"}[action]
