#!/usr/bin/python

import curses
import curses.panel
import dataclasses
import datetime
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import typing

# Keyboard constants and the input state classes now live in gitk.input;
# re-exported below so this file's remaining code keeps resolving them.
from gitk.input import (KEY_SHIFT_F5, KEY_CTRL_LEFT, KEY_CTRL_RIGHT,
                        KEY_CTRL_BACKSPACE, KEY_CTRL_DEL, KEY_ENTER, KEY_RETURN,
                        KEY_TAB, ENTER_KEYS, KeyboardState, MouseState)
# Screen now lives in gitk.screen; re-exported so this file's code resolves it.
from gitk.screen import Screen
# Segments (and the ref_color_and_title helper) now live in gitk.segments.
from gitk.segments import (ref_color_and_title, Segment, FillerSegment,
                           TextSegment, RefSegment, ButtonSegment, ToggleSegment,
                           SplitButtonSegment, DynamicTextSegment,
                           HighlightToggleSegment, OnOffToggleSegment, ChoiceSegment)
# List items now live in gitk.items.
from gitk.items import (Item, SeparatorItem, RefListItem, TextListItem,
                        SpacerListItem, StatListItem, DiffListItem,
                        SegmentedListItem, ButtonRowItem, button_row,
                        WindowTopBarItem, UncommittedChangesListItem,
                        CommitListItem, ContextMenuItem, UserInputListItem,
                        ResetModeItem, PreferenceRow)
# View / ListView (+ split helper and view constants) now live in gitk.view.
from gitk.view import (View, ListView, _raise_split_sibling,
                       HORIZONTAL_OFFSET_JUMP, SPLIT_DIVIDER_COLOR)

# View/dialog/job identifiers now live in gitk.ids; re-exported for this file.
from gitk.ids import *
# Background git jobs now live in gitk.jobs.
from gitk.jobs import (Job, GitLogJob, GitRefreshHeadJob, GitDiffJob,
                       GitSearchJob, GitRefsJob)

# Config persistence, the clipboard helper, and KEY_CTRL now live in gitk.config.
# Re-exported here so not-yet-extracted code in this file keeps resolving them.
from gitk.config import (KEY_CTRL, DEFAULT_CONFIG, get_config_path,
                         load_config, save_config, copy_to_clipboard)
# Modal dialog popups now live in gitk.dialogs.
from gitk.dialogs import (ResetDialogPopup, RefPushDialogPopup,
                          _RedMessageBoxPopup, ConfirmDialogPopup,
                          ErrorDialogPopup, UserInputDialogPopup,
                          PreferencesDialogPopup, NewRefDialogPopup,
                          SearchDialogPopup, GitSearchDialogPopup)
# The concrete views + context menu now live in gitk.views.
from gitk.views import (GitLogView, GitDiffView, GitRefsView, LogView,
                        ContextMenu)

class Log:
    def __init__(self, app):
        self.app = app
        self.view = LogView(app)
        self.level = 4

    def debug(self, txt):
        if self.level > 4: self.log(18, txt)

    def info(self, txt):
        if self.level > 3: self.log(1, txt)

    def success(self, txt):
        if self.level > 2:
            self.log(1, txt)
            # Flash the message green over the bottom bar (guarded: success can
            # fire during start-up before the screen exists).
            screen = getattr(self.app, 'screen', None)
            if screen is not None:
                screen.show_flash(txt)

    def warning(self, txt):
        if self.level > 1: self.log(12, txt)

    def error(self, txt):
        if self.level > 0:
            self.log(2, txt)
            # Surface errors as a modal red dialog (the status bar is gone).
            # Guarded: errors can fire during start-up before the dialog exists.
            dialog = getattr(self.app, 'error_dialog', None)
            if dialog is not None:
                dialog.show_error(txt)

    def log(self, color, txt):
        now = datetime.datetime.now()
        for line in txt.splitlines():
            self.view.append(TextListItem(f'{now} {line}', color))

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

