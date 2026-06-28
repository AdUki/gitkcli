"""The App struct: the application's components and the service methods that
coordinate them.

Created once in launch_curses and injected into the components (Screen/views/
jobs get `self.app`; items/segments reach it via get_app()). A plain instance,
not a service-locator global.
"""

from __future__ import annotations

from gitk.input import KeyboardState
from gitk.jobs import Job

class App:
    """The application struct: holds the app's components and the service
    methods that coordinate them.

    Created once in `launch_curses` and handed to the components. Screen, views,
    and jobs receive it at construction (`self.app`); items/segments reach it
    through the parent chain (`get_app()`). It is a plain instance that is
    passed/injected, not a service-locator global.
    """

    def __init__(self):
        self.running = True
        self.screen:Screen = None
        self.mouse:MouseState = None
        self.keyboard:KeyboardState = None
        self.log:Log = None
        self.git_log:GitLogView = None
        self.git_diff:GitDiffView = None
        self.git_refs:GitRefsView = None
        self.context_menu:ContextMenu = None
        self.preferences:"PreferencesDialogPopup" = None
        self.confirm_dialog:"ConfirmDialogPopup" = None
        self.error_dialog:"ErrorDialogPopup" = None

        # Split view tiles the git-log and git-diff panes side by side.
        #   'off'     - normal single-view behaviour
        #   'side'    - git-log left, git-diff right
        #   'stacked' - git-log top, git-diff bottom
        self.split_mode = 'off'
        self.split_ratio = 0.5         # fraction of the screen given to the git-log pane
        self._raising_split_sibling = False

        # Layout the app opens in: 'fullscreen' (single view), 'side' or 'stacked'.
        self.default_view_mode = 'fullscreen'

    def run_git(self, args, ok=None, err='Error', refresh_head=False, reload_refs=False,
                check_uncommitted=False, force=False, reasons=(), retry=None,
                title='', lines=(), label='[Yes]'):
        """Run a git command and react to the result. On success: run the
        requested refreshes and log `ok`. On a forceable rejection (`retry` set,
        not already forcing, and a `reasons` substring in stderr): pop a confirm
        dialog. Otherwise log `err` + stderr. Returns the CompletedProcess."""
        result = Job.run_job(self, args)
        if result.returncode == 0:
            if refresh_head: self.git_log.refresh_head()
            if reload_refs: self.git_refs.reload_refs()
            if check_uncommitted: self.git_log.check_uncommitted_changes()
            if ok: self.log.success(ok)
        elif retry and not force and any(r in result.stderr for r in reasons):
            self.confirm_dialog.confirm(title, list(lines), retry, confirm_label=label)
        else:
            self.log.error(f"{err}: {result.stderr}")
        return result

    def refresh_all(self):
        """Refresh new commits on HEAD and reload refs (the F5 action)."""
        self.git_log.refresh_head()
        self.git_refs.reload_refs()

    def open_search(self):
        """Open the active view's search dialog (the F6 / '/' action)."""
        view = self.screen.get_active_view()
        if view:
            view.handle_input(KeyboardState(ord('/')))

    def open_context_menu(self, at_selection=True):
        """Open the context menu for the active view's selected item.
        at_selection=True (the F7 *key*) opens it at the selected row, since the
        keyboard has no cursor; at_selection=False (a mouse click on the F7 bar
        button) leaves it at the current mouse position."""
        view = self.screen.get_active_view()
        if not view or not hasattr(view, 'get_selected'):
            return
        item = view.get_selected()
        if item is None:
            return
        if at_selection:
            win_y, win_x = view.win.getbegyx()
            self.mouse.screen_x = win_x + view.x
            self.mouse.screen_y = win_y + view.y + (view._selected - view._offset_y)
        self.context_menu.show_context_menu(item)

    def reload_refs_commits(self):
        self.git_refs.reload_refs()
        self.git_log.reload_commits()

    def exit_program(self):
        self.running = False
        for job in Job.jobs.values():
            job.stop_job()

    def split_active(self):
        """True only when the split is currently shown as two tiled panes.

        `split_mode` is the user's intent; on a terminal too small to tile, the
        panes fall back to fullscreen (view_mode != 'window'). Behaviours that
        only make sense with a visible split (Esc/q stepping, divider drag,
        pane focus pairing) key off this, not off `split_mode` alone.
        """
        return self.split_mode != 'off' and self.git_log.view_mode == 'window'

    def cycle_split_view(self):
        self.set_split_mode({'off': 'side', 'side': 'stacked', 'stacked': 'off'}[self.split_mode])
        return True

    def set_split_mode(self, mode):
        self.split_mode = mode
        self.apply_split_layout()
        if mode != 'off':
            # Seed the diff pane from the current selection if it has no content yet.
            if not self.git_diff.items:
                item = self.git_log.get_selected()
                if item and hasattr(item, 'load_to_view'):
                    item.load_to_view()
            self.git_log.show()   # focus the log pane (raises the diff pane with it)

    def apply_split_layout(self):
        """Position the git-log/git-diff panes for the current split mode."""
        lines, cols = self.screen.getmaxyx()
        min_w, min_h = 12, 4
        # Both axes must clear their minimum, otherwise a pane would be tiled
        # into a degenerate (<=0 content) window.
        fits = ((self.split_mode == 'side' and cols >= 2 * min_w and lines >= min_h) or
                (self.split_mode == 'stacked' and lines >= 2 * min_h and cols >= min_w))
        if self.split_mode != 'off' and fits:
            if self.split_mode == 'side':
                log_w = max(min_w, min(cols - min_w, int(round(cols * self.split_ratio))))
                self.git_log.set_tiled(0, 0, lines, log_w)
                self.git_diff.set_tiled(log_w, 0, lines, cols - log_w)
            else:
                log_h = max(min_h, min(lines - min_h, int(round(lines * self.split_ratio))))
                self.git_log.set_tiled(0, 0, log_h, cols)
                self.git_diff.set_tiled(0, log_h, lines - log_h, cols)
        else:
            # split off, or terminal too small to tile: both panes go fullscreen.
            # Clear the tiled geometry so a later toggle_window_mode floats a
            # centered window again instead of reusing the last pane rect.
            for v in (self.git_log, self.git_diff):
                v.fixed_x = v.fixed_y = v.fixed_width = v.fixed_height = None
                v.set_fullscreen()
                v.dirty = True
