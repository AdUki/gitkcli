"""gitk's dialogs: reset, ref-push, git command, preferences, new-ref, the
commit search, and the commit message. (Generic popups - message boxes, the input and search
prompts - come from tuikit.dialogs.)

They reach the views and services through the App struct (`self.app`).
"""

from __future__ import annotations

import curses
import os
import re
import shlex

from tuikit import keys as K
from tuikit.dialogs import Dialog, InputDialog, SearchDialog
from tuikit import colors
from tuikit.items import (
    Item,
    SeparatorItem,
    SpacerListItem,
    TextListItem,
    UserInputListItem,
)
from tuikit.segmented import LabeledRow, SegmentedListItem, button_row
from tuikit.segments import (
    ButtonSegment,
    ChoiceSegment,
    FillerSegment,
    OnOffToggleSegment,
    TextSegment,
    ToggleSegment,
)
from tuikit.view import MODE_FULLSCREEN

from gitk import keymap as KM
from gitk.config import update_config
from gitk.ids import (
    ID_COMMIT,
    ID_GIT_COMMAND,
    ID_GIT_LOG_SEARCH,
    ID_GIT_REF_PUSH,
    ID_GIT_RESET,
    ID_INIT_REPO,
    ID_NEW_GIT_REF,
    ID_PREFERENCES,
    ID_SHORTCUTS,
)
from gitk.items import ResetModeItem
from gitk.jobs import Job
from gitk.screen import Screen
from gitk.segments import ref_color_and_title
from gitk.split_layout import SPLIT_OFF, SPLIT_SIDE, SPLIT_STACKED


class ResetDialogPopup(Dialog):
    def __init__(self, app):
        super().__init__(app, ID_GIT_RESET, " Reset current branch")
        self.commit_id = ""
        self.selected_mode = "--mixed"

        self.target_item = TextListItem("", Screen.C_GIT_ID, is_selectable=False)
        self.append(self.target_item)
        self.append(SeparatorItem())
        self.append(
            ResetModeItem(
                self,
                "--soft",
                "  Soft    keep index + working tree (move HEAD)",
                Screen.C_STATUS,
            )
        )
        self.append(
            ResetModeItem(
                self, "--mixed", "  Mixed   reset index, keep working tree (default)"
            )
        )
        self.append(
            ResetModeItem(
                self,
                "--hard",
                "  Hard    discard index + working tree changes",
                Screen.C_ERROR,
            )
        )
        self.append(SeparatorItem())
        self.set_button_row(
            button_row(
                ButtonSegment("[Ok]", self._confirm, Screen.C_STATUS),
                TextSegment("   "),
                ButtonSegment("[Cancel]", self.hide),
            )
        )

    def _confirm(self):
        # [Ok] applies whichever reset mode is currently highlighted.
        self.hide()
        self.app.git_log.reset(self.selected_mode, self.commit_id)
        return True

    def set_selected(self, what, visible_mode="center"):
        # Track the highlighted mode (keyboard AND mouse funnel through here) so
        # [Ok] knows which reset to run once focus moves to the buttons row.
        result = super().set_selected(what, visible_mode)
        item = self.get_selected()
        if isinstance(item, ResetModeItem):
            self.selected_mode = item.mode
        return result

    def open(self, commit_id):
        self.commit_id = commit_id
        self.selected_mode = "--mixed"
        title = self.app.git_log.commits.get(commit_id, {}).get("title", "")
        if len(title) > 34:  # the dialog packs around it: keep it compact
            title = title[:33] + "…"
        self.target_item.set_text(f"  Reset HEAD → {commit_id[:8]}  {title}")
        self.set_selected(3)  # highlight Mixed by default
        self._button_row.reset_focus()
        self.show()

    def handle_key(self, event):
        key = event.key
        if key in (curses.KEY_EXIT, ord("q")):
            self.hide()
            return True
        return super().handle_key(event)


