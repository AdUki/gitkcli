"""ContextMenu: the right-click / F7 popup menu."""

from __future__ import annotations

from gitk.config import copy_to_clipboard
from gitk.ids import ID_CONTEXT_MENU
from gitk.input import KeyboardState
from gitk.items import ContextMenuItem, DiffListItem, SeparatorItem, StatListItem
from gitk.jobs import Job
from gitk.list_view import ListView
from gitk.segmented_items import UncommittedChangesListItem

class ContextMenu(ListView):
    def __init__(self, app):
        super().__init__(app, ID_CONTEXT_MENU, 'window')
        self.is_popup = True

    def on_activated(self):
        super().on_activated()
        self.app.mouse.capture_mouse_movement(True, self)

    def on_deactivated(self):
        super().on_deactivated()
        self.app.mouse.capture_mouse_movement(False, self)
        
    def _append_copy_items(self, view, item):
        """The line/range/all clipboard trio shared by the git-log, git-diff and
        log context menus."""
        self.append(ContextMenuItem("Copy line to clipboard", item.copy_text_to_clipboard))
        self.append(ContextMenuItem("Copy range to clipboard", view.copy_text_range_to_clipboard, [item]))
        self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard))

    def show_context_menu(self, item, view_id:str = '') -> bool:
        if self.app.screen.showed_views[-1] == self:
            return True
        self.clear()
        self._selected = -1
        if not view_id:
            view_id = self.app.screen.showed_views[-1].id
        view = self.app.screen.get_active_view()
        x = self.app.mouse.screen_x
        y = self.app.mouse.screen_y
        if item is self.app: # main menu
            win_y, win_x = view.win.getbegyx()
            x = win_x + view.x
            y = win_y + view.y
            self.append(ContextMenuItem("Show Git commit log <F1>", item.git_log.show))
            self.append(ContextMenuItem("Show Git references <F2>", item.git_refs.show))
            self.append(ContextMenuItem("Show Git commit diff <F3>", item.git_diff.show))
            self.append(ContextMenuItem("Show Logs <F4>", item.log.view.show))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Search </>", view.handle_input, [KeyboardState(ord('/'))]))
            self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Refresh <F5>", item.git_log.refresh_head))
            self.append(ContextMenuItem("Reload <Shift+F5>", item.reload_refs_commits))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Preferences", self.app.preferences.show))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Quit", item.exit_program))
        elif view_id == 'git-log' and hasattr(item, 'id'):
            if isinstance(item, UncommittedChangesListItem):
                label = "Clear staged changes" if item._staged else "Clear unstaged changes"
                self.append(ContextMenuItem(label, view.clean_uncommitted_changes, [item._staged]))
            else:
                self.append(ContextMenuItem("Create new branch", self.app.git_refs.view_new_ref.create_ref, [item.id]))
                self.append(ContextMenuItem("Create new tag", self.app.git_refs.view_new_ref.create_ref, [item.id, 'tag']))
                self.append(ContextMenuItem("Cherry-pick this commit", view.cherry_pick, [item.id]))
                self.append(ContextMenuItem("Revert this commit", view.revert, [item.id]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Reset branch here", view.confirm_reset, [item.id]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Diff this --> selected", view.diff_commits, [item.id, view.get_selected_commit_id()]))
                self.append(ContextMenuItem("Diff selected --> this", view.diff_commits, [view.get_selected_commit_id(), item.id]))
                self.append(ContextMenuItem("Diff this --> marked commit", view.diff_commits, [item.id, view.marked_commit_id], bool(view.marked_commit_id)))
                self.append(ContextMenuItem("Diff marked commit --> this", view.diff_commits, [view.marked_commit_id, item.id], bool(view.marked_commit_id)))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Mark this commit", view.mark_commit, [item.id]))
                self.append(ContextMenuItem("Return to mark", view.select_commit, [view.marked_commit_id], bool(view.marked_commit_id)))
            self.append(SeparatorItem())
            self._append_copy_items(view, item)
        elif view_id == 'git-diff':
            self.append(ContextMenuItem("Jump to file", StatListItem.jump_to_file, [item], isinstance(item, StatListItem)))
            self.append(ContextMenuItem("Show origin of this line", DiffListItem.jump_to_origin, [item], isinstance(item, DiffListItem) and item.old_file_path and item.old_file_line is not None))
            self.append(SeparatorItem())
            self._append_copy_items(view, item)
        elif view_id == 'git-refs' and hasattr(item, 'data'):
            if item.data['type'] == 'heads':
                self.append(ContextMenuItem("Check out this branch", self.checkout_branch, [item.data['name']]))
                self.append(ContextMenuItem("Rename this branch", self.app.git_refs.view_new_ref.rename_branch, [item.data['name']]))
                self.append(ContextMenuItem("Copy branch name", copy_to_clipboard, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Push branch to remote", self.push_ref_to_remote, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Remove this branch", self.remove_branch, [item.data['name']]))
            elif item.data['type'] == 'tags':
                self.append(ContextMenuItem("Copy tag name", copy_to_clipboard, [item.data['name']]))
                self.append(ContextMenuItem("Show tag annotation", self.app.git_diff.job.show_tag_annotation, [item.data.get('tag_id')], 'tag_id' in item.data))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Push tag to remote", self.push_ref_to_remote, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Remove this tag", self.remove_tag, [item.data['name']]))
            elif item.data['type'] == 'remotes':
                self.append(ContextMenuItem("Copy remote branch name", copy_to_clipboard, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Remove this remote branch", self.remove_remote_ref, [item.data['name']]))
            else:
                self.append(ContextMenuItem("Copy ref name", copy_to_clipboard, [item.data['name']]))
        elif view_id == 'log':
            self._append_copy_items(view, item)
        else:
            return False
        self.set_dimensions(x, y, len(self.items) + 2, 30)
        self.show()
        return True

    def checkout_branch(self, branch_name, force = False):
        args = ['git', 'checkout'] + (['-f'] if force else []) + [branch_name]
        self.app.run_git(args, ok=f'Switched to branch {branch_name}',
                        err='Error checking out branch',
                        refresh_head=True, reload_refs=True, check_uncommitted=True,
                        force=force, reasons=('would be overwritten by checkout',),
                        retry=lambda: self.checkout_branch(branch_name, True),
                        title=' Checkout blocked',
                        lines=[(f"Local files conflict with switching to '{branch_name}'.", 4),
                               ("Force checkout? Conflicting local files will be lost.", 2)],
                        label='[Force checkout]')

    def push_ref_to_remote(self, branch_name):
        self.app.git_refs.view_ref_push.ref_name = branch_name
        self.app.git_refs.view_ref_push.header_item.set_text(f"Push ref: {branch_name}")
        self.app.git_refs.view_ref_push.clear()
        self.app.git_refs.view_ref_push.show()

    def remove_branch(self, branch_name):
        self.app.run_git(['git', 'branch', '-D', branch_name],
                        ok=f'Deleted branch {branch_name}',
                        err='Error deleting branch', reload_refs=True)

    def remove_tag(self, tag_name):
        remotes = Job.run_job(self.app, ['git', 'remote']).stdout.splitlines()
        removed_from_remotes = []

        result = Job.run_job(self.app, ['git', 'tag', '-d', tag_name])
        if result.returncode == 0:
            removed_from_remotes.append('<local>')

        for remote in remotes:
            result = Job.run_job(self.app, ['git', 'push', '--delete', remote, tag_name])
            if result.returncode == 0:
                removed_from_remotes.append(remote)

        if removed_from_remotes:
            self.app.git_refs.reload_refs()
            self.app.log.success(f'Deleted tag {tag_name} from remotes: ' + ' '.join(removed_from_remotes))
        else:
            self.app.log.error(f"Error deleting tag: {result.stderr}")

    def remove_remote_ref(self, remote_ref):
        remote, branch = remote_ref.split('/', 1)
        self.app.run_git(['git', 'push', '--delete', remote, branch],
                        ok=f'Deleted remote branch {remote_ref}',
                        err='Error deleting remote branch', reload_refs=True)
