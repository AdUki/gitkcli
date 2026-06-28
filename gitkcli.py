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

class GitLogView(ListView):
    def __init__(self, app, git_args:typing.List, cmd_args:typing.List):
        super().__init__(app, ID_GIT_LOG, 'fullscreen');

        self.commits = {} # map: git_id --> { parents, date, author, title }

        self.marked_commit_id = ''
        self.jump_list = []
        self.jump_index = 0
        self.head_branch = ''
        self.head_id = ''
        # Whether the working tree / index currently differ; recomputed by
        # check_uncommitted_changes and consumed by _place_uncommitted_rows.
        self._has_working = False
        self._has_staged = False
        # In --graph mode git computes the lane art over the whole commit set, so
        # we cannot append a single commit's art incrementally - HEAD changes must
        # trigger a full reload instead. Recomputed in set_pref_flags.
        self._graph_mode = '--graph' in git_args
        # Commit to re-select once it streams back in after a reload (keeps the
        # cursor where the user left it). One-shot, like _focus_head_pending.
        self._pending_select_id = ''
        # One-shot: scroll to HEAD the first time it can be located after launch,
        # so it is visible even when it is not at the top (uncommitted rows above
        # it, an unrelated revision arg, or a detached/old HEAD deep in the list).
        self._focus_head_pending = True

        self.show_commit_id = True
        self.show_commit_date = True
        self.show_commit_author = True

        self._cli_args = git_args + cmd_args
        self.pref_flags = ''
        self.job = GitLogJob(self.app, ID_GIT_LOG, list(self._cli_args))
        self.job_git_refresh_head = GitRefreshHeadJob(self.app)
        self.job_git_search = GitSearchJob(self.app, cmd_args)
        self.view_reset = ResetDialogPopup(app)

    def set_pref_flags(self, flags: str):
        self.pref_flags = flags
        self.job.args = list(self._cli_args) + flags.split()
        # --graph may also arrive via the preferences "flags" field.
        self._graph_mode = '--graph' in self.job.args

        repo_name = os.path.basename(Job.run_job(self.app, ['git', 'rev-parse', '--show-toplevel']).stdout.strip())
        self.set_header_item(WindowTopBarItem(repo_name, [
                SplitButtonSegment(30),
                ButtonSegment("[<-]", lambda: self.move_in_jump_list(+1), 30),
                ButtonSegment("[->]", lambda: self.move_in_jump_list(-1), 30)
            ], title_color = 5))

        self.set_search_dialog(GitSearchDialogPopup(self.app));

    def add_commit(self, id, commit):
        if id in self.commits:
            return False
        self.commits[id] = commit
        return True

    def refresh_head(self):
        """Pull in commits made since the view last saw HEAD. In --graph mode the
        lane art is computed by git over the whole commit set, so a single new
        commit cannot be appended with correct art - reload the full log instead.
        Otherwise fetch just the new commits cheaply with `old..HEAD`."""
        new_head = Job.run_job(self.app, ['git', 'rev-parse', 'HEAD']).stdout.strip()
        if self._graph_mode:
            self.head_id = new_head
            self.reload_commits()
            return
        if self.head_id:
            # The job's in-view guard needs the OLD head, so start it before
            # advancing head_id below.
            self.job_git_refresh_head.start_job(['--reverse', f'{self.head_id}..HEAD'])
        self.head_id = new_head
        self.check_uncommitted_changes()

    def reload_commits(self):
        # Re-select the same commit once it streams back in, so a reload (prefs
        # change, F5 in --graph mode, Shift+F5) does not dump the cursor at the top.
        self._pending_select_id = self.get_selected_commit_id()
        self.clear()
        self.job.start_job()
        self.check_uncommitted_changes()

    def show(self):
        _raise_split_sibling(self, self.app.git_diff)
        super().show()

    def set_selected(self, what:int|str|re.Pattern, visible_mode = 'center') -> bool:
        ret = super().set_selected(what, visible_mode)
        if self.app.screen.is_view_visible(self.app.git_diff):
            item = self.get_selected()
            if item:
                item.load_to_view()
        return ret

    def check_uncommitted_changes(self):
        """Probe the working tree / index and (re)place the pseudo-rows."""
        self._has_staged = Job.run_job(self.app, ['git', 'diff', '--cached', '--quiet']).returncode != 0
        self._has_working = Job.run_job(self.app, ['git', 'diff', '--quiet']).returncode != 0
        self._place_uncommitted_rows()

    def _place_uncommitted_rows(self):
        """Single source of truth for the uncommitted pseudo-rows: drop any that
        exist and reinsert the ones we need directly above the HEAD commit row
        (working above staged), keeping the cursor on the same screen line.
        Idempotent, so it can be re-fired whenever HEAD or the rows change."""
        selected_id = getattr(self.get_selected(), 'id', None)
        old_selected = self._selected

        # Rebuild the list without the pseudo-rows.
        self.items = [it for it in self.items if not isinstance(it, UncommittedChangesListItem)]

        # Insert directly above the HEAD row; fall back to the top of the
        # real-commit list when HEAD is not (yet) in view.
        first_commit = head_index = None
        for i, it in enumerate(self.items):
            if isinstance(it, CommitListItem):
                if first_commit is None:
                    first_commit = i
                if it.id == self.head_id:
                    head_index = i
                    break
        insert_at = head_index if head_index is not None else (first_commit or 0)

        # Mirror HEAD's graph art onto the rows only when HEAD is the topmost
        # commit, so they render as nodes in HEAD's lane; never splice a node into
        # unrelated lanes when HEAD is buried (e.g. --all). Empty when not in
        # --graph mode.
        graph_prefix = ''
        if head_index is not None and head_index == first_commit:
            graph_prefix = self.commits.get(self.head_id, {}).get('prefix', '')

        rows = []
        if self._has_working:
            rows.append(UncommittedChangesListItem(staged = False))
        if self._has_staged:
            rows.append(UncommittedChangesListItem(staged = True))
        for n, row in enumerate(rows):
            row.graph_prefix = graph_prefix
            row._view = self
            self.items.insert(insert_at + n, row)

        # Keep the previously-selected row on the same screen line. Real commits
        # keep their identity across the rebuild; pseudo-rows are recreated, so we
        # match by id (a selected pseudo-row that vanished falls through to clamp).
        new_index = next((i for i, it in enumerate(self.items)
                          if getattr(it, 'id', None) == selected_id), None)
        if selected_id is not None and new_index is not None:
            self._selected = new_index
            self._offset_y = max(0, self._offset_y + (new_index - old_selected))
        if self.items:
            self._selected = max(0, min(self._selected, len(self.items) - 1))
        self.dirty = True

    def prepend_commit(self, item):
        """Insert a freshly-discovered real commit at the front of the
        real-commit sequence, below any leading uncommitted pseudo-rows."""
        insert_at = 0
        while insert_at < len(self.items) and isinstance(self.items[insert_at], UncommittedChangesListItem):
            insert_at += 1
        item._view = self
        self.items.insert(insert_at, item)
        if insert_at <= self._selected:
            self._selected += 1
        if insert_at <= self._offset_y:
            self._offset_y += 1
        self.dirty = True

    def select_commit(self, id:str) -> typing.Optional[CommitListItem]:
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
        if self._focus_head_pending and self.head_id and self.select_commit(self.head_id):
            self._focus_head_pending = False
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

    def select_if_pending(self, id:str):
        """Re-select a commit remembered across a reload (reload_commits), once its
        row streams back in. One-shot, so it clears itself after restoring."""
        if self._pending_select_id and id == self._pending_select_id and self.select_commit(id):
            self._pending_select_id = ''

    def add_to_jump_list(self, commit_id:str, line:typing.Optional[int] = None, offset_y:typing.Optional[int] = None):
        self.jump_list = self.jump_list[self.jump_index:]
        entry = (commit_id, line, offset_y)
        if self.jump_list and self.jump_list[0] == entry:
            return
        self.jump_list.insert(0, entry)
        self.jump_index = 0

    def move_in_jump_list(self, jump:int):
        if not self.jump_list:
            return True
        new_index = self.jump_index + jump
        if not (0 <= new_index < len(self.jump_list)):
            return True

        self.jump_index = new_index
        commit_id, line, offset_y = self.jump_list[new_index]
        is_local = commit_id.startswith('local-')

        # Locate the item in git_log; skip the entry if not found
        idx = None
        for i, item in enumerate(self.items):
            if isinstance(item, (CommitListItem, UncommittedChangesListItem)) and item.id == commit_id:
                idx = i
                break
        if idx is None:
            self.move_in_jump_list(jump)
            return True

        if line is not None:
            self.app.git_diff.job.selected_line_map[commit_id] = (line, offset_y)

        was_same_commit = (self.app.git_diff.commit_id == commit_id
                           and (not self.app.git_diff.is_diff or is_local))

        # Move the git_log cursor without going through GitLogView.set_selected →
        # *ListItem.load_to_view → show_commit/show_diff, which would re-push to
        # the jumplist and clobber forward entries.
        super().set_selected(idx)

        if was_same_commit:
            if line is not None:
                self.app.git_diff.restore_view_position(line, offset_y)
        elif is_local:
            item = self.items[idx]
            self.app.git_diff.job.show_diff('HEAD', cached=item._staged, title=item.txt,
                                            view_id=item.id, add_to_jump_list=False)
        else:
            self.app.git_diff.job.show_commit(commit_id, add_to_jump_list=False)
        return True

    def get_selected_commit_id(self):
        selected_item = self.get_selected()
        # Pseudo-rows have no real sha; report none so callers never feed a
        # 'local-*' id into a git command.
        if selected_item and not isinstance(selected_item, UncommittedChangesListItem):
            return selected_item.id
        return ''

    def cherry_pick(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        if not commit_id:
            self.app.log.warning('Select a commit to cherry-pick')
            return
        Job.run_job(self.app, ['git', 'cherry-pick', '--abort'])
        self.app.run_git(['git', 'cherry-pick', '-m', '1', commit_id],
                        ok=f'Commit {commit_id} cherry picked successfully',
                        err='Error during cherry-pick', refresh_head=True, reload_refs=True)

    def revert(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        if not commit_id:
            self.app.log.warning('Select a commit to revert')
            return
        self.app.run_git(['git', 'revert', '--no-edit', '-m', '1', commit_id],
                        ok=f'Commit {commit_id} reverted successfully',
                        err='Error during revert', refresh_head=True, reload_refs=True)

    def confirm_reset(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        if not commit_id or commit_id.startswith('local'):
            self.app.log.warning('Select a commit to reset the current branch to')
            return
        self.view_reset.open(commit_id)

    def reset(self, mode, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        # refresh_head so --graph mode reloads the log (HEAD moved); it also
        # re-probes uncommitted changes, so check_uncommitted is not needed.
        self.app.run_git(['git', 'reset', mode, commit_id],
                        ok=f'{mode[2:].capitalize()} reset to {commit_id[:8]}',
                        err=f'Error during {mode} reset', refresh_head=True, reload_refs=True)

    def clean_uncommitted_changes(self, staged:bool = False):
        if staged:
            result = Job.run_job(self.app, ['git', 'stash', 'save', '--keep-index'])
            if result.returncode == 0:
                result = Job.run_job(self.app, ['git', 'reset', '--hard'])
            if result.returncode == 0:
                result = Job.run_job(self.app, ['git', 'stash', 'pop'])
        else:
            result = Job.run_job(self.app, ['git', 'restore', '.'])
        if result.returncode == 0:
            self.app.git_refs.reload_refs()
            self.app.git_log.check_uncommitted_changes()
        else:
            self.app.log.error(f"Error cleaning {'staged' if staged else 'unstaged'} changes: {result.stderr}")

    def mark_commit(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        self.marked_commit_id = commit_id
        self.dirty = True
    
    def diff_commits(self, old_commit_id, new_commit_id):
        self.app.git_diff.job.show_diff(old_commit_id, new_commit_id)
        self.app.git_diff.show()

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == ord('q'):
            self.app.exit_program()
        elif key == curses.KEY_EXIT:
            if self.app.split_active():
                self.app.set_split_mode('off')   # Esc on the log pane leaves split view
            else:
                self.app.exit_program()
        elif key == ord('b'):
            self.app.git_refs.view_new_ref.create_ref(self.get_selected_commit_id())
        elif key in (ord('r'), ord('R')):
            self.confirm_reset()
        elif key == ord('c'):
            self.cherry_pick()
        elif key == ord('v'):
            self.revert()
        elif key == ord('m'):
            self.mark_commit()
        elif key == ord('M'):
            self.select_commit(self.marked_commit_id)
        else:
            return super().handle_input(keyboard)
        return True

class GitDiffView(ListView):
    def __init__(self, app):
        super().__init__(app, ID_GIT_DIFF, 'fullscreen')

        self.context_size = 3
        self.rename_limit = 1570
        self.ignore_whitespace = False
        self.job = GitDiffJob(self.app)

        self.commit_id = ''
        self.is_diff = False

        self.set_header_item(WindowTopBarItem('Git commit diff', [
            TextSegment("Context:", 30),
            DynamicTextSegment(lambda: self.app.git_diff.context_size, 30),
            ButtonSegment("[+]", lambda: self.change_context(+1), 30),
            ButtonSegment("[-]", lambda: self.change_context(-1), 30),
            HighlightToggleSegment("[Ignore whitespace]",
                                   lambda: self.app.git_diff.ignore_whitespace,
                                   lambda: self.app.git_diff.change_ignore_whitespace(), 30),
            ButtonSegment("[<-]", lambda: self.app.git_log.move_in_jump_list(+1), 30),
            ButtonSegment("[->]", lambda: self.app.git_log.move_in_jump_list(-1), 30)
        ], title_color = 5))

        self.set_search_dialog(SearchDialogPopup(app, ID_GIT_DIFF_SEARCH))

    def clear(self):
        self.commit_id = ''
        self.is_diff = False
        super().clear()

    def show(self):
        _raise_split_sibling(self, self.app.git_log)
        super().show()

    def _tracks_position(self) -> bool:
        return bool(self.commit_id) and (not self.is_diff or self.commit_id.startswith('local-'))

    def set_selected(self, what:int|str|re.Pattern, visible_mode = 'center') -> bool:
        ret = super().set_selected(what, visible_mode)
        if self._tracks_position():
            self.job.selected_line_map[self.commit_id] = (self._selected, self._offset_y)
        return ret

    def restore_view_position(self, line:int, offset_y:typing.Optional[int] = None):
        self.set_selected(line)
        if offset_y is not None:
            self._offset_y = offset_y
            if self._tracks_position():
                self.job.selected_line_map[self.commit_id] = (self._selected, self._offset_y)

    def select_line(self, file:str, line:int):
        for item in self.items:
            if isinstance(item, DiffListItem) and item.new_file_path == file and item.new_file_line == line:
                self.set_selected(item.line)

    def _reload_diff(self):
        self.clear()
        self.job.selected_line_map.clear()
        self.job.restart_job()

    def change_context(self, size:int):
        self.context_size = max(0, self.context_size + size)
        self._reload_diff()

    def change_ignore_whitespace(self, val:typing.Optional[bool] = None):
        self.ignore_whitespace = not self.ignore_whitespace if val is None else val
        self._reload_diff()

    def handle_input(self, keyboard) -> bool:
        key = keyboard.key
        if self.app.split_active() and (key == ord('q') or key == curses.KEY_EXIT):
            # Esc/q in split view steps back to the log pane and stays split,
            # rather than collapsing the split.
            self.app.git_log.show()
            return True
        if key == KEY_CTRL('n'):
            self.app.git_log.handle_input(KeyboardState(curses.KEY_DOWN))
        elif key == KEY_CTRL('p'):
            self.app.git_log.handle_input(KeyboardState(curses.KEY_UP))
        elif key in (ord('g'), ord('G'), curses.KEY_HOME, curses.KEY_END):
            track = self._tracks_position()
            if track:
                self.app.git_log.add_to_jump_list(self.commit_id, self._selected, self._offset_y)
            ret = super().handle_input(keyboard)
            if track:
                self.app.git_log.add_to_jump_list(self.commit_id, self._selected, self._offset_y)
            return ret
        else:
            return super().handle_input(keyboard)
        return True

class GitRefsView(ListView):
    def __init__(self, app):
        super().__init__(app, ID_GIT_REFS) 

        self.refs = {} # map: git_id --> [ { 'type':<ref-type>, 'name':<ref-name> } ]

        self.set_header_item(WindowTopBarItem('Git references', title_color = 5))
        self.set_search_dialog(SearchDialogPopup(app, ID_GIT_REFS_SEARCH))

        self.view_new_ref = NewRefDialogPopup(app)
        self.view_ref_push = RefPushDialogPopup(app)

        self.job = GitRefsJob(self.app)

    def reload_refs(self):
        self.clear()
        self.job.start_job()

class LogView(ListView):
    def __init__(self, app):
        super().__init__(app, ID_LOG, 'fullscreen') 

        self.set_header_item(WindowTopBarItem('Logs', [
            ButtonSegment("[Clear]", lambda: self.clear(), 30),
            HighlightToggleSegment("[Autoscroll]", lambda: self.autoscroll, self.toggle_autoscroll, 30),
            TextSegment("  Log level:", 30),
            DynamicTextSegment(lambda: self.app.log.level, 30),
            ButtonSegment("[+]", lambda: self.change_log_level(+1), 30),
            ButtonSegment("[-]", lambda: self.change_log_level(-1), 30)], title_color = 5))

        self.set_search_dialog(SearchDialogPopup(app, ID_LOG_SEARCH))

    def change_log_level(self, value):
        self.app.log.level = max(0, min(5, self.app.log.level + value))
        self.dirty = True

    def toggle_autoscroll(self):
        self.autoscroll = not self.autoscroll
        if self.autoscroll:
            self._offset_y = max(0, len(self.items) - self.height)
        self.dirty = True

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
                self.append(ContextMenuItem("Rename this branch", self.app.git_refs.view_new_ref.create_ref, [item.data['name']]))
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