class RefPushDialogPopup(Dialog):
    def __init__(self, app):
        super().__init__(app, ID_GIT_REF_PUSH)

        # `git remote` prints one name per line (none in a repo with no
        # remotes). split() drops the trailing/empty lines so a remote-less repo
        # yields [] rather than [''] (which would make a blank toggle and push
        # to an empty remote name).
        self.remote = ""
        self.remotes = [
            ToggleSegment(name, callback=lambda val: self.change_remote(val.txt))
            for name in Job.run_job(self.app, ["git", "remote"]).stdout.split()
        ]
        if self.remotes:
            self.change_remote(self.remotes[0].txt)

        self.force = ToggleSegment("<Force>")
        self.append(
            SegmentedListItem(
                [TextSegment("Select remote:")]
                + self.remotes
                + [FillerSegment(), TextSegment("Flags:"), self.force]
            )
        )

        self.append(SpacerListItem())
        self.set_button_row(
            button_row(
                ButtonSegment("[Push]", self._confirm),
                ButtonSegment("[Cancel]", self.hide),
            )
        )
        self.ref_name = ""

        # Make the buttons row navigable (Left/Right pick a button, Enter
        # activates it); default focus is [Push] so a bare Enter still pushes.
        self.focus_button_row()

    def _confirm(self):
        self.hide()
        self.push_ref()
        return True

    def on_activated(self):
        self._button_row.reset_focus()
        self._selected = len(self.items) - 1
        super().on_activated()

    def change_remote(self, new_remote):
        self.remote = new_remote
        for remote in self.remotes:
            remote.toggled = remote.txt == self.remote

    def clear(self):
        self.force.toggled = False

    def push_ref(self):
        # No configured remote (or none selected): pushing would run
        # `git push "" <ref>` -> a cryptic "fatal: no path specified" error
        # dialog. Skip with a warning instead.
        if not self.remote:
            self.app.log.warning(f"No remote to push '{self.ref_name}' to")
            return
        self._do_push(self.remote, self.ref_name, self.force.toggled)

    def _do_push(self, remote, ref_name, force):
        args = ["git", "push"] + (["-f"] if force else []) + [remote, ref_name]
        self.app.run_git(
            args,
            ok=f"Branch pushed {ref_name} to {remote}",
            err=f"Error pushing ref '{ref_name}'",
            reload_refs=True,
            force=force,
            reasons=("non-fast-forward", "fetch first", "would clobber"),
            retry=lambda: self._do_push(remote, ref_name, True),
            title=" Push rejected",
            lines=[
                (f"Push of '{ref_name}' to '{remote}' was rejected.", Screen.C_GIT_ID),
                "The remote has changes you don't have locally.",
                ("Force push? This may overwrite remote commits.", Screen.C_ERROR),
            ],
            label="[Force push]",
        )

    def handle_key(self, event):
        key = event.key
        # Enter is routed through the buttons row (super -> ButtonRowItem) so it
        # activates the focused button instead of always pushing.
        if key == curses.KEY_EXIT:
            self.hide()
        elif key == curses.KEY_F1:
            self.force.toggle()
        elif key == K.KEY_TAB:  # cycle through remotes
            names = [r.txt for r in self.remotes]
            if (
                names
            ):  # a repo with no remotes has none to cycle (avoids index/%0 errors)
                self.change_remote(names[(names.index(self.remote) + 1) % len(names)])
        else:
            return super().handle_key(event)
        return True


class InsertChipSegment(ButtonSegment):
    """A clickable chip in the command dialog's helper row: clicking it pastes
    its token (the commit id or a ref name) at the input cursor. It is
    highlighted while it is the current target of the Tab cycle."""

    def __init__(self, dialog, index, label, color):
        super().__init__(label, lambda: dialog.pick_chip(index), color)
        self.dialog = dialog
        self.index = index

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        # `marked` (highlight background) tracks the Tab cycle, not the row.
        return super().draw(
            win, offset, width, selected, matched, self.dialog.cycle_index == self.index
        )


