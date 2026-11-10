"""The App struct: holds the app's components and the methods that coordinate
them. A tuikit.App, so it also runs the event loop.

Created once in `launch_curses` and injected into the components: Screen, views,
and jobs receive it at construction (`self.app`); items and segments reach it
through their view (`get_app()`). A plain instance, not a global.
"""

from __future__ import annotations

import curses

import tuikit
from tuikit import keys as K

from gitk.jobs import Job
from gitk.jump_list import JumpList
from gitk.keymap import Keymap
from gitk.split_layout import GitSplitLayout


class App(tuikit.App):
    name = "gitkcli"

    def __init__(self):
        super().__init__()
        self.git_log: GitLogView = None
        self.git_diff: GitDiffView = None
        self.git_refs: GitRefsView = None
        self.context_menu: ContextMenu = None
        self.preferences: "PreferencesDialogPopup" = None
        self.command_dialog: "CommandDialogPopup" = None
        self.confirm_dialog: "ConfirmDialog" = None
        self.error_dialog: "ErrorDialog" = None
        self.shortcuts: "ShortcutsDialogPopup" = None
        # The shortcut bindings, `app.keymap` (overrides loaded from config).
        self.keymap = Keymap()

        # Split-view state + tiling logic, reached as `app.split`; the panes
        # are attached once the views exist.
        self.split = GitSplitLayout(self)
        # Back/forward history shared by the log and diff panes, `app.jump_list`.
        self.jump_list = JumpList(self)
        self.commit_dialog: "CommitDialogPopup" = None
        self._toplevel = None

    def git_toplevel(self) -> str:
        """The work tree's top directory ("" outside one). Patches and paths
        from a diff are relative to it, wherever the app was started."""
        if not self._toplevel:
            result = Job.run_job(self, ["git", "rev-parse", "--show-toplevel"])
            self._toplevel = result.stdout.strip() if result.returncode == 0 else ""
        return self._toplevel

    def run_git(
        self,
        args,
        ok=None,
        err="Error",
        refresh_head=False,
        reload_refs=False,
        check_uncommitted=False,
        force=False,
        reasons=(),
        retry=None,
        title="",
        lines=(),
        label="[Yes]",
        quiet=False,
        input=None,
    ):
        """Run a git command and react to the result. On success: run the
        requested refreshes and log `ok`. On a forceable rejection (`retry` set,
        not already forcing, and a `reasons` substring in stderr): pop a confirm
        dialog. Otherwise log `err` + stderr. Returns the CompletedProcess.

        `quiet` drops both bits of bottom-bar ceremony - the in-progress bar
        (with its screen dim) and the green success flash - for commands that
        finish in a blink and show their own result: creating / deleting /
        renaming a local ref repaints the refs list immediately, so the bar
        would only flicker and the flash would sit on the bottom bar for two
        seconds saying what the screen already shows. The log line still lands
        in the log view, and failures still raise the error dialog.

        `input` (str) is fed to the command's stdin (a patch, a message)."""
        # Paint the in-progress bar before the call: run_job blocks the event
        # loop, so nothing can repaint until the command returns. The refreshes
        # run inside the same bar - they block too (e.g. the uncommitted-changes
        # probes re-stat the whole tree right after a checkout).
        previous_message = self.screen.working_message
        previous_dim = self.screen.dimmed
        message = "Working: " + " ".join(args) + " ..."
        if not quiet:
            self.screen.show_working(message)
        try:
            result = Job.run_job(self, args, input=input)
            if result.returncode == 0:
                if refresh_head:
                    self.git_log.refresh_head()
                if reload_refs:
                    self.git_refs.reload_refs()
                if check_uncommitted:
                    self.git_log.check_uncommitted_changes()
                if ok:
                    self.log.info(ok) if quiet else self.log.success(ok)
            elif retry and not force and any(r in result.stderr for r in reasons):
                self.confirm_dialog.confirm(
                    title, list(lines), retry, confirm_label=label
                )
            else:
                self.log.error(f"{err}: {result.stderr}")
        finally:
            # Only touch the bar if it still shows OUR message: a refresh above
            # may have started a streaming reload (reload_commits) that owns the
            # bar until its first rows arrive. Restore rather than clear so a
            # command run during such a reload gives the bar back to it.
            if not quiet and self.screen.working_message == message:
                if previous_message:
                    self.screen.working_message = previous_message
                    self.screen.dimmed = previous_dim
                else:
                    self.screen.clear_working()
        return result

    def refresh_all(self):
        """Refresh new commits on HEAD and reload refs (the F5 action), and
        the uncommitted changes when they are shown."""
        self.git_log.refresh_head()
        self.git_refs.reload_refs()
        self.git_diff.reload_worktree()

    def open_search(self):
        """Open the active view's search dialog (the F6 / '/' action)."""
        view = self.screen.get_active_view()
        if view:
            view.handle_key(K.KeyEvent(ord("/")))

    def open_context_menu(self, at_selection=True):
        """Open the context menu for the active view's selected item.
        at_selection=True (the F7 *key*) opens it at the selected row, since the
        keyboard has no cursor; at_selection=False (a mouse click on the F7 bar
        button) leaves it at the current mouse position.

        From the keyboard, on a segmented git-log row, repeated F7 presses cycle
        through the row's menus - the commit menu first, then each branch / tag /
        remote ref on the row, wrapping around - giving full keyboard reach to
        the per-segment menus a right-click opens with the mouse."""
        menu = self.context_menu
        # Menu already open (F7 pressed again): step to the next cycle target.
        if self.screen.showed_views[-1] is menu:
            menu.advance_cycle()
            return

        view = self.screen.get_active_view()
        if not view or not hasattr(view, "get_selected"):
            return
        item = view.get_selected()
        if item is None:
            return

        row_x, row_y = view.row_screen_position()
        # Anchor one row BELOW the selection so the selected row stays visible
        # above the menu (the keyboard has no cursor to show what it acts on).
        row_y += 1

        targets = (
            item.get_context_menu_targets()
            if hasattr(item, "get_context_menu_targets")
            else None
        )
        if at_selection and targets:
            menu.start_cycle(targets, view, row_x, row_y)
        else:
            # Mouse bar button, or a row with no segment menus: a single menu.
            # The keyboard anchors it at the row; the mouse keeps its position.
            at = (max(0, row_x), row_y) if at_selection else None
            menu.show_context_menu(item, at=at)

    def reload_refs_commits(self):
        self.git_refs.reload_refs()
        self.git_log.reload_commits()

    def exit_program(self):
        self.quit()
        for job in Job.jobs.values():
            job.stop_job()

    # -- tuikit.App hooks -----------------------------------------------------

    def process_background(self) -> bool:
        return Job.process_all_jobs()

    def show_context_menu(self, item, view=None) -> bool:
        return self.context_menu.show_context_menu(item)

    def handle_global_key(self, event) -> bool:
        key = event.key
        action = self.keymap.action_for("global", key)
        # Ctrl+Left / Ctrl+Right always walk the jump list too.
        if action == "global.jump_back" or key == K.KEY_CTRL_LEFT:
            self.jump_list.move(+1)
        elif action == "global.jump_forward" or key == K.KEY_CTRL_RIGHT:
            self.jump_list.move(-1)
        elif action == "global.split":
            self.split.cycle_split_view()
        elif action == "global.switch_pane":
            # toggle focus between the two split panes
            if self.split.split_active():
                (self.git_diff if self.git_log.is_active() else self.git_log).show()
        elif action == "global.reload":
            self.reload_refs_commits()
        elif key == curses.KEY_F7:
            # From the keyboard, open at the selected row (no mouse cursor).
            self.open_context_menu()
        else:
            # q / Esc close the top view; the F-keys run their bar entry.
            return super().handle_global_key(event)
        return True
