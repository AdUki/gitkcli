"""ContextMenu: the right-click / F7 popup menu."""

from __future__ import annotations

from gitk.config import copy_to_clipboard
from gitk.ids import ID_CONTEXT_MENU, ID_GIT_DIFF, ID_GIT_LOG, ID_GIT_REFS, ID_LOG
from gitk.input import KeyboardState
from gitk.items import ContextMenuItem, DiffListItem, SeparatorItem, StatListItem
from gitk.jobs import Job
from gitk.list_view import ListView
from gitk.screen import Screen
from gitk.segmented_items import UncommittedChangesListItem


class ContextMenu(ListView):
    def __init__(self, app):
        super().__init__(app, ID_CONTEXT_MENU, "window")
        self.is_popup = True
        # F7 cycle state: (targets, view, row_x, row_y) and the index shown, or
        # None when no segment cycle is active. See start_cycle / advance_cycle.
        self._cycle = None
        self._cycle_index = -1

    def on_activated(self):
        super().on_activated()
        self.app.mouse.capture_mouse_movement(True, self)

    def on_deactivated(self):
        super().on_deactivated()
        self.app.mouse.capture_mouse_movement(False, self)

    def _append_copy_items(self, view, item):
        """The line/range/all clipboard trio shared by the git-log, git-diff and
        log context menus."""
        self.append(
            ContextMenuItem("Copy line to clipboard", item.copy_text_to_clipboard)
        )
        self.append(
            ContextMenuItem(
                "Copy range to clipboard", view.copy_text_range_to_clipboard, [item]
            )
        )
        self.append(
            ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard)
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
        self.app.mouse.screen_x = max(0, row_x + segment_x)
        self.app.mouse.screen_y = row_y
        self.show_context_menu(menu_item, view_id, view=view, force=True)

    def show_context_menu(
        self, item, view_id: str = "", view=None, force=False
    ) -> bool:
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
        x = self.app.mouse.screen_x
        y = self.app.mouse.screen_y
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
        self.set_dimensions(x, y, len(self.items) + 2, 30)
        self.show()
        return True

    def _build_main_menu(self, item, view):
        """The app-level menu (right-click on empty space / F7 on self.app):
        view switches, search, refresh, preferences, quit. Returns the (x, y)
        this menu should be positioned at, anchored to `view`'s origin rather
        than the mouse position the other menus use."""
        win_y, win_x = view.win.getbegyx()
        x = win_x + view.x
        y = win_y + view.y
        self.append(ContextMenuItem("Show Git commit log <F1>", item.git_log.show))
        self.append(ContextMenuItem("Show Git references <F2>", item.git_refs.show))
        self.append(ContextMenuItem("Show Git commit diff <F3>", item.git_diff.show))
        self.append(ContextMenuItem("Show Logs <F4>", item.log.view.show))
        self.append(SeparatorItem())
        self.append(
            ContextMenuItem("Search </>", view.handle_input, [KeyboardState(ord("/"))])
        )
        self.append(
            ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard)
        )
        self.append(SeparatorItem())
        self.append(ContextMenuItem("Refresh <F5>", item.git_log.refresh_head))
        self.append(ContextMenuItem("Reload <Shift+F5>", item.reload_refs_commits))
        self.append(SeparatorItem())
        self.append(ContextMenuItem("Preferences", self.app.preferences.show))
        self.append(SeparatorItem())
        self.append(ContextMenuItem("Quit", item.exit_program))
        return x, y

    def _build_log_menu(self, view, item):
        """Menu for a row in the git-log view: an uncommitted pseudo-row gets
        just its clear action; a real commit gets branch/tag creation, cherry-
        pick/revert/reset, diff-against-selection/mark, and mark/jump."""
        if isinstance(item, UncommittedChangesListItem):
            label = "Clear staged changes" if item._staged else "Clear unstaged changes"
            self.append(
                ContextMenuItem(label, view.clean_uncommitted_changes, [item._staged])
            )
        else:
            self.append(
                ContextMenuItem(
                    "Create new branch",
                    self.app.git_refs.new_ref_dialog.create_ref,
                    [item.id],
                )
            )
            self.append(
                ContextMenuItem(
                    "Create new tag",
                    self.app.git_refs.new_ref_dialog.create_ref,
                    [item.id, "tag"],
                )
            )
            self.append(
                ContextMenuItem("Cherry-pick this commit", view.cherry_pick, [item.id])
            )
            self.append(ContextMenuItem("Revert this commit", view.revert, [item.id]))
            self.append(SeparatorItem())
            self.append(
                ContextMenuItem("Reset branch here", view.confirm_reset, [item.id])
            )
            self.append(SeparatorItem())
            self.append(
                ContextMenuItem(
                    "Diff this --> selected",
                    view.diff_commits,
                    [item.id, view.get_selected_commit_id()],
                )
            )
            self.append(
                ContextMenuItem(
                    "Diff selected --> this",
                    view.diff_commits,
                    [view.get_selected_commit_id(), item.id],
                )
            )
            self.append(
                ContextMenuItem(
                    "Diff this --> marked commit",
                    view.diff_commits,
                    [item.id, view.marked_commit_id],
                    bool(view.marked_commit_id),
                )
            )
            self.append(
                ContextMenuItem(
                    "Diff marked commit --> this",
                    view.diff_commits,
                    [view.marked_commit_id, item.id],
                    bool(view.marked_commit_id),
                )
            )
            self.append(SeparatorItem())
            self.append(
                ContextMenuItem("Mark this commit", view.mark_commit, [item.id])
            )
            self.append(
                ContextMenuItem(
                    "Return to mark",
                    view.select_commit,
                    [view.marked_commit_id],
                    bool(view.marked_commit_id),
                )
            )
        self.append(SeparatorItem())
        self._append_copy_items(view, item)

    def _build_diff_menu(self, view, item):
        """Menu for a row in the diff view: jump to the file (stat rows) or to
        the line's blame origin (diff rows), then the copy trio."""
        self.append(
            ContextMenuItem(
                "Jump to file",
                StatListItem.jump_to_file,
                [item],
                isinstance(item, StatListItem),
            )
        )
        self.append(
            ContextMenuItem(
                "Show origin of this line",
                DiffListItem.jump_to_origin,
                [item],
                isinstance(item, DiffListItem)
                and item.old_file_path
                and item.old_file_line is not None,
            )
        )
        self.append(SeparatorItem())
        self._append_copy_items(view, item)

    def _build_refs_menu(self, item):
        """Menu for a row in the refs view, branching on ref type: branch
        (checkout/rename/push/delete), tag (annotation/push/delete), remote
        branch (delete), or any other ref (just copy the name)."""
        if item.data["type"] == "heads":
            self.append(
                ContextMenuItem(
                    "Check out this branch", self.checkout_branch, [item.data["name"]]
                )
            )
            self.append(
                ContextMenuItem(
                    "Rename this branch",
                    self.app.git_refs.new_ref_dialog.rename_branch,
                    [item.data["name"]],
                )
            )
            self.append(
                ContextMenuItem(
                    "Copy branch name", copy_to_clipboard, [item.data["name"], self.app]
                )
            )
            self.append(SeparatorItem())
            self.append(
                ContextMenuItem(
                    "Push branch to remote",
                    self.push_ref_to_remote,
                    [item.data["name"]],
                )
            )
            self.append(SeparatorItem())
            self.append(
                ContextMenuItem(
                    "Remove this branch", self.remove_branch, [item.data["name"]]
                )
            )
        elif item.data["type"] == "tags":
            self.append(
                ContextMenuItem(
                    "Copy tag name", copy_to_clipboard, [item.data["name"], self.app]
                )
            )
            self.append(
                ContextMenuItem(
                    "Show tag annotation",
                    self.app.git_diff.job.show_tag_annotation,
                    [item.data.get("tag_id")],
                    "tag_id" in item.data,
                )
            )
            self.append(SeparatorItem())
            self.append(
                ContextMenuItem(
                    "Push tag to remote", self.push_ref_to_remote, [item.data["name"]]
                )
            )
            self.append(SeparatorItem())
            self.append(
                ContextMenuItem("Remove this tag", self.remove_tag, [item.data["name"]])
            )
        elif item.data["type"] == "remotes":
            self.append(
                ContextMenuItem(
                    "Copy remote branch name",
                    copy_to_clipboard,
                    [item.data["name"], self.app],
                )
            )
            self.append(SeparatorItem())
            self.append(
                ContextMenuItem(
                    "Remove this remote branch",
                    self.remove_remote_ref,
                    [item.data["name"]],
                )
            )
        else:
            self.append(
                ContextMenuItem(
                    "Copy ref name", copy_to_clipboard, [item.data["name"], self.app]
                )
            )

    def checkout_branch(self, branch_name, force=False):
        args = ["git", "checkout"] + (["-f"] if force else []) + [branch_name]
        self.app.run_git(
            args,
            ok=f"Switched to branch {branch_name}",
            err="Error checking out branch",
            refresh_head=True,
            reload_refs=True,
            check_uncommitted=True,
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
        self.app.git_refs.ref_push_dialog.header_item.set_text(
            f"Push ref: {branch_name}"
        )
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
        )

    def remove_tag(self, tag_name):
        remotes = Job.run_job(self.app, ["git", "remote"]).stdout.splitlines()
        removed_from_remotes = []

        result = Job.run_job(self.app, ["git", "tag", "-d", tag_name])
        if result.returncode == 0:
            removed_from_remotes.append("<local>")

        for remote in remotes:
            result = Job.run_job(
                self.app, ["git", "push", "--delete", remote, tag_name]
            )
            if result.returncode == 0:
                removed_from_remotes.append(remote)

        if removed_from_remotes:
            self.app.git_refs.reload_refs()
            self.app.log.success(
                f"Deleted tag {tag_name} from remotes: "
                + " ".join(removed_from_remotes)
            )
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