class CommandDialogPopup(InputDialog):
    """F8: run an arbitrary git command. The leading `git` is implied, so the
    user types just the arguments (e.g. `checkout main`, `stash pop`).

    The helper row offers the selected commit's insertable tokens: its 7-char
    abbreviated id first, then every ref on it (branches, tags, remotes). Tab
    cycles through them, pasting each in place of the last (like F7 cycling a
    row's ref menus); clicking a chip pastes that one. Up/Down browse the
    command history like the search dialogs.

    Every command runs with the curses UI suspended (Screen.run_interactive):
    the child inherits the real terminal, so whatever it needs just works - an
    editor (`rebase -i`, `commit`), an interactive prompt (`add -p`), or a
    pager (git auto-pages `log`/`diff` since stdout is a real TTY). There is no
    fragile "is this interactive?" guess; the user sees the raw output and
    presses Enter to return."""

    def __init__(self, app):
        # Header: the `$ git ` prompt on the left, the insert chips on the right.
        # The chips are rebuilt for the selected commit each time we open.
        self.prompt = TextSegment("$ git ", Screen.C_GIT_ID)
        self.chip_row = SegmentedListItem([self.prompt])
        self.insert_tokens = []
        self.cycle_index = -1  # index of the last chip pasted via Tab / click
        self._tab_span = None  # (start, end) of that paste, for replace-in-place
        super().__init__(app, ID_GIT_COMMAND, " Git command", self.chip_row)

    def _build_chips(self):
        """Rebuild the helper row from the selected commit's id + refs."""
        commit_id = self.app.git_log.get_selected_commit_id()
        self.insert_tokens = []
        segments = [self.prompt, FillerSegment(), TextSegment("Insert (Tab): ")]
        if commit_id:
            self.insert_tokens.append(commit_id[:7])
            segments.append(
                InsertChipSegment(self, 0, f"[{commit_id[:7]}]", Screen.C_GIT_ID)
            )
            head_branch = self.app.git_log.head_branch
            for ref in self.app.git_refs.refs.get(commit_id, []):
                if ref["type"] == "head":
                    continue  # the HEAD pseudo-ref duplicates the commit id chip
                color, title = ref_color_and_title(ref, head_branch)
                segments.append(TextSegment(" "))
                segments.append(
                    InsertChipSegment(self, len(self.insert_tokens), title, color)
                )
                self.insert_tokens.append(ref["name"])
        else:
            segments.append(TextSegment("(no commit selected)", Screen.C_DIM))
        self.chip_row.set_segments(segments)
        self.cycle_index = -1
        self._tab_span = None

    def _insert_token(self, token, replace_span=None):
        inp = self.input
        if replace_span:  # replace the previous Tab-pasted token in place
            start, end = replace_span
            inp.txt = inp.txt[:start] + inp.txt[end:]
            inp.cursor_pos = start
        start = inp.cursor_pos
        inp.insert(token)
        self._tab_span = (start, inp.cursor_pos)
        self.dirty = True

    def _active_span(self):
        """The previous paste's span if it is still intact right before the
        cursor (so Tab / another chip can replace it), else None."""
        if self._tab_span is None or not (
            0 <= self.cycle_index < len(self.insert_tokens)
        ):
            return None
        start, end = self._tab_span
        if (
            self.input.cursor_pos == end
            and self.input.txt[start:end] == (self.insert_tokens[self.cycle_index])
        ):
            return self._tab_span
        return None

    def cycle_insert(self):
        """Tab: paste the next insertable token, replacing the last one in place."""
        if not self.insert_tokens:
            self.app.log.warning("No commit selected to insert")
            return True
        replace = self._active_span()
        self.cycle_index = (self.cycle_index + 1) % len(self.insert_tokens)
        self._insert_token(self.insert_tokens[self.cycle_index], replace)
        return True

    def pick_chip(self, index):
        """Mouse: paste the clicked chip (replacing the last paste if intact)."""
        replace = self._active_span()
        self.cycle_index = index
        self._insert_token(self.insert_tokens[index], replace)
        self._selected = 1  # keep keyboard focus on the input field
        return True

    def on_activated(self):
        # Reused singleton: start each open with an empty field and fresh chips.
        self.clear()
        self._build_chips()
        self._selected = 1
        super().on_activated()

    def handle_key(self, event):
        if event.key == K.KEY_TAB:
            return self.cycle_insert()
        # Any edit / cursor move ends the in-place replace chain, so the next Tab
        # pastes fresh at the cursor rather than clobbering unrelated text.
        self._tab_span = None
        return super().handle_key(event)

    def execute(self):
        command = self.input.txt.strip()
        if not command:
            return  # bare Enter on an empty field is a no-op (dialog already hid)
        try:
            args = shlex.split(command)
        except ValueError as e:
            # Unbalanced quotes etc. — report instead of running a broken command.
            self.app.log.error(f"Could not parse command: {e}")
            return
        super().execute()  # record the raw command in the history

        # Run every command with the UI suspended and the real terminal handed
        # to the child (see the class docstring). No captured-output path and no
        # interactivity guess: an editor (`rebase -i`), a prompt (`add -p`), or a
        # pager all just work, and the user reads the raw output before Enter.
        rc = self.app.screen.run_interactive(["git"] + args)
        # Record the outcome in the Log view (F4) only - no green success flash
        # and no red error dialog. The suspended console already showed the exit
        # status (coloured green/red), so both would just be noise on return.
        if rc == 0:
            self.app.log.log(Screen.C_NORMAL, f"git {command}")
        elif rc is not None:
            self.app.log.log(
                Screen.C_ERROR, f"Error running: git {command} (exit {rc})"
            )

        # A command can move HEAD, change refs, rewrite history (rebase), or
        # dirty the working tree - fully reload refs + log rather than the
        # incremental refresh, and re-probe the uncommitted pseudo-rows.
        self.app.reload_refs_commits()
        self.app.git_log.check_uncommitted_changes()


