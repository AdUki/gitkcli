"""The App struct: holds the app's components and the methods that coordinate
them.

Created once in `launch_curses` and injected into the components: Screen, views,
and jobs receive it at construction (`self.app`); items and segments reach it
through the parent chain (`get_app()`). A plain instance, not a global.
"""

from __future__ import annotations

from gitk.input import KeyboardState
from gitk.jobs import Job
from gitk.split_layout import SplitLayout


class App:
    def __init__(self):
        self.running = True
        self.screen: Screen = None
        self.mouse: MouseState = None
        self.keyboard: KeyboardState = None
        self.log: Log = None
        self.git_log: GitLogView = None
        self.git_diff: GitDiffView = None
        self.git_refs: GitRefsView = None
        self.context_menu: ContextMenu = None
        self.preferences: "PreferencesDialogPopup" = None
        self.command_dialog: "CommandDialogPopup" = None
        self.confirm_dialog: "ConfirmDialogPopup" = None
        self.error_dialog: "ErrorDialogPopup" = None

        # Split-view state + tiling logic, reached as `app.split`.
        self.split = SplitLayout(self)

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
    ):
        """Run a git command and react to the result. On success: run the
        requested refreshes and log `ok`. On a forceable rejection (`retry` set,
        not already forcing, and a `reasons` substring in stderr): pop a confirm
        dialog. Otherwise log `err` + stderr. Returns the CompletedProcess."""
        # Paint the in-progress bar before the call: run_job blocks the event
        # loop, so nothing can repaint until the command returns. The refreshes
        # run inside the same bar - they block too (e.g. the uncommitted-changes
        # probes re-stat the whole tree right after a checkout).
        previous_message = self.screen.working_message
        message = "Working: " + " ".join(args) + " ..."
        self.screen.show_working(message)
        try:
            result = Job.run_job(self, args)
            if result.returncode == 0:
                if refresh_head:
                    self.git_log.refresh_head()
                if reload_refs:
                    self.git_refs.reload_refs()
                if check_uncommitted:
                    self.git_log.check_uncommitted_changes()
                if ok:
                    self.log.success(ok)
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
            if self.screen.working_message == message:
                if previous_message:
                    self.screen.working_message = previous_message
                else:
                    self.screen.clear_working()
        return result

    def refresh_all(self):
        """Refresh new commits on HEAD and reload refs (the F5 action)."""
        self.git_log.refresh_head()
        self.git_refs.reload_refs()

    def open_search(self):
        """Open the active view's search dialog (the F6 / '/' action)."""
        view = self.screen.get_active_view()
        if view:
            view.handle_input(KeyboardState(ord("/")))

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

        win_y, win_x = view.win.getbegyx()
        row_x = win_x + view.x - view._offset_x
        # Anchor one row BELOW the selection so the selected row stays visible
        # above the menu (the keyboard has no cursor to show what it acts on).
        row_y = win_y + view.y + (view._selected - view._offset_y) + 1

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
            if at_selection:
                self.mouse.screen_x = max(0, row_x)
                self.mouse.screen_y = row_y
            menu.show_context_menu(item)

    def reload_refs_commits(self):
        self.git_refs.reload_refs()
        self.git_log.reload_commits()

    def exit_program(self):
        self.running = False
        for job in Job.jobs.values():
            job.stop_job()