def launch_curses(stdscr, git_args:typing.List, cmd_args:typing.List):

    app = App()

    app.screen = Screen(app, stdscr)
    app.mouse = MouseState()
    app.mouse.app = app
    app.keyboard = KeyboardState()
    app.keyboard.app = app
    app.log = Log(app)
    app.git_log = GitLogView(app, git_args, cmd_args)
    app.git_diff = GitDiffView(app)
    app.git_refs = GitRefsView(app)
    app.context_menu = ContextMenu(app)
    app.preferences = PreferencesDialogPopup(app)
    app.confirm_dialog = ConfirmDialogPopup(app)
    app.error_dialog = ErrorDialogPopup(app)

    _cfg = load_config()
    app.git_log.show_commit_id     = _cfg['git_log']['show_commit_id']
    app.git_log.show_commit_date   = _cfg['git_log']['show_commit_date']
    app.git_log.show_commit_author = _cfg['git_log']['show_commit_author']
    app.git_log.set_pref_flags(_cfg['git_log']['flags'])
    app.git_diff.ignore_whitespace = _cfg['git_diff']['ignore_whitespace']
    app.log.view.autoscroll        = _cfg['log']['autoscroll']
    app.default_view_mode          = _cfg['view']['default_mode']

    app.log.info('Application started')

    app.git_refs.job.start_job()
    app.git_log.job.start_job()
    app.git_log.check_uncommitted_changes()

    if app.default_view_mode in ('side', 'stacked'):
        app.set_split_mode(app.default_view_mode)
    else:
        app.git_log.show()

    try:
        user_input = True

        while app.running:

            update_jobs = Job.process_all_jobs()

            # A success flash auto-expires on a timer, so keep redrawing the bottom
            # bar while one is showing even if nothing else changed.
            flash = app.screen.flash_active()

            if update_jobs or user_input or flash:
                try:
                    # Draws dirty content, then composites the panel deck and the
                    # bottom bar in one doupdate().
                    app.screen.draw_visible_views()
                except curses.error as e:
                    app.log.warning(f"Curses exception: {str(e)}\n{traceback.format_exc()}")

            active_view = app.screen.get_active_view()
            if not active_view:
                break;

            stdscr.timeout(5 if update_jobs or flash else 100)

            user_input = app.keyboard.read(stdscr)
            if not user_input:
                # no key pressed (or an unrecognized escape sequence)
                continue

            key = app.keyboard.key

            if key == curses.KEY_MOUSE:
                if not app.mouse.read_curses_event(stdscr):
                    continue

                event_type = app.mouse.event_type

                if event_type == 'right-click' and app.mouse.left_pressed:
                    app.mouse.left_pressed = False
                    app.mouse.process_mouse_event(active_view, 'right-release')

                if (event_type == 'left-click' or event_type == 'double-click') and app.mouse.right_pressed:
                    app.mouse.right_pressed = False
                    app.mouse.process_mouse_event(active_view, 'left-release')

                app.mouse.process_mouse_event(active_view, event_type)

            elif key == curses.KEY_RESIZE:
                app.screen._full_redraw = True
                lines, cols = app.screen.getmaxyx()
                for view in app.screen.views.values():
                    view.screen_size_changed(lines, cols)
                if app.split_mode != 'off':
                    app.apply_split_layout()

            elif active_view.handle_input(app.keyboard):
                active_view.dirty = True

            else:
                if key == ord('q') or key == curses.KEY_EXIT:
                    app.screen.hide_active_view()
                elif key == KEY_CTRL_LEFT or key == KEY_CTRL('o'):
                    app.git_log.move_in_jump_list(+1)
                elif key == KEY_CTRL_RIGHT or key == KEY_CTRL('i'):
                    app.git_log.move_in_jump_list(-1)
                elif key == ord('|'):
                    app.cycle_split_view()
                elif key == KEY_CTRL('w') and app.split_active():
                    # toggle focus between the two split panes
                    (app.git_diff if app.git_log.is_active() else app.git_log).show()
                elif key == KEY_SHIFT_F5:
                    app.reload_refs_commits()
                elif key == curses.KEY_F7:
                    # From the keyboard, open at the selected row (no mouse cursor).
                    app.open_context_menu()
                elif key in app.screen.fkey_actions:
                    app.screen.fkey_actions[key]()

    except KeyboardInterrupt:
        pass

    app.exit_program()

    app.log.info('Application ended')

def main():
    args = sys.argv[1:]

    # Check for help flags
    if '-h' in args:
        subprocess.run(['git', 'log', '-h'])
        sys.exit(0)
    if '--help' in args:
        subprocess.run(['git', 'log', '--help'])
        sys.exit(0)

    git_args = []
    cmd_args = []

    for arg in args:
        if arg == '--no-color':
            # Force the monochrome tier regardless of TERM (same effect as the
            # NO_COLOR env var). Not a git arg, so it never reaches `git log`.
            Screen.force_mono = True
        elif arg == '--graph':
            git_args.append(arg)
        else:
            cmd_args.append(arg)

    curses.wrapper(lambda stdscr: launch_curses(stdscr, git_args, cmd_args))

if __name__ == "__main__":
    main()