class PreferencesDialogPopup(Dialog):
    def __init__(self, app):
        super().__init__(app, ID_PREFERENCES, " Preferences")

        self.t_show_id = OnOffToggleSegment()
        self.t_show_date = OnOffToggleSegment()
        self.t_show_author = OnOffToggleSegment()
        self.t_ign_ws = OnOffToggleSegment()
        self.t_autoscroll = OnOffToggleSegment()
        self.c_view_mode = ChoiceSegment(
            [
                (MODE_FULLSCREEN, "Fullscreen"),
                (SPLIT_SIDE, "Horizontal split"),
                (SPLIT_STACKED, "Vertical split"),
            ],
            MODE_FULLSCREEN,
        )
        self.input_flags = UserInputListItem()

        self.append(LabeledRow("Show commit ID", self.t_show_id))
        self.append(LabeledRow("Show commit date", self.t_show_date))
        self.append(LabeledRow("Show commit author", self.t_show_author))
        self.append(SeparatorItem())
        self.append(LabeledRow("Ignore whitespace (diff)", self.t_ign_ws))
        self.append(SeparatorItem())
        self.append(LabeledRow("Autoscroll (log view)", self.t_autoscroll))
        self.append(SeparatorItem())
        self.append(LabeledRow("Default view mode", self.c_view_mode))
        self.append(SeparatorItem())
        self.append(
            LabeledRow(
                "Keyboard shortcuts",
                ButtonSegment("[Edit]", lambda: self.app.shortcuts.show()),
            )
        )
        self.append(SeparatorItem())
        self.append(TextListItem("  Git log default flags:", is_selectable=False))
        self.append(self.input_flags)

        self.set_button_row(
            button_row(
                ButtonSegment("[Save]", self.on_save),
                TextSegment("  "),
                ButtonSegment("[Close]", self.on_cancel),
            )
        )
        self._selected = 0

    def on_activated(self):
        self.t_show_id.set_toggled(self.app.git_log.show_commit_id)
        self.t_show_date.set_toggled(self.app.git_log.show_commit_date)
        self.t_show_author.set_toggled(self.app.git_log.show_commit_author)
        self.t_ign_ws.set_toggled(self.app.git_diff.ignore_whitespace)
        self.t_autoscroll.set_toggled(self.app.log.view.autoscroll)
        self.c_view_mode.set_value(self.app.split.default_view_mode)
        self.input_flags.set_text(self.app.git_log.pref_flags)
        self._button_row.reset_focus()
        self.dirty = True
        super().on_activated()

    def _apply(self, flags=True):
        """Make the dialog's values take effect now (Save only persists them).
        Each setting is applied only when it differs from what is in effect,
        so an untouched "Default view mode" never re-lays out the views."""
        git_log, log_view = self.app.git_log, self.app.log.view
        shown = (
            git_log.show_commit_id,
            git_log.show_commit_date,
            git_log.show_commit_author,
        )
        wanted = (
            self.t_show_id.toggled,
            self.t_show_date.toggled,
            self.t_show_author.toggled,
        )
        if shown != wanted:
            (
                git_log.show_commit_id,
                git_log.show_commit_date,
                git_log.show_commit_author,
            ) = wanted
            git_log.dirty = True
        if log_view.autoscroll != self.t_autoscroll.toggled:
            log_view.autoscroll = self.t_autoscroll.toggled
            log_view.dirty = True
        if self.app.git_diff.ignore_whitespace != self.t_ign_ws.toggled:
            self.app.git_diff.change_ignore_whitespace(self.t_ign_ws.toggled)
        # The flags reload the log, so they apply once the field is left (or
        # the dialog saved or closed), not on every keystroke.
        new_flags = self.input_flags.txt.strip()
        if flags and new_flags != git_log.pref_flags:
            git_log.set_pref_flags(new_flags)
            git_log.reload_commits()
        split = self.app.split
        if self.c_view_mode.value != split.default_view_mode:
            split.default_view_mode = self.c_view_mode.value
            split.set_split_mode(
                split.default_view_mode
                if split.default_view_mode in (SPLIT_SIDE, SPLIT_STACKED)
                else SPLIT_OFF
            )
            # Entering a split raises the log/diff panes: keep this on top.
            self.show()

    def on_save(self):
        self._apply()
        new_flags = self.input_flags.txt.strip()

        sections = {
            "git_log": {
                "show_commit_id": self.t_show_id.toggled,
                "show_commit_date": self.t_show_date.toggled,
                "show_commit_author": self.t_show_author.toggled,
                "flags": new_flags,
                # not edited here; keep what the config file has
                "graph_dot": self.app.git_log.graph_dot,
            },
            "git_diff": {"ignore_whitespace": self.t_ign_ws.toggled},
            "log": {"autoscroll": self.t_autoscroll.toggled},
            "view": {"default_mode": self.c_view_mode.value},
        }
        # The shortcut editor saves its own section; leave "keys" as saved.
        if update_config(sections, self.app):
            self.app.log.success("Preferences saved")

    def on_cancel(self):
        self.hide()

    def on_deactivated(self):
        super().on_deactivated()
        if not self.is_shown():
            self._apply()  # closed with the flags field still being edited

    def _apply_after(self, handled):
        if self.is_active():
            self._apply(flags=self.get_selected() is not self.input_flags)
        return handled

    def handle_key(self, event):
        key = event.key
        if key == curses.KEY_EXIT:
            self.on_cancel()
            return True
        return self._apply_after(super().handle_key(event))

    def handle_mouse(self, event):
        return self._apply_after(super().handle_mouse(event))


class ShortcutRow(Item):
    """`  Cherry-pick this commit ........ c  ` in the shortcut editor; Enter
    (or a double-click) asks for a new key."""

    KEY_COLUMN = len("<press a key>")

    def __init__(self, dialog, action):
        super().__init__()
        self.dialog = dialog
        self.action = action

    def get_text(self):
        return "  " + self.action.label

    def natural_width(self) -> int:
        return len(self.get_text()) + 3 + self.KEY_COLUMN + 2

    def activate(self) -> bool:
        self.dialog.start_capture(self)
        return True

    def draw_line(self, win, offset, width, selected, matched, marked):
        def attr(color, dim=False):
            return colors.attr(color, selected, dim=dim)

        if self.dialog.capturing is self:
            key, color, dim = "<press a key>", Screen.C_MATCH, False
        else:
            spec = self.get_app().keymap.spec(self.action.id)
            key, color, dim = (spec, Screen.C_DATA, False) if spec else ("-", 0, True)
            color = color or Screen.C_DIM
        right = key + "  "
        left = self.get_text()[: max(0, width - len(right))]
        win.addstr(left, attr(Screen.C_NORMAL))
        win.addstr(" " * max(0, width - len(left) - len(right)), attr(Screen.C_NORMAL))
        win.addstr(right[: max(0, width - len(left))], attr(color, dim))


