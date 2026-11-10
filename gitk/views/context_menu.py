"""ContextMenu: the right-click / F7 popup menu."""

from __future__ import annotations

from tuikit.dialogs import Menu
from tuikit.items import MenuItem, SeparatorItem
from tuikit.keys import KeyEvent

from gitk.ids import ID_CONTEXT_MENU, ID_GIT_DIFF, ID_GIT_LOG, ID_GIT_REFS, ID_LOG
from gitk.diff_target import SECTION_UNSTAGED
from gitk.items import DiffListItem, StatListItem
from gitk.jobs import Job
from gitk.screen import Screen
from gitk.segmented_items import UncommittedChangesListItem
from gitk.stage import DISCARD, STAGE, UNSTAGE, file_block


class ContextMenu(Menu):
    def __init__(self, app):
        super().__init__(app, ID_CONTEXT_MENU)
        # F7 cycle state: (targets, view, row_x, row_y) and the index shown, or
        # None when no segment cycle is active. See start_cycle / advance_cycle.
        self._cycle = None
        self._cycle_index = -1

    def _append(self, label, action, args=(), enabled=True, shortcut="", key=None):
        self.append(MenuItem(label, action, args, enabled, shortcut, key))

    def _append_action(self, action_id, label, action, args=(), enabled=True):
        """A menu entry for a keymap action: shows its key on the right, and
        the key runs the entry while the menu is open."""
        keymap = self.app.keymap
        self._append(
            label, action, args, enabled, keymap.spec(action_id), keymap.key(action_id)
        )

    def _append_copy_items(self, view, item):
        """The line/range/all clipboard trio shared by the git-log, git-diff and
        log context menus (and the selection, when there is one)."""
        if view.has_selection():
            self._append_action(
                "copy.line",
                "Copy selection to clipboard",
                view.copy_selection_to_clipboard,
            )
        copy_line = "copy.line" if not view.has_selection() else ""
        self._append(
            "Copy line to clipboard",
            item.copy_text_to_clipboard,
            shortcut=self.app.keymap.spec(copy_line) if copy_line else "",
            key=self.app.keymap.key(copy_line) if copy_line else None,
        )
        self._append(
            "Copy range to clipboard", view.copy_text_range_to_clipboard, [item]
        )
        self._append_action(
            "copy.all", "Copy all to clipboard", view.copy_text_to_clipboard
        )

    def start_cycle(self, targets, view, row_x, row_y):
        """Begin the F7 segment cycle for a row. `targets` is the ordered
        (menu_item, view_id, x) list from get_context_menu_targets, `view` the
        originating view (so the menu's callbacks bind to it, not to this popup),
        and (row_x, row_y) the row's top-left on screen. Shows the first target."""
        self._cycle = (targets, view, row_x, row_y)
        self._cycle_index = -1
        self.advance_cycle()

    def advance_cycle(self):
        """Show the next menu in the active F7 cycle, wrapping past the last back
        to the first. No-op when no cycle is active."""
        if not self._cycle:
            return
        targets, view, row_x, row_y = self._cycle
        self._cycle_index = (self._cycle_index + 1) % len(targets)
        menu_item, view_id, segment_x = targets[self._cycle_index]
        self.show_context_menu(
            menu_item,
            view_id,
            view=view,
            force=True,
            at=(max(0, row_x + segment_x), row_y),
        )

    def show_context_menu(
        self, item, view_id: str = "", view=None, force=False, at=None
    ) -> bool:
        """Open the menu for `item` (as a row of `view_id`, default the active
        view's) with its corner at screen position `at` (default: the mouse).
        False when there is no menu for it."""
        if not force and self.app.screen.showed_views[-1] == self:
            return True
        # A plain (non-cycle) open clears any leftover cycle; force=True keeps it
        # so the next F7 advances rather than restarting.
        if not force:
            self._cycle = None
        self.clear()
        self._selected = -1
        if view is None:
            view = self.app.screen.get_active_view()
        if not view_id:
            view_id = view.id
        x, y = at if at is not None else self.app.mouse.position
        if item is self.app:  # main menu
            x, y = self._build_main_menu(item, view)
        elif view_id == ID_GIT_LOG and hasattr(item, "id"):
            self._build_log_menu(view, item)
        elif view_id == ID_GIT_DIFF:
            self._build_diff_menu(view, item)
        elif view_id == ID_GIT_REFS and hasattr(item, "data"):
            self._build_refs_menu(item)
        elif view_id == ID_LOG:
            self._append_copy_items(view, item)
        else:
            return False
        self.open_at(x, y)
        return True

    def _build_main_menu(self, item, view):
        """The app-level menu (right-click on empty space / F7 on self.app):
        view switches, search, refresh, preferences, quit. Returns the (x, y)
        this menu should be positioned at, anchored to `view`'s origin rather
        than the mouse position the other menus use."""
        win_y, win_x = view.win.getbegyx()
        x = win_x + view.x
        y = win_y + view.y
        self._append("Show Git commit log", item.git_log.show, shortcut="F1")
        self._append("Show Git references", item.git_refs.show, shortcut="F2")
        self._append("Show Git commit diff", item.git_diff.show, shortcut="F3")
        self._append("Show Logs", item.log.view.show, shortcut="F4")
        self.append(SeparatorItem())
        self._append("Search", view.handle_key, [KeyEvent(ord("/"))], shortcut="/")
        self._append_action(
            "copy.all", "Copy all to clipboard", view.copy_text_to_clipboard
        )
        self.append(SeparatorItem())
        self._append("Refresh", item.git_log.refresh_head, shortcut="F5")
        self._append_action("global.reload", "Reload", item.reload_refs_commits)
        self.append(SeparatorItem())
        self._append("Preferences", self.app.preferences.show, shortcut="F9")
        self.append(SeparatorItem())
        self._append("Quit", item.exit_program, shortcut="F10")
        return x, y

    def _build_log_menu(self, view, item):
        """Menu for a row in the git-log view: an uncommitted pseudo-row gets
        just its clear action; a real commit gets branch/tag creation, cherry-
        pick/revert/reset, diff-against-selection/mark, and mark/jump."""
        if isinstance(item, UncommittedChangesListItem):
            self._append("Open to stage and commit", item.activate, shortcut="Enter")
            self._append("Commit staged changes...", self.app.commit_dialog.open)
            self.append(SeparatorItem())
            label = "Clear staged changes" if item._staged else "Clear unstaged changes"
            self._append(label, view.clean_uncommitted_changes, [item._staged])
        else:
            new_ref = self.app.git_refs.new_ref_dialog.create_ref
            selected = view.get_selected_commit_id()
            marked = view.marked_commit_id
            act = self._append_action
            act("log.new_branch", "Create new branch", new_ref, [item.id])
            act("log.new_tag", "Create new tag", new_ref, [item.id, "tag"])
            act(
                "log.cherry_pick",
                "Cherry-pick this commit",
                view.cherry_pick,
                [item.id],
            )
            act("log.revert", "Revert this commit", view.revert, [item.id])
            self.append(SeparatorItem())
            act("log.reset", "Reset branch here", view.confirm_reset, [item.id])
            self.append(SeparatorItem())
            self._append(
                "Diff this --> selected", view.diff_commits, [item.id, selected]
            )
            self._append(
                "Diff selected --> this", view.diff_commits, [selected, item.id]
            )
            act(
                "log.diff_to_marked",
                "Diff this --> marked commit",
                view.diff_commits,
                [item.id, marked],
                bool(marked),
            )
            act(
                "log.diff_from_marked",
                "Diff marked commit --> this",
                view.diff_commits,
                [marked, item.id],
                bool(marked),
            )
            self.append(SeparatorItem())
            act("log.mark", "Mark this commit", view.mark_commit, [item.id])
            act(
                "log.return_to_mark",
                "Return to mark",
                view.jump_to_commit,
                [marked],
                bool(marked),
            )
        self.append(SeparatorItem())
        self._append_copy_items(view, item)

    def _build_stage_items(self, view, item):
        """The uncommitted-changes view's staging entries for `item`'s
        section: stage / discard in the unstaged one, unstage in the staged
        one - of the selection, else the hunk (file, section) and the line."""
        section = view.target.section
        if view.has_selection():
            what, line = "selected lines", "selected lines"
        elif isinstance(item, StatListItem):
            what, line = "file", "line"
        else:
            block = file_block(view.items, view.items.index(item))
            if block is None:
                what = "all"
            elif block.hunk_of(view.items.index(item)) is None:
                what = "file"
            else:
                what = "hunk"
            line = "line"
        act = self._append_action
        if section == SECTION_UNSTAGED:
            act("diff.stage", f"Stage {what}", view.stage_action, [STAGE])
            if not view.has_selection():
                act(
                    "diff.stage_line", f"Stage {line}", view.stage_action, [STAGE, True]
                )
            act("diff.discard", f"Discard {what}...", view.stage_action, [DISCARD])
            if not view.has_selection():
                act(
                    "diff.discard_line",
                    f"Discard {line}...",
                    view.stage_action,
                    [DISCARD, True],
                )
        else:
            act("diff.unstage", f"Unstage {what}", view.stage_action, [UNSTAGE])
            if not view.has_selection():
                act(
                    "diff.unstage_line",
                    f"Unstage {line}",
                    view.stage_action,
                    [UNSTAGE, True],
                )
        if what != "all":
            label = "Unstage all" if view.target.staged else "Stage all"
            act("diff.all", label, view.stage_all)
        self.append(SeparatorItem())
        act("diff.commit", "Commit staged changes...", view.open_commit_dialog)
        self.append(SeparatorItem())

    def _build_diff_menu(self, view, item):
        """Menu for a row in the diff view: staging (uncommitted changes),
        jump to the file (stat rows) or to the line's blame origin (diff
        rows), then the copy trio."""
        if view.shows_worktree():
            self._build_stage_items(view, item)
        # Enter is the rows' own action (activate), not a keymap binding.
        self._append(
            "Jump to file",
            StatListItem.jump_to_file,
            [item],
            isinstance(item, StatListItem),
            shortcut="Enter",
        )
        self._append(
            "Show origin of this line",
            DiffListItem.jump_to_origin,
            [item],
            isinstance(item, DiffListItem)
            and item.old_file_path
            and item.old_file_line is not None,
            shortcut="Enter",
        )
        self.append(SeparatorItem())
        self._append_copy_items(view, item)

    def ref_actions(self, data):
        """The actions on a ref, by its type: branch (checkout / rename / copy /
        push / remove), tag (copy / annotation / push / remove), remote branch
        (copy / remove), or any other ref (copy). A list of (action id, label,
        callable, args, enabled), None marking a separator. The refs menu shows
        them; the refs view runs them from their keys."""
        name = data["name"]
        copy = self.app.copy_to_clipboard
        kind = data["type"]
        if kind == "heads":
            return [
                (
                    "refs.checkout",
                    "Check out this branch",
                    self.checkout_branch,
                    [name],
                    True,
                ),
                (
                    "refs.rename",
                    "Rename this branch",
                    self.app.git_refs.new_ref_dialog.rename_branch,
                    [name],
                    True,
                ),
                ("refs.copy_name", "Copy branch name", copy, [name], True),
                None,
                (
                    "refs.push",
                    "Push branch to remote",
                    self.push_ref_to_remote,
                    [name],
                    True,
                ),
                None,
                ("refs.remove", "Remove this branch", self.remove_branch, [name], True),
            ]
        if kind == "tags":
            return [
                ("refs.copy_name", "Copy tag name", copy, [name], True),
                (
                    "refs.tag_annotation",
                    "Show tag annotation",
                    self.app.git_diff.show_tag_annotation,
                    [data.get("tag_id")],
                    "tag_id" in data,
                ),
                None,
                (
                    "refs.push",
                    "Push tag to remote",
                    self.push_ref_to_remote,
                    [name],
                    True,
                ),
                None,
                (
                    "refs.remove",
                    "Remove this tag",
                    self.confirm_remove_tag,
                    [name],
                    True,
                ),
            ]
        if kind == "remotes":
            return [
                ("refs.copy_name", "Copy remote branch name", copy, [name], True),
                None,
                (
                    "refs.remove",
                    "Remove this remote branch",
                    self.confirm_remove_remote_ref,
                    [name],
                    True,
                ),
            ]
        return [("refs.copy_name", "Copy ref name", copy, [name], True)]

    def run_ref_action(self, data, action_id) -> bool:
        """Run `action_id` on the ref `data` (a refs-view key). False when the
        ref has no such action."""
        for entry in self.ref_actions(data):
            if entry and entry[0] == action_id:
                _, _, action, args, enabled = entry
                if enabled:
                    action(*args)
                return True
        return False

    def _build_refs_menu(self, item):
        for entry in self.ref_actions(item.data):
            if entry is None:
                self.append(SeparatorItem())
            else:
                self._append_action(*entry)

    def checkout_branch(self, branch_name, force=False):
        args = ["git", "checkout"] + (["-f"] if force else []) + [branch_name]
        # refresh_head also re-probes the uncommitted changes on every path, so
        # check_uncommitted is not needed (the probes re-stat the whole tree
        # after a checkout - running them twice is noticeably slow on big repos).
        self.app.run_git(
            args,
            ok=f"Switched to branch {branch_name}",
            err="Error checking out branch",
            refresh_head=True,
            reload_refs=True,
            force=force,
            reasons=("would be overwritten by checkout",),
            retry=lambda: self.checkout_branch(branch_name, True),
            title=" Checkout blocked",
            lines=[
                (
                    f"Local files conflict with switching to '{branch_name}'.",
                    Screen.C_GIT_ID,
                ),
                (
                    "Force checkout? Conflicting local files will be lost.",
                    Screen.C_ERROR,
                ),
            ],
            label="[Force checkout]",
        )

    def push_ref_to_remote(self, branch_name):
        self.app.git_refs.ref_push_dialog.ref_name = branch_name
        self.app.git_refs.ref_push_dialog.set_title(f"Push ref: {branch_name}")
        self.app.git_refs.ref_push_dialog.clear()
        self.app.git_refs.ref_push_dialog.show()

    def remove_branch(self, branch_name, force=False):
        # Safe delete by default (-d); only force (-D) after the user confirms the
        # "not fully merged" warning. This matches the checkout/create/push/rename
        # force-confirm pattern, instead of silently force-deleting unmerged
        # commits. A merged branch deletes straight away with no prompt.
        args = ["git", "branch", "-D" if force else "-d", branch_name]
        self.app.run_git(
            args,
            ok=f"Deleted branch {branch_name}",
            err="Error deleting branch",
            reload_refs=True,
            force=force,
            reasons=("not fully merged",),
            retry=lambda: self.remove_branch(branch_name, True),
            title=" Branch not fully merged",
            lines=[
                (f"Branch '{branch_name}' is not fully merged.", Screen.C_GIT_ID),
                ("Delete anyway? Unmerged commits will be lost.", Screen.C_ERROR),
            ],
            label="[Force delete]",
            # Local and instant; the branch just disappears from the refs list.
            # The remote-side deletes (remove_remote_ref / remove_tag) hit the
            # network and keep their bar.
            quiet=True,
        )

    def confirm_remove_tag(self, tag_name):
        """Deleting a tag also deletes it from every remote: ask first."""
        self.app.confirm_dialog.confirm(
            " Remove tag",
            [
                (f"Delete tag '{tag_name}'", Screen.C_GIT_ID),
                ("locally and from every remote?", Screen.C_ERROR),
            ],
            lambda: self.remove_tag(tag_name),
            confirm_label="[Delete]",
        )

    def confirm_remove_remote_ref(self, remote_ref):
        """Deleting a remote branch pushes the deletion: ask first."""
        self.app.confirm_dialog.confirm(
            " Remove remote branch",
            [
                (f"Delete branch '{remote_ref}'", Screen.C_GIT_ID),
                ("from its remote?", Screen.C_ERROR),
            ],
            lambda: self.remove_remote_ref(remote_ref),
            confirm_label="[Delete]",
        )

    def remove_tag(self, tag_name):
        remotes = Job.run_job(self.app, ["git", "remote"]).stdout.splitlines()
        removed_from_remotes = []

        # Pushing the deletion to each remote hits the network, and this runs
        # on the UI thread - keep the in-progress bar up for the whole batch.
        # With no remotes to push to this is just a local `git tag -d`: instant,
        # and the tag vanishes from the refs list, so stay quiet about it like
        # the other local-ref commands (run_git's `quiet`).
        quiet = not remotes
        if not quiet:
            self.app.screen.show_working(f"Working: deleting tag {tag_name} ...")
        try:
            result = Job.run_job(self.app, ["git", "tag", "-d", tag_name])
            if result.returncode == 0:
                removed_from_remotes.append("<local>")

            for remote in remotes:
                result = Job.run_job(
                    self.app, ["git", "push", "--delete", remote, tag_name]
                )
                if result.returncode == 0:
                    removed_from_remotes.append(remote)
        finally:
            if not quiet:
                self.app.screen.clear_working()

        if removed_from_remotes:
            self.app.git_refs.reload_refs()
            message = f"Deleted tag {tag_name} from remotes: " + " ".join(
                removed_from_remotes
            )
            self.app.log.info(message) if quiet else self.app.log.success(message)
        else:
            self.app.log.error(f"Error deleting tag: {result.stderr}")

    def remove_remote_ref(self, remote_ref):
        remote, branch = remote_ref.split("/", 1)
        self.app.run_git(
            ["git", "push", "--delete", remote, branch],
            ok=f"Deleted remote branch {remote_ref}",
            err="Error deleting remote branch",
            reload_refs=True,
        )