class ShortcutsDialogPopup(Dialog):
    """The shortcut editor (Preferences > Keyboard shortcuts): every keymap
    action by scope with its key. Enter on a row waits for the new key (Esc
    cancels); Backspace / Del unbind. A key another action already uses
    where this one is live moves over to it. Changes apply at once; [Save]
    persists them, [Defaults] restores the built-in keys."""

    def __init__(self, app):
        super().__init__(app, ID_SHORTCUTS, " Keyboard shortcuts")
        self.capturing = None  # the ShortcutRow waiting for a key
        for scope, title in KM.SCOPES:
            self.append(TextListItem(f" {title}", Screen.C_DATA, is_selectable=False))
            for action in KM.ACTIONS:
                if action.scope == scope:
                    self.append(ShortcutRow(self, action))
        self.append(SeparatorItem())
        self.append(
            TextListItem(
                "  Enter: change key   Backspace: unbind",
                Screen.C_DIM,
                is_selectable=False,
            )
        )
        self.set_button_row(
            button_row(
                ButtonSegment("[Save]", self.on_save),
                TextSegment("  "),
                ButtonSegment("[Defaults]", self.on_defaults),
                TextSegment("  "),
                ButtonSegment("[Close]", self.hide),
            )
        )
        self._selected = 1  # the first action row

    def on_activated(self):
        self.capturing = None
        self._button_row.reset_focus()
        self.dirty = True
        super().on_activated()

    def start_capture(self, row):
        self.capturing = row
        self.dirty = True

    def bind(self, action, key):
        """Put `action` on `key`, unless the key cannot be bound; a key in use
        where `action` is live moves over (and the bar says from where)."""
        keymap = self.app.keymap
        spec = KM.format_key(key)
        if spec is None:
            self.app.screen.show_flash("That key cannot be bound")
            return
        if key in KM.RESERVED:
            self.app.screen.show_flash(f"{spec} is reserved for navigation")
            return
        other = keymap.conflict(action.id, key)
        if other:
            keymap.bind(other, None)
            label = KM.ACTIONS_BY_ID[other].label
            self.app.screen.show_flash(f"{spec} moved here from '{label}'")
        keymap.bind(action.id, key)

    def on_save(self):
        if update_config({"keys": self.app.keymap.overrides()}, self.app):
            self.app.log.success("Shortcuts saved")
        return True

    def on_defaults(self):
        self.app.keymap.reset()
        self.dirty = True
        return True

    def handle_key(self, event):
        key = event.key
        if self.capturing is not None:
            row, self.capturing = self.capturing, None
            self.dirty = True
            if key != curses.KEY_EXIT:
                self.bind(row.action, key)
            return True  # the key was for the binding, not for the list
        item = self.get_selected()
        if isinstance(item, ShortcutRow) and (
            key in K.BACKSPACE_KEYS or key == curses.KEY_DC
        ):
            self.app.keymap.bind(item.action.id, None)
            self.dirty = True
            return True
        if key == curses.KEY_EXIT:
            self.hide()
            return True
        return super().handle_key(event)

    def handle_mouse(self, event):
        if self.capturing is not None and not event.is_move:
            self.capturing = None  # a click elsewhere gives up waiting
            self.dirty = True
        return super().handle_mouse(event)


class NewRefDialogPopup(InputDialog):
    def __init__(self, app):
        self.force = ToggleSegment("<Force>")
        self.commit_id = ""
        self.ref_type = ""  # branch or tag
        self.rename_from = ""  # branch being renamed; '' means create mode
        self.prompt = TextSegment("Specify the new branch name:")
        super().__init__(
            app,
            ID_NEW_GIT_REF,
            " New Branch",
            SegmentedListItem(
                [self.prompt, FillerSegment(), TextSegment("Flags:"), self.force]
            ),
        )

    def create_ref(self, commit_id, ref_type="branch"):
        # get_selected_commit_id() returns '' on the uncommitted pseudo-rows, so
        # 'b' there would open the dialog and later run `git <type> <name> ''`
        # -> "fatal: not a valid object name: ''". Refuse without a real target
        # (mirrors the cherry-pick / revert / reset guards).
        if not commit_id:
            self.app.log.warning(f"Select a commit to create a {ref_type} from")
            return
        self.rename_from = ""
        self.commit_id = commit_id
        self.ref_type = ref_type
        self.set_title(f" New {ref_type.capitalize()}")
        self.prompt.set_text(f"Specify the new {ref_type} name:")
        self.clear()
        self.show()

    def rename_branch(self, old_name):
        # Rename with `git branch -m`, not `git branch <new> <old>` (which copies
        # and leaves the original branch in place).
        if not old_name:
            self.app.log.warning("Select a branch to rename")
            return
        self.rename_from = old_name
        self.ref_type = "branch"
        self.set_title(" Rename Branch")
        self.prompt.set_text(f"Rename '{old_name}' to:")
        self.clear()
        self.show()

    def clear(self):
        self.force.toggled = False
        super().clear()

    def handle_key(self, event):
        key = event.key
        if key == curses.KEY_F1:
            self.force.toggle()
        else:
            return super().handle_key(event)
        return True

    def execute(self):
        # An empty name would run `git branch "" <commit>` -> "fatal: '' is not a
        # valid branch name" -> a spurious red error dialog. Treat empty Enter as
        # a cancel (the base handler already hid the dialog).
        if not self.input.txt.strip():
            return
        if self.rename_from:
            self._rename_branch(self.rename_from, self.input.txt, self.force.toggled)
        else:
            self._create_ref(
                self.ref_type, self.input.txt, self.commit_id, self.force.toggled
            )
        super().execute()

    def _rename_branch(self, old_name, new_name, force):
        # -M is -m plus overwrite; only used after the user confirms the
        # "already exists" force prompt.
        args = ["git", "branch", "-M" if force else "-m", old_name, new_name]
        self.app.run_git(
            args,
            ok=f"Renamed branch {old_name} to {new_name}",
            err="Error renaming branch",
            reload_refs=True,
            refresh_head=True,
            force=force,
            reasons=("already exists",),
            retry=lambda: self._rename_branch(old_name, new_name, True),
            title=" Branch already exists",
            lines=[
                (f"A branch named '{new_name}' already exists.", Screen.C_GIT_ID),
                "Overwrite it? (uses git branch -M)",
            ],
            label="[Overwrite]",
            # Local and instant; the renamed branch shows up in the refs list.
            quiet=True,
        )

    def _create_ref(self, ref_type, name, commit_id, force):
        args = ["git", ref_type] + (["-f"] if force else []) + [name, commit_id]
        self.app.run_git(
            args,
            ok=f"{ref_type} {name} created successfully",
            err=f"Error creating {ref_type}",
            reload_refs=True,
            force=force,
            reasons=("already exists",),
            retry=lambda: self._create_ref(ref_type, name, commit_id, True),
            title=f" {ref_type.capitalize()} already exists",
            lines=[
                (f"A {ref_type} named '{name}' already exists.", Screen.C_GIT_ID),
                f"Overwrite it? (uses git {ref_type} --force)",
            ],
            label="[Overwrite]",
            # Purely local and instant; the new ref shows up in the refs list.
            quiet=True,
        )


# git's pickaxe argument (-G) is always a POSIX extended regex, so a literal
# "Diff" query has to escape the ERE metacharacters itself. Only those are
# escaped: a backslash before an ordinary character is undefined in ERE.
_ERE_SPECIAL = re.compile(r"([.^$*+?()\[\]{}|\\])")


class GitSearchDialogPopup(SearchDialog):
    _WORKING_BAR = "Working: searching commits ..."
    _TYPES = [
        ("txt", "[Txt]"),
        ("id", "[ID]"),
        ("message", "[Message]"),
        ("path", "[Filepaths]"),
        ("diff", "[Diff]"),
    ]

    def __init__(self, app):
        super().__init__(app, ID_GIT_LOG_SEARCH)
        self._type_segments = [
            (
                t,
                ToggleSegment(
                    label, callback=lambda val, t=t: self.change_search_type(t)
                ),
            )
            for t, label in self._TYPES
        ]
        self.header.segments[0:0] = [TextSegment("Type:")] + [
            s for _, s in self._type_segments
        ]
        self.change_search_type("txt")

    def change_search_type(self, new_type):
        self.search_type = new_type
        for t, seg in self._type_segments:
            seg.toggled = t == new_type
        self.use_regexp.enabled = self.case_sensitive.enabled = new_type != "path"

    def matches(self, item):
        if self.search_type == "txt":
            return super().matches(item)
        elif hasattr(item, "id"):
            return item.id in self.app.git_log.job_search.found_ids
        return False

    def handle_key(self, event):
        key = event.key
        if key in K.ENTER_KEYS:
            if self.search_type == "txt":
                return super().handle_key(event)

            if not self.input.txt:
                # An empty query would launch a doomed git command — e.g. ID
                # search runs `git log ^!` → "fatal: bad revision" → a spurious
                # red error dialog. Swallow Enter and keep the prompt open.
                return True

            self.hide()

            args = []
            if not self.case_sensitive.toggled:
                args.append("-i")
            if self.search_type == "message":
                if not self.use_regexp.toggled:
                    args.append("-F")
                args.append("--grep")
                args.append(self.input.txt)
            elif self.search_type == "id":
                args.append(f"{self.input.txt}^!")
            elif self.search_type == "diff":
                # Always -G ("changed lines matching"), never -S: -S is a
                # pickaxe on the *number of occurrences* of the string, so a
                # commit that rewrites a line without adding or removing an
                # occurrence (`setValue(..., "Speaker")` -> `..., "Speaker\n")`)
                # does not match - surprising for a "search the diff" prompt.
                args.append("-G")
                args.append(
                    self.input.txt
                    if self.use_regexp.toggled
                    else _ERE_SPECIAL.sub(r"\\\1", self.input.txt)
                )
            elif self.search_type == "path":
                args.append("--")
                args.append(f"*{self.input.txt}*")

            self.add_query_to_history()
            # dim=False: the search runs off the event loop, so the UI stays
            # live - the bar is only a "still searching" hint, which a diff
            # search over a big repo needs (it can take tens of seconds).
            self.app.screen.show_working(self._WORKING_BAR, dim=False)
            self.app.git_log.job_search.start_job(
                args,
                on_finished=self._search_finished,
                scoped=self.search_type != "id",
            )

        elif key == K.KEY_TAB:  # cycle through search types
            self.parent_list_view.dirty = True
            types = [t for t, _ in self._TYPES]
            self.change_search_type(
                types[(types.index(self.search_type) + 1) % len(types)]
            )

        else:
            return super().handle_key(event)

        return True

    def _search_finished(self):
        """Move the cursor onto a match once the git search returns, the way
        Enter does for a [Txt] search. The job only fills found_ids, so without
        this a search that took seconds ends with no visible effect beyond
        highlighting rows the user may be thousands of lines away from.

        A search that lands announces itself (the cursor moved, the row is
        highlighted); one that finds nothing to land on has nothing to show, so
        say so on the bottom bar."""
        # Only drop the bar if it is still ours: a reload started meanwhile
        # (which owns the bar until its first rows arrive) must keep its own.
        if self.app.screen.working_message == self._WORKING_BAR:
            self.app.screen.clear_working()
        found = len(self.app.git_log.job_search.found_ids)
        if not found:
            self.app.screen.show_flash(f"No commit matches '{self.input.txt}'")
        elif not self.parent_list_view.search(repeat=True):
            # Matches exist but none is in the log view - e.g. the log was
            # opened on revisions that exclude them.
            plural = "" if found == 1 else "es"
            self.app.screen.show_flash(
                f"{found} match{plural} for '{self.input.txt}', none in this log"
            )


class MessageLineItem(UserInputListItem):
    """A line of the commit message: a text field whose cursor shows only
    while it is the selected line."""

    def draw_line(self, win, offset, width, selected, matched, marked):
        if selected:
            super().draw_line(win, offset, width, False, matched, marked)
            return
        txt = self.txt[self.offset :][:width]
        win.addstr(txt, colors.attr(self.color))
        win.addstr(" " * (width - len(txt)))


class CommitDialogPopup(Dialog):
    """Commit the staged changes: a one-line summary, a free multi-line
    description, and <Amend> (F1) to redo the last commit instead (its
    message is loaded when the fields are empty).

    Enter in the summary commits; in the description it starts a new line
    (Backspace at a line's start / Del at its end join lines). Up/Down move
    between lines, Tab between the summary, the description and the buttons.
    Esc closes the dialog keeping the draft for the next time."""

    BODY_LINES = 6  # the description's lines shown even when empty
    min_width = 72

    def __init__(self, app):
        super().__init__(app, ID_COMMIT, " Commit")
        self.amend = ToggleSegment("<Amend>", callback=self._amend_toggled)
        self._loaded_message = ""  # the amended commit's message, as loaded
        self.summary = MessageLineItem()
        self.body = [MessageLineItem() for _ in range(self.BODY_LINES)]
        self.buttons = button_row(
            ButtonSegment("[Commit]", self.commit, Screen.C_STATUS),
            TextSegment("   "),
            ButtonSegment("[Cancel]", self.hide),
        )
        self._rebuild()

    # rows ---------------------------------------------------------------------

    def _label(self, text):
        return TextListItem(text, Screen.C_DATA, is_selectable=False)

    def _rebuild(self):
        """Lay the rows out again (the description grew or shrank)."""
        selected = self.get_selected()
        offset_y = self._offset_y
        self.items = []
        self.append(
            SegmentedListItem(
                [
                    TextSegment("Summary", Screen.C_DATA),
                    FillerSegment(),
                    TextSegment("Flags:"),
                    self.amend,
                    TextSegment("(F1)", Screen.C_DIM),
                ]
            )
        )
        self.items[-1].is_selectable = False
        self.append(self.summary)
        self.append(self._label("Description"))
        for line in self.body:
            self.append(line)
        self.append(SpacerListItem())
        self.append(
            TextListItem(
                "Enter in the summary commits · Tab: next field · Esc: close",
                Screen.C_DIM,
                is_selectable=False,
            )
        )
        self.set_button_row(self.buttons)
        self._selected = self.items.index(selected) if selected in self.items else 1
        self._offset_y = offset_y
        if self.is_active():
            self.pack(min_width=self.min_width, padx=self.padx)
        self.dirty = True

    # message ------------------------------------------------------------------

    def message(self) -> str:
        """The message as git gets it: the summary, a blank line, the
        description (trailing blank lines dropped)."""
        body = [line.txt for line in self.body]
        while body and not body[-1].strip():
            body.pop()
        summary = self.summary.txt.strip()
        if not body:
            return summary
        return summary + "\n\n" + "\n".join(body)

    def set_message(self, text: str):
        lines = text.rstrip("\n").split("\n") if text.strip() else [""]
        self.summary.set_text(lines[0])
        rest = lines[1:]
        if rest and not rest[0].strip():
            rest = rest[1:]
        self.body = [MessageLineItem() for _ in range(max(self.BODY_LINES, len(rest)))]
        for item, txt in zip(self.body, rest):
            item.set_text(txt)
        self._rebuild()

    def _amend_toggled(self, segment):
        if segment.toggled:
            if not self.message():
                result = Job.run_job(self.app, ["git", "log", "-1", "--format=%B"])
                if result.returncode == 0:
                    self._loaded_message = result.stdout.strip()
                    self.set_message(self._loaded_message)
        elif self._loaded_message and self.message() == self._loaded_message:
            self.set_message("")  # the loaded message, untouched: drop it
            self._loaded_message = ""
        self.dirty = True

    # opening / committing -----------------------------------------------------

    def open(self):
        self.buttons.reset_focus()
        self.summary.cursor_pos = len(self.summary.txt)
        self._selected = self.items.index(self.summary)
        self.show()

    def commit(self):
        message = self.message()
        amend = self.amend.toggled
        staged = Job.run_job(self.app, ["git", "diff", "--cached", "--quiet"])
        if not amend and staged.returncode == 0:
            self.app.screen.show_flash("Nothing staged to commit: stage changes first")
            return True
        if not self.summary.txt.strip():
            self.app.screen.show_flash("Write a summary line first")
            self._selected = self.items.index(self.summary)
            self.dirty = True
            return True
        self.hide()
        log = self.app.git_log
        had_head = bool(log.head_id) and log.head_id in log.commits
        args = ["git", "commit", "--cleanup=strip", "-F", "-"]
        if amend:
            args.append("--amend")
        summary = self.summary.txt.strip()
        result = self.app.run_git(
            args,
            ok=f"{'Amended' if amend else 'Committed'}: {summary}",
            err="Commit failed",
            refresh_head=had_head and not amend,
            reload_refs=True,
            check_uncommitted=True,
            input=message + "\n",
        )
        if result.returncode == 0:
            self.amend.toggled = False
            self._loaded_message = ""
            self.set_message("")
            if amend or not had_head:
                # An amended commit replaces HEAD; a first commit has no log
                # to add to: load the log afresh.
                self.app.reload_refs_commits()
            self.app.git_diff.reload_worktree()
        return True

    # editing ------------------------------------------------------------------

    def _fields(self):
        return [self.summary] + self.body + [self.buttons]

    def _move_line(self, step: int):
        """Up/Down: to the neighbouring line, keeping the cursor column."""
        fields = self._fields()
        item = self.get_selected()
        if item not in fields:
            return
        i = fields.index(item) + step
        if not 0 <= i < len(fields):
            return
        target = fields[i]
        if isinstance(item, UserInputListItem) and isinstance(
            target, UserInputListItem
        ):
            target.cursor_pos = min(item.cursor_pos, len(target.txt))
        self.set_selected(self.items.index(target))

    def _next_field(self, step: int = 1):
        """Tab / Shift+Tab: summary -> description -> buttons."""
        item = self.get_selected()
        if item is self.summary:
            group = 0
        elif item in self.body:
            group = 1
        else:
            group = 2
        group = (group + step) % 3
        target = (self.summary, self.body[0], self.buttons)[group]
        if group == 2:
            self.buttons.reset_focus()
        self.set_selected(self.items.index(target))

    def _split_line(self, item):
        i = self.body.index(item)
        rest = item.txt[item.cursor_pos :]
        item.txt = item.txt[: item.cursor_pos]
        new = MessageLineItem()
        new.set_text(rest)
        new.cursor_pos = 0
        self.body.insert(i + 1, new)
        # Grow only while the last line holds text: a typed line pushes the
        # empty spare lines down instead of adding rows.
        if len(self.body) > self.BODY_LINES and not self.body[-1].txt:
            self.body.pop()
        self._rebuild()
        self.set_selected(self.items.index(new))

    def _join_lines(self, upper, lower):
        """Append `lower` to `upper`; the cursor at the joint."""
        pos = len(upper.txt)
        upper.txt += lower.txt
        upper.cursor_pos = pos
        self.body.remove(lower)
        while len(self.body) < self.BODY_LINES:
            self.body.append(MessageLineItem())
        self._rebuild()
        self.set_selected(self.items.index(upper))

    def handle_key(self, event):
        key = event.key
        item = self.get_selected()
        if key == curses.KEY_EXIT:
            self.hide()
        elif key == curses.KEY_F1:
            self.amend.activate()
        elif key == K.KEY_TAB:
            self._next_field(1)
        elif key == curses.KEY_BTAB:
            self._next_field(-1)
        elif key in (curses.KEY_UP, K.ctrl("p")):
            self._move_line(-1)
        elif key in (curses.KEY_DOWN, K.ctrl("n")):
            self._move_line(1)
        elif key in K.ENTER_KEYS and item is self.summary:
            self.commit()
        elif key in K.ENTER_KEYS and item in self.body:
            self._split_line(item)
        elif (
            key in K.BACKSPACE_KEYS
            and item in self.body
            and item.cursor_pos == 0
            and self.body.index(item) > 0
        ):
            self._join_lines(self.body[self.body.index(item) - 1], item)
        elif (
            key == curses.KEY_DC
            and item in self.body
            and item.cursor_pos == len(item.txt)
            and self.body.index(item) + 1 < len(self.body)
        ):
            self._join_lines(item, self.body[self.body.index(item) + 1])
        else:
            super().handle_key(event)
        # Modal: the keys are text here, never shortcuts.
        return True


def _home_relative(path: str) -> str:
    """`path` with the home directory written as ~."""
    home = os.path.expanduser("~")
    if home not in ("", "/") and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home) :]
    return path


class InitRepoDialogPopup(Dialog):
    """Started outside a git repository: offer to create one here (git init)
    instead of failing every git command. [Init repository] (i / y / Enter)
    runs it and then `on_init`; [Quit] (q / n / Esc) leaves the app."""

    def __init__(self, app):
        super().__init__(app, ID_INIT_REPO, " No git repository")
        self._on_init = lambda: None

    def ask(self, on_init):
        self._on_init = on_init
        self.show_message(
            [
                "This directory is not inside a git repository:",
                (_home_relative(os.getcwd()), Screen.C_GIT_ID),
                "",
                "Create an empty repository here (git init)?",
            ],
            button_row(
                ButtonSegment("[Init repository]", self.init_repo, Screen.C_STATUS),
                TextSegment("   "),
                ButtonSegment("[Quit]", self.app.exit_program),
            ),
        )

    def init_repo(self):
        self.hide()
        result = self.app.run_git(
            ["git", "init"], ok="Initialized an empty git repository", quiet=True
        )
        if result.returncode == 0:
            self.app._toplevel = None
            # The log is titled after the repository, which now exists.
            name = os.path.basename(self.app.git_toplevel())
            self.app.git_log.header_item.set_title(name)
            self._on_init()
        return True

    def handle_key(self, event):
        key = event.key
        if key in (ord("i"), ord("y"), ord("Y")):
            self.init_repo()
        elif key in (curses.KEY_EXIT, ord("q"), ord("n"), ord("N")):
            self.app.exit_program()
        else:
            super().handle_key(event)
        return True  # modal: nothing else makes sense without a repository
