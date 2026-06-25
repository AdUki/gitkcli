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


HORIZONTAL_OFFSET_JUMP = 1

# Neutral grey for the divider between split panes — fixed so the line never
# looks like it belongs to whichever pane happens to be focused.
SPLIT_DIVIDER_COLOR = 18

KEY_SHIFT_F5 = -100
KEY_CTRL_LEFT = -101
KEY_CTRL_RIGHT = -102
KEY_CTRL_BACKSPACE = -103
KEY_CTRL_DEL = -104
KEY_ENTER = 10
KEY_RETURN = 13
KEY_TAB = 9
ENTER_KEYS = (curses.KEY_ENTER, KEY_ENTER, KEY_RETURN)

def KEY_CTRL(key):
    return ord(key) & 0x1F

ID_LOG = 'log'
ID_LOG_SEARCH = 'log-search'
ID_GIT_LOG = 'git-log'
ID_GIT_LOG_SEARCH = 'git-log-search'
ID_NEW_GIT_REF = 'git-log-ref'
ID_GIT_DIFF = 'git-diff'
ID_GIT_DIFF_SEARCH = 'git-diff-search'
ID_GIT_REFS = 'git-refs'
ID_GIT_REFS_SEARCH = 'git-refs-search'
ID_GIT_REF_PUSH = 'git-ref-push'
ID_CONTEXT_MENU = 'context-menu'
ID_CONFIRM_DIALOG = 'confirm-dialog'
ID_ERROR_DIALOG = 'error-dialog'
ID_GIT_REFRESH_HEAD = 'git-refresh-head'
ID_GIT_SEARCH = 'git-search'
ID_PREFERENCES = 'preferences'
ID_GIT_RESET = 'git-reset'

DEFAULT_CONFIG = {
    'git_log': {'show_commit_id': True, 'show_commit_date': True, 'show_commit_author': True, 'flags': ''},
    'git_diff': {'ignore_whitespace': False},
    'log': {'autoscroll': False},
    'view': {'default_mode': 'fullscreen'},  # fullscreen | side | stacked
}

def get_config_path() -> str:
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'gitkcli', 'config.json')

def load_config() -> dict:
    cfg = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    try:
        with open(get_config_path(), 'r') as f:
            data = json.load(f)
        for section, values in data.items():
            if section in cfg and isinstance(values, dict):
                cfg[section].update({k: v for k, v in values.items() if k in cfg[section]})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg

def save_config(cfg: dict) -> bool:
    path = get_config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(cfg, f, indent=2)
        return True
    except OSError as e:
        Gitkcli.log.error(f"Failed to save preferences: {e}")
        return False

def copy_to_clipboard(txt:str):
    try:
        import pyperclip
        pyperclip.copy(txt)
    except ImportError:
        Gitkcli.log.warning("pyperclip module not found. Install with: pip install pyperclip")
    except Exception as e:
        Gitkcli.log.error(f"Error copying to clipboard: {str(e)}")

class Job:

    jobs = {}

    @classmethod
    def add_job(cls, id, job):
        if id in cls.jobs:
            cls.jobs[id].stop_job()
        cls.jobs[id] = job

    @classmethod
    def run_job(cls, args):
        Gitkcli.log.info('Run job: ' + ' '.join(args))
        # Pin LC_ALL=C so git speaks English: callers parse stderr to detect
        # conditions like "already exists" / "non-fast-forward".
        return subprocess.run(args, capture_output=True, text=True,
                              env={**os.environ, 'LC_ALL': 'C'})

    @classmethod
    def process_all_jobs(cls) -> bool:
        update = False
        for job in cls.jobs.values():
            processed = job.process_items()
            if processed or job.running:
                update = True
        return update

    def __init__(self, id:str):
        self.id = id
        self.cmd = ''
        self.args = []
        self.job = None
        self.running = False
        self.stop = False
        self.items = queue.Queue()
        self.messages = queue.Queue()
        self.on_finished = None
        Job.add_job(id, self)

    def process_line(self, line) -> typing.Any:
        return line

    def process_item(self, item):
        # This should be implemented by derived classes
        pass

    def process_message(self, message):
        if message['type'] == 'error':
            Gitkcli.log.error(message['message'])
        elif message['type'] == 'started':
            self.running = True
        elif message['type'] == 'finished':
            self.running = False
            Gitkcli.log.debug(f'Job finished {self.id}')
            if self.on_finished:
                # process remaining items
                self.process_items()
                self.on_finished()
                self.on_finished = None

    def _drain(self, q, handler) -> bool:
        """Drain a queue, dispatching each truthy item to handler (skipped while
        stopped). Returns True if anything was processed."""
        processed = False
        try:
            while True:
                item = q.get_nowait()
                q.task_done()
                if not item:
                    break
                if not self.stop:
                    handler(item)
                    processed = True
        except queue.Empty:
            pass
        return processed

    def process_items(self) -> bool:
        drained_items = self._drain(self.items, self.process_item)
        drained_msgs = self._drain(self.messages, self.process_message)
        return drained_items or drained_msgs

    def stop_job(self):
        self.stop = True
        self.on_finished = None
        if self.job and self.get_exit_code() is None:
            self.job.terminate()
            try:
                self.job.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.job.kill()
                self.running = False
            Gitkcli.log.debug(f'Job stopped {self.id}')

    def start_job(self, args = [], on_finished = None):
        self.stop_job()
        self.stop = False
        self.on_finished = on_finished

        Gitkcli.log.info(' '.join(['Job started', self.id + ':', self.cmd] + args + self.args))

        self.job = subprocess.Popen(
                self.cmd.split(' ') + args + self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        
        stdout_thread = threading.Thread(target=self._reader_thread, args=(self.job.stdout, False))
        stderr_thread = threading.Thread(target=self._reader_thread, args=(self.job.stderr, True))
        stdout_thread.start()
        stderr_thread.start()

    def get_exit_code(self):
        return self.job.poll() if self.job else None

    def _reader_thread(self, stream, is_stderr=False):
        if not is_stderr:
            self.messages.put({'type': 'started'})
        for bytearr in iter(stream.readline, b''):
            if self.stop:
                break
            try:
                # curses automatically converts tab to spaces, so we will replace it here and cut off newline
                tabsize = curses.get_tabsize() if hasattr(curses, 'get_tabsize') else 8
                line = bytearr.decode('utf-8', errors='replace').replace('\t', ' ' * tabsize).rstrip('\r\n')
                if is_stderr:
                    self.messages.put({'type': 'error', 'message': line})
                else:
                    item = self.process_line(line)
                    if item:
                        self.items.put(item)

            except Exception as e:
                self.messages.put({'type': 'error', 'message': f"Error processing line: {bytearr}\n{str(e)}"})
        stream.close()
        if not is_stderr and not self.stop:
            self.messages.put({'type': 'finished'})

class GitLogJob(Job):
    def __init__(self, id:str, args = []):
        super().__init__(id) 
        self.cmd = 'git log --format=#%H#%P#%aI#%an#%s'
        self.args = args

    def start_job(self, args = [], on_finished = None):
        Gitkcli.git_log.commits.clear()
        super().start_job(args, on_finished) 

    def process_line(self, line) -> typing.Any:
        try:
            prefix, id, parents_str, date_str, author, title = line.split('#', 5)
            return (id, {
                'prefix': prefix,
                'parents': parents_str.split(' '),
                'date': datetime.datetime.fromisoformat(date_str),
                'author': author,
                'title': title,
            })
        except ValueError:
            return str(line)

    def process_item(self, item):
        if isinstance(item, str):
            Gitkcli.git_log.append(TextListItem(item, selectable = False))
        else:
            id, commit = item
            if Gitkcli.git_log.add_commit(id, commit):
                Gitkcli.git_log.append(CommitListItem(id))

class GitRefreshHeadJob(GitLogJob):
    def __init__(self):
        super().__init__(ID_GIT_REFRESH_HEAD, []) 

    def start_job(self, args = [], on_finished = None):
        # check if HEAD commit is actually in view
        head_found = False
        for item in Gitkcli.git_log.items:
            if hasattr(item, 'id') and item.id == Gitkcli.git_log.head_id:
                head_found = True
                break
        if not head_found:
            # no HEAD commit found, don't do anything
            return

        # skip calling Gitkcli.git_log.commits.clear()
        Job.start_job(self, args, on_finished) 

    def process_item(self, item):
        (id, commit) = item
        if Gitkcli.git_log.add_commit(id, commit):
            Gitkcli.git_log.prepend_commit(CommitListItem(id))

class GitDiffJob(Job):
    def __init__(self):
        super().__init__(ID_GIT_DIFF) 
        self.cmd = 'git'

        self.line_pattern = re.compile(r'^(?:( )|(?:\+\+\+ b/(.*))|(?:--- a/(.*))|(\+\+\+|---|diff|index)|(\+)|(-)|(@@ -(\d+),\d+ \+(\d+),\d+ @@))')
        self.stat_pattern = re.compile(r' (?:\.\.\.)?(?:.* => )?(.*?)}? +\| +\d+ \+*-*')

        self.commit_id: typing.Optional[str] = None
        self.tag_id: typing.Optional[str] = None
        self.cached: bool = False
        self.old_commit_id: typing.Optional[str] = None
        self.new_commit_id: typing.Optional[str] = None
        self.old_file_path: typing.Optional[str] = None
        self.old_file_line:int = -1
        self.new_file_path: typing.Optional[str] = None
        self.new_file_line:int = -1
        self.line_count = -1
        self.selected_line_map = {}

    def _get_args(self):
        self.old_file_path = None
        self.old_file_line = -1
        self.new_file_path = None
        self.new_file_line = -1
        self.line_count = -1

        if self.tag_id:
            return ['cat-file', '-p', self.tag_id]

        if self.commit_id:
            args = ['show', '-m', self.commit_id]
        else:
            args = ['diff', self.old_commit_id]
            if self.new_commit_id:
                args.append(self.new_commit_id)

        if self.cached:
            args.insert(1, '--cached')

        args.extend([f'-U{Gitkcli.git_diff.context_size}', f'--stat={Gitkcli.git_diff.width}', '--no-color', f'-l{Gitkcli.git_diff.rename_limit}'])

        if Gitkcli.git_diff.ignore_whitespace:
            args.append('-w')

        return args

    def restart_job(self):
        self.start_job(self._get_args())

    def get_old_revision(self):
        """Git revision for the 'old' (---) side of the current diff"""
        if self.commit_id:
            return f'{self.commit_id}^'
        return self.old_commit_id

    def _restore_on_finished(self, key):
        """Callback that restores the saved cursor/scroll for `key`, or None."""
        entry = self.selected_line_map.get(key)
        return (lambda: Gitkcli.git_diff.restore_view_position(*entry)) if entry else None

    def _prepare(self, title, *, is_diff, view_commit_id, commit_id=None, tag_id=None,
                 old_commit_id=None, new_commit_id=None, cached=False):
        """Reset the job target and the diff view before starting a show_* job."""
        self.commit_id = commit_id
        self.tag_id = tag_id
        self.cached = cached
        self.old_commit_id = old_commit_id
        self.new_commit_id = new_commit_id
        Gitkcli.git_diff.clear()
        Gitkcli.git_diff.commit_id = view_commit_id
        Gitkcli.git_diff.is_diff = is_diff
        Gitkcli.git_diff.header_item.set_title(title)

    def show_diff(self, old_commit_id, new_commit_id = None, cached = False, title = None,
                  view_id = None, add_to_jump_list = False):
        if not title:
            title = f'Diff {old_commit_id[:7]} {new_commit_id[:7]}'
        self._prepare(title, is_diff=True, view_commit_id=view_id or old_commit_id,
                      old_commit_id=old_commit_id, new_commit_id=new_commit_id, cached=cached)
        self.start_job(self._get_args(), on_finished=self._restore_on_finished(view_id))
        if add_to_jump_list and view_id:
            Gitkcli.git_log.add_to_jump_list(view_id)

    def show_commit(self, commit_id, on_finished = None, add_to_jump_list = True):
        self._prepare(f'Commit {commit_id[:7]}', is_diff=False, view_commit_id=commit_id,
                      commit_id=commit_id)
        if on_finished is None:
            on_finished = self._restore_on_finished(commit_id)
        self.start_job(self._get_args(), on_finished=on_finished)
        if add_to_jump_list:
            Gitkcli.git_log.add_to_jump_list(commit_id)

    def show_tag_annotation(self, tag_id):
        self._prepare(f'Tag {tag_id}', is_diff=True, view_commit_id=tag_id, tag_id=tag_id)
        self.start_job(self._get_args())
        Gitkcli.git_diff.show()

    def process_line(self, line) -> typing.Any:
        color = 1
        self.line_count += 1

        # 9 capture groups
        match = self.line_pattern.search(line)
        if match:
            if match.group(1): # code lines, stats and commit message
                if self.old_file_line < 0 and self.new_file_line < 0: # commit message or stats line
                    if line.startswith(' ') and not line.startswith('    '): # stats line
                        color = 10
                    stat_match = self.stat_pattern.match(line)
                    if stat_match: # stats line
                        return StatListItem(line, color, stat_match.group(1))
                    return TextListItem(line, color)
                self.old_file_line += 1
                self.new_file_line += 1
                return DiffListItem(self.line_count, line, color, self.old_file_path, self.old_file_line, self.new_file_path, self.new_file_line)
            elif match.group(2): # '+++' new file
                color = 17
                self.new_file_path = str(match.group(2))
                return TextListItem(line, color)
            elif match.group(3): # '---' old file
                color = 17
                self.old_file_path = str(match.group(3))
                return TextListItem(line, color)
            elif match.group(4): # infos
                color = 17
                return TextListItem(line, color)
            elif match.group(5): # '+' added code lines
                color = 9
                self.new_file_line += 1
                return DiffListItem(self.line_count, line, color, None, None, self.new_file_path, self.new_file_line)
            elif match.group(6): # '-' remove code lines
                color = 8
                self.old_file_line += 1
                return DiffListItem(self.line_count, line, color, self.old_file_path, self.old_file_line, None, None)
            elif match.group(7): # diff numbers
                color = 10
                self.old_file_line = int(match.group(8)) - 1
                self.new_file_line = int(match.group(9)) - 1
                return DiffListItem(self.line_count, line, color, self.old_file_path, self.old_file_line, self.new_file_path, self.new_file_line)

        return TextListItem(line, color)

    def process_item(self, item):
        Gitkcli.git_diff.append(item)

class GitSearchJob(Job):
    def __init__(self, args = []):
        super().__init__(ID_GIT_SEARCH)
        self.cmd = 'git log --format=%H'
        # CLI revision args (e.g. a branch name). These must precede any
        # '--' pathspec separator added by the search, so keep them out of
        # self.args (which the base class appends *after* the per-search args)
        # and instead prepend them to args in start_job.
        self.revisions = args
        self.found_ids = set()

    def start_job(self, args = [], on_finished = None):
        self.found_ids.clear()
        Gitkcli.git_log.dirty = True
        super().start_job(self.revisions + args, on_finished)

    def process_item(self, item):
        self.found_ids.add(item)
        Gitkcli.git_log.dirty = True

class GitRefsJob(Job):
    def __init__(self):
        super().__init__(ID_GIT_REFS) 
        self.cmd = 'git show-ref --head --dereference'

    def start_job(self, args = [], on_finished = None):
        Gitkcli.git_refs.refs.clear()

        Gitkcli.git_log.head_branch = Job.run_job(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.rstrip()
        if Gitkcli.git_log.head_branch == 'HEAD': Gitkcli.git_log.head_branch = ''

        super().start_job(args, on_finished) 

    def process_line(self, line) -> typing.Any:
        id, value = tuple(line.split(' '))
        if value == 'HEAD':
            return {'id': id, 'name': value, 'type': 'head'}
        parts = value.split('/', 2)
        return {'id': id, 'type': parts[1], 'name': parts[1] if len(parts) == 2 else parts[2]}

    def process_item(self, item):
        id = item['id']

        if item['type'] == 'tags' and item['name'].endswith('^{}'): 
            # process link to annotated tag
            last_item_data = Gitkcli.git_refs.items[-1].data
            last_item_data['tag_id'] = last_item_data['id']
            last_item_data['id'] = id
            item = last_item_data
        else:
            Gitkcli.git_refs.append(RefListItem(item))

        Gitkcli.git_refs.refs.setdefault(id,[]).append(item)
        Gitkcli.git_log.dirty = True
        if item['type'] == 'head':
            Gitkcli.git_log.head_id = id

class Item:
    def __init__(self):
        self.is_selectable = True
        self.is_separator = False

    def get_text(self) -> str:
        return ''

    def copy_text_to_clipboard(self):
        copy_to_clipboard(self.get_text())

    def set_text(self, txt:str):
        pass

    def draw_line(self, win, offset, width, selected, matched, marked):
        pass

    def activate(self) -> bool:
        """Default action on Enter / double-click. Override in subclasses."""
        return False

    def handle_input(self, keyboard) -> bool:
        if keyboard.key in ENTER_KEYS:
            return self.activate()
        return False

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'double-click':
            return self.activate()
        if mouse.event_type == 'right-click':
            return Gitkcli.context_menu.show_context_menu(self)
        return False

class SeparatorItem(Item):
    def __init__(self):
        super().__init__()
        self.is_selectable = False
        self.is_separator = True

class RefListItem(Item):
    def __init__(self, data):
        super().__init__()
        self.data = data

    def get_text(self):
        return self.data['name']

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.get_text()[offset:]
        color, _ = GitRefsView.get_ref_color_and_title(self.data)
        if selected or marked:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]

        win.addstr(line, Screen.color(color, selected, marked, matched))
        win.clrtoeol()

    def activate(self) -> bool:
        if Gitkcli.git_log.select_commit(self.data['id']):
            Gitkcli.git_log.show()
        else:
            Gitkcli.log.warning(f"Commit with hash {self.data['id']} not found")
        return True

class TextListItem(Item):
    def __init__(self, txt, color = 1, expand = False, selectable = True, dim = False):
        super().__init__()
        self.txt = txt
        self.color = color
        self.expand = expand
        self.is_selectable = selectable
        self.dim = dim

    def get_text(self):
        return self.txt

    def set_text(self, txt:str):
        self.txt = txt

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.get_text()[offset:]
        clear = True
        if selected or marked or self.expand:
            line += ' ' * (width - len(line))
            clear = False
        if len(line) >= width:
            line = line[:width]
            clear = False

        win.addstr(line, Screen.color(self.color, selected, marked, matched, dim = self.dim))
        if clear:
            win.clrtoeol()

class SpacerListItem(Item):
    def __init__(self):
        super().__init__()
        self.is_selectable = False

    def draw_line(self, win, offset, width, selected, matched, marked):
        win.clrtoeol()

class StatListItem(TextListItem):
    def __init__(self, txt:str, color:int, stat_file_path:str):
        self.stat_file_path = stat_file_path
        super().__init__(txt, color)

    def jump_to_file(self):
        diff = Gitkcli.git_diff
        Gitkcli.git_log.add_to_jump_list(diff.commit_id, diff._selected, diff._offset_y)
        diff.set_selected(re.compile(f'diff.*{self.stat_file_path}'), 'top')
        Gitkcli.git_log.add_to_jump_list(diff.commit_id, diff._selected, diff._offset_y)

    def activate(self) -> bool:
        self.jump_to_file()
        return True

class DiffListItem(TextListItem):
    def __init__(self, line:int, txt:str, color:int,
                 old_file_path:typing.Optional[str] = None, old_file_line:typing.Optional[int] = None,
                 new_file_path:typing.Optional[str] = None, new_file_line:typing.Optional[int] = None):
        self.line = line
        self.old_file_line = old_file_line
        self.old_file_path = old_file_path
        self.new_file_line = new_file_line
        self.new_file_path = new_file_path
        super().__init__(txt, color)

    def jump_to_origin(self):
        blame_revision = Gitkcli.git_diff.job.get_old_revision()
        if self.old_file_path and self.old_file_line and blame_revision:
            args = ['git', 'blame', '-lsfn', '-L',
                    f'{self.old_file_line},{self.old_file_line}',
                    blame_revision,
                    '--', self.old_file_path]

            result = Job.run_job(args)
            if result.returncode == 0:
                # Example output:
                # a42cadebfe42d85cbf36f4887be166b34077b3e2 test test.txt 1 1) aaa
                match = re.search(r'^(\S+) ([^)]+) ([0-9]+) ', result.stdout)
                if match:
                    id = str(match.group(1))
                    file_path = str(match.group(2))
                    file_line = int(match.group(3))

                    # When commit id starts with '^' it means this is initial git-id and is 1 char shorer
                    # ^1af87e6c2614c1aea4a81476df0deb8206d5489 451)         except Exception:
                    if id.startswith('^'):
                        id = Job.run_job(['git', 'rev-parse', id]).stdout.lstrip('^').rstrip()
                    commit = Gitkcli.git_log.select_commit(id)
                    if commit:
                        diff = Gitkcli.git_diff
                        Gitkcli.git_log.add_to_jump_list(diff.commit_id, diff._selected, diff._offset_y)

                        def on_finished():
                            diff.select_line(file_path, file_line)
                            Gitkcli.git_log.add_to_jump_list(commit.id, diff._selected, diff._offset_y)

                        diff.job.show_commit(commit.id, on_finished=on_finished, add_to_jump_list=False)

    def activate(self) -> bool:
        self.jump_to_origin()
        return True

class Segment:
    def get_text(self) -> str:
        return ''

    def set_text(self, txt:str):
        pass

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return 0

    def _draw_text(self, win, offset, width, color) -> int:
        """Draw the segment's text clipped to [offset, width) in `color` and
        return how many cells it consumed. Shared by the simple draw() variants."""
        visible_txt = self.get_text()[offset:width]
        win.addstr(visible_txt, color)
        return len(visible_txt)

    def handle_mouse_input(self, mouse) -> bool:
        return False

class FillerSegment(Segment):
    pass

class TextSegment(Segment):
    def __init__(self, txt, color = 1):
        super().__init__()
        self.txt = txt
        self.color = color

    def get_text(self):
        return self.txt

    def set_text(self, txt:str):
        self.txt = txt

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return self._draw_text(win, offset, width, Screen.color(self.color, selected, marked, matched))

class RefSegment(TextSegment):
    def __init__(self, ref):
        self.ref = ref
        color, txt = GitRefsView.get_ref_color_and_title(ref)
        super().__init__(txt, color)

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'right-click':
            return Gitkcli.context_menu.show_context_menu(RefListItem(self.ref), 'git-refs')
        elif mouse.event_type == 'double-click' and 'tag_id' in self.ref:
            Gitkcli.git_diff.job.show_tag_annotation(self.ref['tag_id'])
            return True
        else:
            return super().handle_mouse_input(mouse)

class ButtonSegment(TextSegment):
    def __init__(self, txt, callback, color = 1):
        super().__init__(txt, color)
        self.callback = callback
        self.is_pressed = False

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'left-click' or mouse.event_type == 'double-click' or mouse.event_type == 'left-move-in':
            self.is_pressed = True
            return True

        if mouse.event_type == 'left-move-out':
            self.is_pressed = False
            return True

        if mouse.event_type == 'left-release':
            self.is_pressed = False
            return self.callback()
        else:
            return super().handle_mouse_input(mouse)

    def activate(self) -> bool:
        return self.callback()

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        if self.is_pressed:
            # Pressed: highlight if not already selected, else go bold+dim.
            bold = dim = selected
            return self._draw_text(win, offset, width,
                                   Screen.color(self.color, True, marked, bold = bold, dim = dim))
        return super().draw(win, offset, width, selected, matched, marked)

class ToggleSegment(TextSegment):
    def __init__(self, txt, toggled = False, callback = lambda val: None, color = 1):
        super().__init__(txt, color)
        self.callback = callback
        self.toggled = toggled
        self.enabled = True

    def toggle(self):
        self.toggled = not self.toggled

    def activate(self) -> bool:
        self.toggle()
        self.callback(self)
        return True

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'left-click' or mouse.event_type == 'double-click':
            self.toggle()
            self.callback(self)
            return True
        else:
            return super().handle_mouse_input(mouse)

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return self._draw_text(win, offset, width,
                               Screen.color(self.color, selected, self.toggled, dim = not self.enabled))

class SegmentedListItem(Item):
    def __init__(self, segments = [], bg_color = 1):
        super().__init__()
        self.segment_separator = ' '
        # Character used for the FillerSegment and the trailing fill. Defaults to
        # a space (an ordinary row); the rule-line title bar overrides it to '─'.
        self.fill_char = ' '
        self.segments = segments
        self.bg_color = bg_color
        self.clicked_segment = None

    def get_segments(self):
        return self.segments

    def get_text(self):
        return self.segment_separator.join(s.get_text() for s in self.get_segments())

    def get_segment_on_offset(self, offset) -> Segment:
        segment_pos = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                length = self.fill_width
            else:
                length = len(segment.get_text())
            if segment_pos <= offset < segment_pos + length:
                return segment
            segment_pos += length + len(self.segment_separator)
        return Segment()

    def handle_mouse_input(self, mouse) -> bool:
        segment = self.clicked_segment or self.get_segment_on_offset(mouse.x)
        if 'left-click' == mouse.event_type or 'double-click' == mouse.event_type:
            self.clicked_segment = segment
        elif self.clicked_segment:
            if 'release' in mouse.event_type:
                self.clicked_segment = None
            if 'move-in' in mouse.event_type and self.clicked_segment != self.get_segment_on_offset(mouse.x):
                mouse.event_type = mouse.event_type.replace('in', 'out')
        if segment and segment.handle_mouse_input(mouse):
            return True
        return super().handle_mouse_input(mouse)

    def get_fill_txt(self, width):
        fillers_count = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                fillers_count += 1
        if fillers_count:
            # Clamp to 0: a negative width (content wider than the window) would
            # otherwise rewind segment_pos in get_segment_on_offset and misroute
            # clicks on the buttons that follow the filler.
            self.fill_width = max(0, int((width - len(self.get_text())) / fillers_count))
            return self.fill_width * self.fill_char
        return ''

    def _segment_selected(self, index, selected):
        # Per-segment highlight flag. Base: whole row follows the row selection.
        return selected

    def _bg_selected(self, selected):
        # Highlight flag for separators/fillers/trailing fill (the row background).
        return selected

    def draw_line(self, win, offset, width, selected, matched, marked):
        draw_separator = False
        remaining_width = width
        bg_selected = self._bg_selected(selected)
        for index, segment in enumerate(self.get_segments()):
            if draw_separator and self.segment_separator:
                draw_separator = False
                remaining_width -= len(self.segment_separator)
                win.addstr(self.segment_separator, Screen.color(self.bg_color, bg_selected, marked, matched))
            if isinstance(segment, FillerSegment):
                txt = self.get_fill_txt(width)
                win.addstr(txt, Screen.color(self.bg_color, bg_selected, marked, matched))
                length = len(txt)
            else:
                length = segment.draw(win, offset, remaining_width, self._segment_selected(index, selected), matched, marked)
                txt = segment.get_text()
            draw_separator = length > 0
            remaining_width -= length
            if remaining_width <= 0:
                break
            offset -= len(txt) - length

        if remaining_width > 0:
            if bg_selected or marked:
                win.addstr(' ' * remaining_width, Screen.color(self.bg_color, bg_selected, marked, matched))
            elif self.fill_char != ' ':
                # rule-line bar: trail the title with its fill character ('─')
                win.addstr(self.fill_char * remaining_width, Screen.color(self.bg_color, bg_selected, marked, matched))
            else:
                win.clrtoeol()

class ButtonRowItem(SegmentedListItem):
    """A row of buttons navigable with Left/Right (or h/l); Enter activates the
    focused button. Only the focused button is highlighted, not the whole row."""
    def __init__(self, segments = [], bg_color = 1):
        super().__init__(segments, bg_color)
        self.is_selectable = True
        indices = self._button_indices()
        self.focused = indices[0] if indices else 0

    def _button_indices(self):
        return [i for i, s in enumerate(self.segments) if hasattr(s, 'activate')]

    def reset_focus(self):
        # Back to the default (first/primary) button. Reused dialogs call this on
        # open so focus doesn't linger on whatever was picked last time.
        indices = self._button_indices()
        self.focused = indices[0] if indices else 0

    def focus_last(self):
        # Focus the last (rightmost) button. Destructive confirm dialogs default
        # here so a stray Enter hits the safe (cancel) button.
        indices = self._button_indices()
        self.focused = indices[-1] if indices else 0

    def _move_focus(self, direction):
        indices = self._button_indices()
        if not indices:
            return
        pos = indices.index(self.focused) if self.focused in indices else 0
        self.focused = indices[(pos + direction) % len(indices)]

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_LEFT or key == ord('h'):
            self._move_focus(-1)
            return True
        if key == curses.KEY_RIGHT or key == ord('l'):
            self._move_focus(1)
            return True
        if key in ENTER_KEYS:
            if 0 <= self.focused < len(self.segments) and hasattr(self.segments[self.focused], 'activate'):
                self.segments[self.focused].activate()
            return True
        return False

    def _segment_selected(self, index, selected):
        return selected and index == self.focused

    def _bg_selected(self, selected):
        # Never band the whole row; only the focused button is highlighted.
        return False

def button_row(*buttons):
    """A centered, keyboard-navigable row of buttons (fillers pad both ends)."""
    return ButtonRowItem([FillerSegment(), *buttons, FillerSegment()])

class SplitButtonSegment(ButtonSegment):
    """Header button that shows the current split-view state and cycles it."""
    _LABELS = {'off': '[Split]', 'side': '[Split |]', 'stacked': '[Split =]'}

    def __init__(self, color = 30):
        super().__init__('', Gitkcli.cycle_split_view, color)

    def get_text(self):
        return self._LABELS.get(Gitkcli.split_mode, '[Split]')

class WindowTopBarItem(SegmentedListItem):
    """Top title bar of a main view, rendered as a horizontal rule line with the
    title and buttons inset: ``─ Title ─────── [buttons] [X]``. Fullscreen and
    split-pane views show it with no surrounding box; a floated window draws it
    between its box's top corners (``┌─ Title …[X]─┐``). Focus is shown by
    colouring the active view's line (blue line + white text) versus a dim grey
    line when inactive."""

    # Line and text colours, by active state. The ── fill uses the line colour;
    # the title and buttons use the text colour. Selected/highlight backgrounds
    # are intentionally bypassed (see draw_line) so the bar stays a thin line.
    LINE_ACTIVE, LINE_INACTIVE = 5, 18
    TEXT_ACTIVE, TEXT_INACTIVE = 1, 18
    CLOSE_COLOR = 2  # red [X] close button when active

    def __init__(self, title:str, additional_segments = [], title_color = None):
        # title_color overrides the title's text colour when active. The bar
        # shows a live "[current/total]" line counter after the title, updated
        # generically by View.draw_header from the owning view's state.
        self._base_title = title
        self.title_segment = TextSegment(title, self.TEXT_ACTIVE)
        self._title_color = title_color
        self._leading = TextSegment('─', self.LINE_ACTIVE)
        self._close_segment = ButtonSegment("[X]", lambda: Gitkcli.screen.hide_active_view(), self.TEXT_ACTIVE)
        segments = [self._leading,
                    self.title_segment,
                    FillerSegment()]
        segments.extend(additional_segments)
        segments.append(self._close_segment)
        super().__init__(segments, self.LINE_INACTIVE)
        self.fill_char = '─'

    def set_title(self, txt:str):
        self._base_title = txt
        self.title_segment.set_text(txt)

    def set_counter(self, current:int, total:int):
        self.title_segment.set_text(f'{self._base_title} [{current}/{total}]')

    def get_fill_txt(self, width):
        # Reserve one trailing column so the rule always ends "…[X]─": a dash
        # sits between the last button and the right edge / box corner / split
        # divider, instead of the button being flush against it.
        return super().get_fill_txt(width - 1)

    def draw_line(self, win, offset, width, selected, matched, marked):
        # `selected` is the view's active state (passed by View.draw). Recolour
        # every segment for that state, then draw with selected=False so the
        # row never gets a highlight band — it must read as a single line.
        active = selected
        line_color = self.LINE_ACTIVE if active else self.LINE_INACTIVE
        text_color = self.TEXT_ACTIVE if active else self.TEXT_INACTIVE
        self.bg_color = line_color
        for seg in self.segments:
            if seg is self._leading:
                seg.color = line_color
            elif active and seg is self.title_segment and self._title_color is not None:
                seg.color = self._title_color
            elif active and seg is self._close_segment:
                seg.color = self.CLOSE_COLOR
            else:
                seg.color = text_color
        super().draw_line(win, offset, width, False, matched, marked)

    def handle_mouse_input(self, mouse) -> bool:
        if super().handle_mouse_input(mouse):
            return True
        if 'double-click' == mouse.event_type:
            Gitkcli.screen.get_active_view().toggle_window_mode()
            return True
        return False

class UncommittedChangesListItem(TextListItem):
    def __init__(self, staged:bool = False):
        self._staged = staged
        self.id = 'local-staged' if staged else 'local-working'
        if self._staged:
            super().__init__('Uncommitted changes (staged)', 3)
        else:
            super().__init__('Uncommitted changes (working directory)', 2)

    def load_to_view(self):
        if Gitkcli.git_diff.commit_id == self.id:
            return
        Gitkcli.git_diff.job.show_diff('HEAD', cached = self._staged, title = self.txt,
                                       view_id = self.id, add_to_jump_list = True)

    def activate(self) -> bool:
        self.load_to_view()
        Gitkcli.git_diff.show()
        return True

class CommitListItem(SegmentedListItem):
    def __init__(self, id:str):
        super().__init__()
        self.id = id

    def get_segments(self):
        commit = Gitkcli.git_log.commits[self.id]
        segments = []

        if commit['prefix']:
            segments.append(TextSegment(commit['prefix']))
        if Gitkcli.git_log.show_commit_id:
            segments.append(TextSegment(self.id[:7], 4))
        if Gitkcli.git_log.show_commit_date:
            segments.append(TextSegment(commit['date'].strftime("%Y-%m-%d %H:%M"), 5))
        if Gitkcli.git_log.show_commit_author:
            segments.append(TextSegment(commit['author'], 6))
        segments.append(TextSegment(commit['title']))

        head_position = len(segments) + 1 # +1, because we want to skip 'HEAD ->' segment
        for ref in Gitkcli.git_refs.refs.get(self.id, []):
            segments.insert(head_position if ref['name'] == Gitkcli.git_log.head_branch else len(segments), RefSegment(ref))

        return segments

    def draw_line(self, win, offset, width, selected, matched, marked):
        super().draw_line(win, offset, width, selected, matched, Gitkcli.git_log.marked_commit_id == self.id)

    def load_to_view(self):
        if Gitkcli.git_diff.commit_id != self.id or Gitkcli.git_diff.is_diff:
            Gitkcli.git_diff.job.show_commit(self.id)

    def activate(self) -> bool:
        self.load_to_view()
        Gitkcli.git_diff.show()
        return True

class View:

    def __init__(self, id:str,
                 view_mode:str = 'fullscreen',
                 x:typing.Optional[int] = None, y:typing.Optional[int] = None,
                 height:typing.Optional[int] = None, width:typing.Optional[int] = None):

        self.id:str = id
        self.view_mode:str = view_mode
        self.header_item:typing.Any = None
        self.is_popup:bool = False

        # coordinates and sizes when view is 'window'
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width

        self.dirty:bool = True
        # When only the header line changed (e.g. a live counter in the title),
        # redraw just row 0 instead of the whole body. A full redraw subsumes it.
        self.header_dirty:bool = False
        self.resize_mode:str = ''
        
        height, width, y, x = self._calculate_dimensions()
        self.win = curses.newwin(height, width, y, x)
        # Each view is a panel in the screen's z-ordered deck. The panel library
        # composites overlapping windows (occlusion, vacated-region cleanup) for
        # us; we only mark content dirty and let update_panels() do the rest.
        # Hidden until show() raises it.
        self.panel = curses.panel.new_panel(self.win)
        self.panel.hide()

        Gitkcli.screen.add_view(id, self)
        
    def split_border_sides(self):
        """Border lines this view draws while it is a tiled split pane, as a
        subset of {'left', 'right', 'bottom'} (the top row is always the title).
        Returns None when the view is not a split pane, meaning use a full box.

        Side-by-side: only the log (left) pane draws a right divider; the diff
        (right) pane is borderless. Stacked: both panes are borderless and the
        bottom pane's title bar doubles as the draggable divider.
        """
        if not (Gitkcli.split_active() and self in (Gitkcli.git_log, Gitkcli.git_diff)):
            return None
        if Gitkcli.split_mode == 'side' and self is Gitkcli.git_log:
            return {'right'}
        return set()

    def _calculate_dimensions(self, lines = None, cols = None):
        if lines is None or cols is None:
            lines, cols = Gitkcli.screen.getmaxyx()

        # fullscreen dimensions
        win_height = lines
        win_width = cols
        win_y = 0
        win_x = 0

        if self.view_mode == 'window':
            win_height = min(lines, self.fixed_height if self.fixed_height else int(lines / 2))
            win_width = min(cols, self.fixed_width if self.fixed_width else int(cols / 2))
            win_y = min(lines - win_height, int((lines - win_height) / 2) if self.fixed_y is None else self.fixed_y)
            win_x = min(cols - win_width, int((cols - win_width) / 2) if self.fixed_x is None else self.fixed_x)

        self.y = 0
        self.x = 0
        self.width = win_width
        self.height = win_height

        sides = self.split_border_sides()
        if sides is not None:
            # split pane: title row on top, plus only the requested thin lines
            self.height -= 1
            self.y += 1
            if 'bottom' in sides:
                self.height -= 1
            if 'left' in sides:
                self.x += 1
                self.width -= 1
            if 'right' in sides:
                self.width -= 1
            return win_height, win_width, win_y, win_x

        # Window-mode views (floating popups and floated main views) draw a full
        # box. Fullscreen main views are borderless apart from their title line.
        box = self.view_mode == 'window'

        if self.header_item or box:
            # substract header line or box top
            self.height -= 1
            self.y += 1

        if box:
            # substract box bottom
            self.height -= 1

        if box:
            # substract box sides
            self.x += 1
            self.width -= 2

        return win_height, win_width, win_y, win_x

    def _set_geometry(self, height, width, y, x):
        """Resize+reposition the window and mark it dirty. A panel resized in
        place does NOT re-expose what it shrinks away from (curses only uncovers
        the new, smaller footprint), so if it is shown we hide it first - which
        uncovers its full OLD footprint - then move to a valid origin, resize,
        move to the target, and restore it: to the top if it was the active view,
        otherwise back into stack order. The actual repaint is a single
        update_panels()/doupdate() per frame, so the hide/show is invisible."""
        was_top = self.is_active()
        shown = not self.panel.hidden()
        if shown:
            self.panel.hide()
        self.panel.move(0, 0)  # an origin valid for any size, before resizing
        self.win.resize(height, width)
        self.panel.move(y, x)
        if shown:
            self.panel.show()
            if was_top:
                self.panel.top()
            else:
                Gitkcli.screen._restack()
        self.dirty = True

    def set_header_item(self, item):
        self.header_item = item
        self._calculate_dimensions()

    def set_view_mode(self, view_mode:str):
        if self.view_mode == view_mode:
            return
        stdscr_height, stdscr_width = Gitkcli.screen.getmaxyx()
        self.view_mode = view_mode
        height, width, y, x = self._calculate_dimensions(stdscr_height, stdscr_width)
        self._set_geometry(height, width, y, x)

    def set_tiled(self, x, y, height, width):
        """Place this view as a non-overlapping pane (used by split view)."""
        self.view_mode = 'window'
        self.set_dimensions(x, y, height, width)

    def set_fullscreen(self):
        self.set_view_mode('fullscreen')

    def toggle_window_mode(self):
        # In split view the log/diff panes are managed by the split layout;
        # toggling a pane "maximizes" it by leaving split view altogether.
        if Gitkcli.split_active() and self in (Gitkcli.git_log, Gitkcli.git_diff):
            Gitkcli.set_split_mode('off')
            return
        self.set_view_mode('fullscreen' if self.view_mode == 'window' else 'window')

    def set_dimensions(self, x, y, height, width):
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width
        height, width, y, x = self._calculate_dimensions()
        self._set_geometry(height, width, y, x)

    def _start_split_resize(self, x:int, y:int) -> bool:
        """Arm a drag of the split divider when the grab is on the shared edge."""
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()
        is_log = self is Gitkcli.git_log
        if Gitkcli.split_mode == 'side':
            # divider is the right edge of the log pane / left edge of the diff pane
            on_divider = (x >= win_x + win_width - 1) if is_log else (x <= win_x)
        else:  # stacked: there is no line, so the bottom pane's title bar is the grip
            on_divider = (not is_log) and (y <= win_y)
        if on_divider:
            self.resize_mode = 'split'
            return True
        return False

    def start_resize(self, x:int, y:int) -> bool:
        self.resize_mode = ''
        # Split panes are fixed in place; only the shared divider can be dragged.
        if Gitkcli.split_active() and self in (Gitkcli.git_log, Gitkcli.git_diff):
            return self._start_split_resize(x, y)
        if self.view_mode != 'window':
            return False
        win_y, win_x = self.win.getbegyx()
        if y <= win_y:
            self.resize_mode = 'm'
            return True
        if self.is_popup:
            return False
        win_height, win_width = self.win.getmaxyx()
        if x >= win_x + win_width - 1:
            self.resize_mode += 'e'
        if x <= win_x:
            self.resize_mode += 'w'
        if y >= win_y + win_height - 1:
            self.resize_mode += 's'
        return bool(self.resize_mode)

    def stop_resize(self) -> bool:
        if self.resize_mode:
            self.resize_mode = ''
            return True
        return False

    def handle_resize(self):
        if self.resize_mode == 'split':
            lines, cols = Gitkcli.screen.getmaxyx()
            if Gitkcli.split_mode == 'side':
                ratio = Gitkcli.mouse.screen_x / max(1, cols)
            else:
                ratio = Gitkcli.mouse.screen_y / max(1, lines)
            Gitkcli.split_ratio = min(0.85, max(0.15, ratio))
            Gitkcli.apply_split_layout()
            return
        stdscr_height, stdscr_width = Gitkcli.screen.getmaxyx()
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()

        if 'm' in self.resize_mode:
            new_x = max(0, min(win_x + Gitkcli.mouse.rel_x, stdscr_width - win_width))
            new_y = max(0, min(win_y + Gitkcli.mouse.rel_y, stdscr_height - win_height))
            if new_x == win_x and new_y == win_y:
                return
            self.panel.move(new_y, new_x)
            self.dirty = True
        else:
            new_x = win_x
            new_y = win_y
            new_width = win_width
            new_height = win_height
            if 'w' in self.resize_mode:
                new_x = max(0, win_x + Gitkcli.mouse.rel_x)
                new_width = win_width - (new_x - win_x)
            if 'e' in self.resize_mode:
                new_width = max(5, min(stdscr_width - new_x, win_width + Gitkcli.mouse.rel_x))
            if 's' in self.resize_mode:
                new_height = max(5, min(stdscr_height - new_y, win_height + Gitkcli.mouse.rel_y))
            self.set_dimensions(new_x, new_y, new_height, new_width)

    def screen_size_changed(self, lines, cols):
        self.dirty = True
        height, width, y, x = self._calculate_dimensions(lines, cols)
        self.win.resize(height, width)
        self.panel.move(y, x)

    def redraw(self, force=False):
        # Draw content into the window buffer; the screen's update_panels() +
        # doupdate() pass composites it. force=True re-touches the whole window so
        # it is re-emitted even when its content is unchanged (used on full redraw).
        if self.dirty or force:
            self.dirty = False
            self.header_dirty = False
            if force:
                self.win.touchwin()
            self.draw()
        elif self.header_dirty:
            self.header_dirty = False
            self.draw_header(self.split_border_sides())

    def border_color(self):
        return curses.color_pair(5 if self.is_active() else 18)

    def draw(self):
        sides = self.split_border_sides()
        if sides is not None:
            # The divider between split panes belongs to neither pane, so it is
            # drawn in a fixed neutral colour (not the owning pane's active /
            # inactive border colour).
            self.win.attrset(Screen.color(SPLIT_DIVIDER_COLOR))
            h, w = self.win.getmaxyx()
            # Full height (row 0 included) so the divider runs the whole way up
            # between the two title bars, not just the body rows.
            if 'left' in sides:
                self.win.vline(0, 0, curses.ACS_VLINE, h)
            if 'right' in sides:
                self.win.vline(0, w - 1, curses.ACS_VLINE, h)
            if 'bottom' in sides:
                self.win.hline(h - 1, 0, curses.ACS_HLINE, w)
        elif self.view_mode == 'window':
            self.win.attrset(self.border_color())
            self.win.box()

        self.draw_header(sides)

        if self != Gitkcli.log.view and self.get_parent() != Gitkcli.log.view:
            Gitkcli.log.debug(f'Draw view {self.id}')

    def draw_header(self, sides):
        """Draw only the header line (row 0). Called by the full draw() and, on
        its own, when header_dirty is set so a title change (e.g. a live counter)
        repaints without re-rendering the body."""
        if not self.header_item:
            return
        _, cols = self.win.getmaxyx()
        if self.is_popup:
            # New style: the title sits inset in the box's top border
            # (┌─ Title ───────┐), no banner and no [X]. Drawn in the box's
            # own colour (red for warning/error dialogs).
            title = self.header_item.get_text().strip()
            if title:
                label = f' {title} '[:max(0, cols - 4)]
                if label:
                    self.win.move(0, 2)
                    self.win.addstr(label, self.border_color() | curses.A_BOLD)
        else:
            # Rule-line title bar (main views). Columns [left, right) the
            # title may paint; the divider / box-corner columns are left for
            # the vline (split) or box corners (┌─ Title ──[X]─┐, floated).
            if isinstance(self.header_item, WindowTopBarItem) and hasattr(self, 'items'):
                current = self._selected + 1 if self.items else 0
                self.header_item.set_counter(current, len(self.items))
            left, right = 0, cols
            if sides is not None:
                if 'left' in sides:
                    left = 1
                if 'right' in sides:
                    right = cols - 1
            elif self.view_mode == 'window':
                left, right = 1, cols - 1
            self.win.move(0, left)
            self.header_item.draw_line(self.win, 0, right - left, self.is_active(), False, False)

    def on_activated(self):
        Gitkcli.log.debug(f'View {self.id} activated')

    def on_deactivated(self):
        Gitkcli.log.debug(f'View {self.id} deactivated')

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'left-release':
            self.stop_resize()
        if mouse.event_type == 'left-move' and self.resize_mode:
            self.handle_resize()
            return True
        if self.win.enclose(Gitkcli.mouse.screen_y, Gitkcli.mouse.screen_x):
            if mouse.y == 0 and self.header_item and self.header_item.handle_mouse_input(mouse):
                if 'left-click' == mouse.event_type or 'double-click' == mouse.event_type:
                    Gitkcli.mouse.clicked_item = self.header_item
                return True
            if mouse.event_type == 'left-click' and self.start_resize(Gitkcli.mouse.screen_x, Gitkcli.mouse.screen_y):
                return True
        elif self.is_popup and 'click' in mouse.event_type:
            self.hide()
            return True
        return False

    def handle_input(self, keyboard) -> bool:
        return False

    def get_parent(self):
        try:
            index = Gitkcli.screen.showed_views.index(self)
            if index > 0:
                return Gitkcli.screen.showed_views[index - 1]
        except ValueError:
            pass
        return None
    
    def is_active(self) -> bool:
        return len(Gitkcli.screen.showed_views) > 0 and Gitkcli.screen.showed_views[-1] == self

    def show(self):
        if self.is_active():
            return
        prev_view = Gitkcli.screen.get_active_view()
        if self in Gitkcli.screen.showed_views:
            Gitkcli.screen.showed_views.remove(self)
        Gitkcli.screen.showed_views.append(self)
        self.panel.show()
        self.panel.top()
        self.dirty = True
        if prev_view:
            # The outgoing top view must repaint to drop its active border/title
            # colour - active state keys off z-order, not overlap with us.
            prev_view.dirty = True
            prev_view.on_deactivated()
        self.on_activated()

    def hide(self):
        if len(Gitkcli.screen.showed_views) > 0:
            if not self in Gitkcli.screen.showed_views:
                return
            deactivated = Gitkcli.screen.showed_views[-1] == self
            # Hiding the panel uncovers whatever was underneath; update_panels()
            # repaints it for us, no manual footprint cleanup needed.
            self.panel.hide()
            Gitkcli.screen.showed_views.remove(self)
            if deactivated:
                self.on_deactivated()
                # Repaint the newly-exposed top view with its active styling.
                new_active = Gitkcli.screen.get_active_view()
                if new_active:
                    new_active.dirty = True

class ListView(View):
    def __init__(self, id:str, view_mode:str = 'fullscreen',
                 x:typing.Optional[int] = None, y:typing.Optional[int] = None,
                 height:typing.Optional[int] = None, width:typing.Optional[int] = None):

        super().__init__(id, view_mode, x, y, height, width)
        self.items = []
        self._selected:int = 0
        self._offset_y:int = 0
        self._offset_x:int = 0
        self.autoscroll:bool = False
        self._search_dialog:typing.Optional[SearchDialogPopup] = None

    def set_search_dialog(self, search_dialog:"SearchDialogPopup"):
        self._search_dialog = search_dialog
        self._search_dialog.parent_list_view = self

    def _resize_centered(self, height, width):
        """Resize to height x width and re-centre on screen. Used by popups that
        size themselves to their content; fixed_x/y = None centres it."""
        self.set_dimensions(None, None, height, width)

    def _focus_button_row(self, focus = 'first'):
        """Make only self._button_row navigable (Left/Right pick a button, Enter
        activates it) and select it. focus='last' defaults to the final button -
        used for destructive confirmations so a bare Enter lands on [Cancel]."""
        for item in self.items:
            item.is_selectable = False
        self._button_row.is_selectable = True
        (self._button_row.focus_last if focus == 'last' else self._button_row.reset_focus)()
        self._selected = len(self.items) - 1

    def _show_message_box(self, lines, button_row_item, focus = 'first'):
        """Lay out a content-sized popup and show it: a spacer, the message
        `lines` (each a str or (text, color) tuple, indented two spaces), a
        spacer, then the button row - the only navigable item. Sizes to the
        widest of the header, the lines and the button row, then centres."""
        self.clear()
        self.append(SpacerListItem())
        content = len(self.header_item.get_text())
        for line in lines:
            text, color = line if isinstance(line, tuple) else (line, 1)
            self.append(TextListItem('  ' + text, color, selectable = False))
            content = max(content, len(text) + 2)  # + 2 for the left indent
        self.append(SpacerListItem())
        self._button_row = button_row_item
        self.append(button_row_item)
        content = max(content, len(button_row_item.get_text()))
        self._focus_button_row(focus)
        # content + 2 (right margin so text doesn't touch the border) + 2 (box sides)
        self._resize_centered(len(self.items) + 2, max(40, content + 4))
        self.show()

    def copy_text_to_clipboard(self):
        text = "\n".join(item.get_text() for item in self.items)
        if text:
            copy_to_clipboard(text)

    def copy_text_range_to_clipboard(self, to_item):
        text = ""
        found = False
        for i, item in enumerate(self.items):
            if not found and item == to_item:
                found = True
            if found or i >= self._selected:
                text += "\n" + item.get_text()
            if found and i >= self._selected:
                break
        copy_to_clipboard(text)

    def append(self, item):
        """Add item to end of list"""
        self.items.append(item)
        if len(self.items) - self._offset_y < self.height:
            self.dirty = True
        else:
            # The new row is off-screen, so the body need not be redrawn — but
            # the header's "[current/total]" counter changed, so request a cheap
            # header-only redraw to keep it current while items stream in.
            self.header_dirty = True
        if self.autoscroll:
            self._offset_y = max(0, len(self.items) - self.height)
        
    def clear(self):
        Gitkcli.log.debug(f'Clear view {self.id}')
        self.items = []
        self.set_selected(0)
        self._offset_y = 0
        self._offset_x = 0
        self.dirty = True

    def set_selected(self, what:int|str|re.Pattern, visible_mode = 'center') -> bool:
        new_index = None

        if isinstance(what, int):
            if (0 <= what < len(self.items)) or (what <= 0 and len(self.items) == 0):
                new_index = what
        elif isinstance(what, (str, re.Pattern)):
            test = (lambda t: what in t) if isinstance(what, str) else (lambda t: what.match(t))
            for i, item in enumerate(Gitkcli.git_diff.items):
                if test(item.get_text()):
                    new_index = i
                    break

        if new_index is not None:
            if self._selected != new_index:

                # skip non-selectable items
                direction = 1 if new_index > self._selected else -1
                if 0 <= new_index < len(self.items) and not self.items[new_index].is_selectable:
                    for dir in [direction, -direction]:
                        i = new_index + dir
                        while 0 <= i < len(self.items) and i != self._selected:
                            if self.items[i].is_selectable:
                                new_index = i
                                break
                            i += dir
                    if not self.items[new_index].is_selectable:
                        return False

                self._selected = new_index
                self.dirty = True

                if self._offset_y <= self._selected < self._offset_y + self.height:
                    # do not change view offset when item is already visible
                    return True

                if visible_mode == 'center':
                    self._offset_y = max(0, min(self._selected - int(self.height / 2), len(self.items) - self.height))
                elif visible_mode == 'top':
                    self._offset_y = max(0, self._selected)
                elif visible_mode == 'bottom':
                    self._offset_y = max(0, self._selected - self.height + 1)
            return True

        return False

    def get_selected(self) -> typing.Any:
        if 0 <= self._selected < len(self.items):
            return self.items[self._selected]
        else:
            return None

    def search(self, backward:bool = False, repeat:bool = False):
        if not self._search_dialog:
            return

        ranges = []
        if not backward:
            ranges.append(range(self._selected + 1, len(self.items)))
            if repeat:
                ranges.append(range(0, self._selected + 1))
        else:
            ranges.append(range(self._selected - 1, -1, -1))
            if repeat:
                ranges.append(range(len(self.items) - 1, self._selected - 1, -1))

        for search_range in ranges:
            for i in search_range:
                if self._search_dialog.matches(self.items[i]):
                    self.set_selected(i)
                    return

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'wheel-up':
            self._offset_y = max(0, self._offset_y - 5)
            return True
        if mouse.event_type == 'wheel-down':
            self._offset_y = min(self._offset_y + 5, max(0, len(self.items) - self.height))
            return True

        if not self.resize_mode:
            view_x = mouse.x - self.x
            view_y = mouse.y - self.y
            index = self._offset_y + view_y

            if 0 <= view_y < self.height and 0 <= view_x < self.width and 0 <= index < len(self.items):
                selected = False
                if 'move' in mouse.event_type:
                    if self._selected == index:
                        return False # do not redraw when hovering over same item
                if mouse.event_type == 'left-click' or mouse.event_type == 'double-click' or ('move' in mouse.event_type and self in Gitkcli.mouse.movement_capture):
                    if self.items[index].is_selectable:
                        self.set_selected(index)
                        selected = True
                item = self.items[index]
                # hand the item its own coordinates, then restore the view-relative
                # ones so a fall-through to super() still sees the right position
                saved_x, saved_y = mouse.x, mouse.y
                mouse.x = view_x + self._offset_x
                mouse.y = index
                handled = item.handle_mouse_input(mouse)
                if handled and ('left-click' == mouse.event_type or 'double-click' == mouse.event_type):
                    Gitkcli.mouse.clicked_item = item
                if selected or handled:
                    return True
                mouse.x, mouse.y = saved_x, saved_y

        return super().handle_mouse_input(mouse)

    def handle_input(self, keyboard):
        key = keyboard.key
        if not self.items:
            return super().handle_input(keyboard)

        selected_item = self.get_selected()
        if selected_item and selected_item.handle_input(keyboard):
            self.dirty = True
            return True

        if key == curses.KEY_UP or key == ord('k'):
            self.set_selected(self._selected - 1, visible_mode = 'top')
        elif key == curses.KEY_DOWN or key == ord('j'):
            self.set_selected(self._selected + 1, visible_mode = 'bottom')
        elif key == curses.KEY_LEFT or key == ord('h'):
            if self._offset_x - HORIZONTAL_OFFSET_JUMP >= 0:
                self._offset_x -= HORIZONTAL_OFFSET_JUMP
            else:
                self._offset_x = 0
        elif key == curses.KEY_RIGHT or key == ord('l'):
            max_length = 0
            for i in range(self._offset_y, min(self._offset_y + self.height, len(self.items))):
                length = len(self.items[i].get_text())
                if length > max_length:
                    max_length = length
            if self._offset_x + self.width < max_length:
                self._offset_x += HORIZONTAL_OFFSET_JUMP
        elif key == curses.KEY_PPAGE or key == KEY_CTRL('b'):
            self._offset_y = max(0, self._offset_y - self.height)
            self.set_selected(max(0, self._selected - self.height))
        elif key == curses.KEY_NPAGE or key == KEY_CTRL('f'):
            self._offset_y = min(self._offset_y + self.height, max(0, len(self.items) - self.height))
            self.set_selected(min(self._selected + self.height, max(0, len(self.items) - 1)))
        elif key == curses.KEY_HOME or key == ord('g'):
            self.set_selected(0)
        elif key == curses.KEY_END or key == ord('G'):
            self.set_selected(max(0, len(self.items) - 1))
        elif key == ord('/'):
            if self._search_dialog:
                self._search_dialog.clear()
                self._search_dialog.show()
        elif key == ord('n'):
            self.search()
        elif key == ord('N'):
            self.search(backward = True)
        else:
            return super().handle_input(keyboard)

        return True

    def draw(self):
        separator_items = []
        for i in range(0, min(self.height, len(self.items) - self._offset_y)):
            idx = i + self._offset_y
            item = self.items[idx]
            selected = idx == self._selected
            matched = self._search_dialog.matches(item) if self._search_dialog else False

            # curses throws exception if you want to write a character in bottom left corner
            width = self.width
            if i == self.height - 1:
                width -= 1

            if item.is_separator:
                separator_items.append((i, width))
            else:
                self.win.move(self.y + i, self.x)
                item.draw_line(self.win, self._offset_x, width, selected, matched, False)

        self.win.clrtobot()
        super().draw()

        if separator_items:
            color = 5 if self.is_active() else 16
            # Joins onto the neutral split divider use its colour, not the pane's.
            join = Screen.color(SPLIT_DIVIDER_COLOR)
            sides = self.split_border_sides()
            for pair in separator_items:
                i, width = pair
                if sides is not None:
                    # split pane: join only the borders that are actually drawn
                    if 'left' in sides:
                        self.win.move(self.y + i, self.x - 1)
                        self.win.addstr('├', join)
                    else:
                        self.win.move(self.y + i, self.x)
                    self.win.addstr('─' * width, Screen.color(color))
                    if 'right' in sides:
                        self.win.addstr('┤', join)
                elif self.view_mode == 'window':
                    self.win.move(self.y + i, self.x-1)
                    self.win.addstr('├', Screen.color(color))
                    self.win.addstr('─' * width, Screen.color(color))
                    self.win.addstr('┤', Screen.color(color))
                else:
                    self.win.move(self.y + i, self.x)
                    self.win.addstr('─' * width, Screen.color(color))

def _raise_split_sibling(view, sibling):
    """Keep both split panes adjacent on top of the stack with `view` focused.

    Focusing a pane (click, F1, F3, ...) goes through View.show(); in split view
    we first raise the sibling so the side-by-side / stacked layout is restored
    even after a fullscreen view (logs, refs) temporarily covered it.
    """
    if not Gitkcli.split_active() or Gitkcli._raising_split_sibling:
        return
    views = Gitkcli.screen.showed_views
    if len(views) >= 2 and views[-1] is view and views[-2] is sibling:
        return  # already the top two in the right order
    Gitkcli._raising_split_sibling = True
    try:
        sibling.show()
    finally:
        Gitkcli._raising_split_sibling = False

class GitLogView(ListView):
    def __init__(self, git_args:typing.List, cmd_args:typing.List):
        super().__init__(ID_GIT_LOG, 'fullscreen');

        self.commits = {} # map: git_id --> { parents, date, author, title }

        self.marked_commit_id = ''
        self.jump_list = []
        self.jump_index = 0
        self.head_branch = ''
        self.head_id = ''

        self.show_commit_id = True
        self.show_commit_date = True
        self.show_commit_author = True

        self._cli_args = git_args + cmd_args
        self.pref_flags = ''
        self.job = GitLogJob(ID_GIT_LOG, list(self._cli_args))
        self.job_git_refresh_head = GitRefreshHeadJob()
        self.job_git_search = GitSearchJob(cmd_args)
        self.view_reset = ResetDialogPopup()

    def set_pref_flags(self, flags: str):
        self.pref_flags = flags
        self.job.args = list(self._cli_args) + flags.split()

        repo_name = os.path.basename(Job.run_job(['git', 'rev-parse', '--show-toplevel']).stdout.strip())
        self.set_header_item(WindowTopBarItem(repo_name, [
                SplitButtonSegment(30),
                ButtonSegment("[<-]", lambda: self.move_in_jump_list(+1), 30),
                ButtonSegment("[->]", lambda: self.move_in_jump_list(-1), 30)
            ], title_color = 5))

        self.set_search_dialog(GitSearchDialogPopup());

    def add_commit(self, id, commit):
        if id in self.commits:
            return False
        self.commits[id] = commit
        return True

    def refresh_head(self):
        commit_id = self.head_id
        if commit_id:
            self.job_git_refresh_head.start_job(['--reverse', f'{commit_id}..HEAD'])
        self.check_uncommitted_changes()

    def reload_commits(self):
        self.clear()
        self.job.start_job()
        self.check_uncommitted_changes()

    def show(self):
        _raise_split_sibling(self, Gitkcli.git_diff)
        super().show()

    def set_selected(self, what:int|str|re.Pattern, visible_mode = 'center') -> bool:
        ret = super().set_selected(what, visible_mode)
        if Gitkcli.screen.is_view_visible(Gitkcli.git_diff):
            item = self.get_selected()
            if item:
                item.load_to_view()
        return ret

    def check_uncommitted_changes(self):
        to_remove = 0
        for i in range(min(2, len(self.items))):
            if hasattr(self.items[i], 'id') and self.items[i].id.startswith('local'):
                to_remove += 1
        for _ in range(to_remove):
            self.items.pop(0)
            if self._selected > 0:
                self._selected -= 1
            if self._offset_y > 0:
                self._offset_y -= 1

        # Check for staged changes
        result = Job.run_job(['git', 'diff', '--cached', '--quiet'])
        has_staged = result.returncode != 0
        if has_staged:
            self.prepend_commit(UncommittedChangesListItem(staged = True))

        # Check for working directory changes
        result = Job.run_job(['git', 'diff', '--quiet'])
        has_working = result.returncode != 0
        if has_working:
            self.prepend_commit(UncommittedChangesListItem())

    def prepend_commit(self, item):
        offset = 0
        for i in range(min(2, len(self.items))):
            if item.id.startswith('local'):
                if self.items[i].id == item.id:
                    return
            elif self.items[i].id.startswith('local'):
                offset += 1
        self.items.insert(offset, item)
        if self._offset_y > 0:
            self._offset_y += 1
        self.set_selected(self._selected + 1)

    def select_commit(self, id:str) -> typing.Optional[CommitListItem]:
        for idx, item in enumerate(self.items):
            if isinstance(item, CommitListItem) and id == item.id:
                self.set_selected(idx)
                return item
        return None

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
            Gitkcli.git_diff.job.selected_line_map[commit_id] = (line, offset_y)

        was_same_commit = (Gitkcli.git_diff.commit_id == commit_id
                           and (not Gitkcli.git_diff.is_diff or is_local))

        # Move the git_log cursor without going through GitLogView.set_selected →
        # *ListItem.load_to_view → show_commit/show_diff, which would re-push to
        # the jumplist and clobber forward entries.
        super().set_selected(idx)

        if was_same_commit:
            if line is not None:
                Gitkcli.git_diff.restore_view_position(line, offset_y)
        elif is_local:
            item = self.items[idx]
            Gitkcli.git_diff.job.show_diff('HEAD', cached=item._staged, title=item.txt,
                                            view_id=item.id, add_to_jump_list=False)
        else:
            Gitkcli.git_diff.job.show_commit(commit_id, add_to_jump_list=False)
        return True

    def get_selected_commit_id(self):
        selected_item = self.get_selected()
        if selected_item:
            return selected_item.id
        return ''

    def cherry_pick(self, commit_id = None):
        Job.run_job(['git', 'cherry-pick', '--abort'])
        commit_id = commit_id or self.get_selected_commit_id()
        Gitkcli.run_git(['git', 'cherry-pick', '-m', '1', commit_id],
                        ok=f'Commit {commit_id} cherry picked successfully',
                        err='Error during cherry-pick', refresh_head=True, reload_refs=True)

    def revert(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        Gitkcli.run_git(['git', 'revert', '--no-edit', '-m', '1', commit_id],
                        ok=f'Commit {commit_id} reverted successfully',
                        err='Error during revert', refresh_head=True, reload_refs=True)

    def confirm_reset(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        if not commit_id or commit_id.startswith('local'):
            Gitkcli.log.warning('Select a commit to reset the current branch to')
            return
        self.view_reset.open(commit_id)

    def reset(self, mode, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        Gitkcli.run_git(['git', 'reset', mode, commit_id],
                        ok=f'{mode[2:].capitalize()} reset to {commit_id[:8]}',
                        err=f'Error during {mode} reset', reload_refs=True, check_uncommitted=True)

    def clean_uncommitted_changes(self, staged:bool = False):
        if staged:
            result = Job.run_job(['git', 'stash', 'save', '--keep-index'])
            if result.returncode == 0:
                result = Job.run_job(['git', 'reset', '--hard'])
            if result.returncode == 0:
                result = Job.run_job(['git', 'stash', 'pop'])
        else:
            result = Job.run_job(['git', 'restore', '.'])
        if result.returncode == 0:
            Gitkcli.git_refs.reload_refs()
            Gitkcli.git_log.check_uncommitted_changes()
        else:
            Gitkcli.log.error(f"Error cleaning {'staged' if staged else 'unstaged'} changes: {result.stderr}")

    def mark_commit(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        self.marked_commit_id = commit_id
        self.dirty = True
    
    def diff_commits(self, old_commit_id, new_commit_id):
        Gitkcli.git_diff.job.show_diff(old_commit_id, new_commit_id)
        Gitkcli.git_diff.show()

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == ord('q'):
            Gitkcli.exit_program()
        elif key == curses.KEY_EXIT:
            if Gitkcli.split_active():
                Gitkcli.set_split_mode('off')   # Esc on the log pane leaves split view
            else:
                Gitkcli.exit_program()
        elif key == ord('b'):
            Gitkcli.git_refs.view_new_ref.create_ref(self.get_selected_commit_id())
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

class DynamicTextSegment(TextSegment):
    """TextSegment whose text is recomputed by a getter on every draw."""
    def __init__(self, getter, color = 1):
        super().__init__('', color)
        self.getter = getter

    def get_text(self):
        return str(self.getter())

class HighlightToggleSegment(ButtonSegment):
    """Header button with a fixed label, highlighted while its state is on."""
    def __init__(self, label, is_active, on_toggle, color = 30):
        super().__init__(label, on_toggle, color)
        self._is_active = is_active

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return super().draw(win, offset, width, selected, matched, self._is_active())

class GitDiffView(ListView):
    def __init__(self):
        super().__init__(ID_GIT_DIFF, 'fullscreen')

        self.context_size = 3
        self.rename_limit = 1570
        self.ignore_whitespace = False
        self.job = GitDiffJob()

        self.commit_id = ''
        self.is_diff = False

        self.set_header_item(WindowTopBarItem('Git commit diff', [
            TextSegment("Context:", 30),
            DynamicTextSegment(lambda: Gitkcli.git_diff.context_size, 30),
            ButtonSegment("[+]", lambda: self.change_context(+1), 30),
            ButtonSegment("[-]", lambda: self.change_context(-1), 30),
            HighlightToggleSegment("[Ignore whitespace]",
                                   lambda: Gitkcli.git_diff.ignore_whitespace,
                                   lambda: Gitkcli.git_diff.change_ignore_whitespace(), 30),
            ButtonSegment("[<-]", lambda: Gitkcli.git_log.move_in_jump_list(+1), 30),
            ButtonSegment("[->]", lambda: Gitkcli.git_log.move_in_jump_list(-1), 30)
        ], title_color = 5))

        self.set_search_dialog(SearchDialogPopup(ID_GIT_DIFF_SEARCH))

    def clear(self):
        self.commit_id = ''
        self.is_diff = False
        super().clear()

    def show(self):
        _raise_split_sibling(self, Gitkcli.git_log)
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
        if Gitkcli.split_active() and (key == ord('q') or key == curses.KEY_EXIT):
            # Esc/q in split view steps back to the log pane and stays split,
            # rather than collapsing the split.
            Gitkcli.git_log.show()
            return True
        if key == KEY_CTRL('n'):
            Gitkcli.git_log.handle_input(KeyboardState(curses.KEY_DOWN))
        elif key == KEY_CTRL('p'):
            Gitkcli.git_log.handle_input(KeyboardState(curses.KEY_UP))
        elif key in (ord('g'), ord('G'), curses.KEY_HOME, curses.KEY_END):
            track = self._tracks_position()
            if track:
                Gitkcli.git_log.add_to_jump_list(self.commit_id, self._selected, self._offset_y)
            ret = super().handle_input(keyboard)
            if track:
                Gitkcli.git_log.add_to_jump_list(self.commit_id, self._selected, self._offset_y)
            return ret
        else:
            return super().handle_input(keyboard)
        return True

class GitRefsView(ListView):
    def __init__(self):
        super().__init__(ID_GIT_REFS) 

        self.refs = {} # map: git_id --> [ { 'type':<ref-type>, 'name':<ref-name> } ]

        self.set_header_item(WindowTopBarItem('Git references', title_color = 5))
        self.set_search_dialog(SearchDialogPopup(ID_GIT_REFS_SEARCH))

        self.view_new_ref = NewRefDialogPopup()
        self.view_ref_push = RefPushDialogPopup()

        self.job = GitRefsJob()

    def reload_refs(self):
        self.clear()
        self.job.start_job()

    @classmethod
    def get_ref_color_and_title(cls, ref):
        title = f"({ref['name']})"
        color = 11
        if ref['type'] == 'head':
            color = 13
            if Gitkcli.git_log.head_branch:
                title += ' ->'
        elif ref['type'] == 'heads':
            title = f"[{ref['name']}]"
        elif ref['type'] == 'remotes':
            color = 15
            title = f"{{{ref['name']}}}"
        elif ref['type'] == 'tags':
            color = 12
            title = f"<{ref['name']}>"
        elif ref['type'] == 'stash':
            color = 14
        return color, title

class LogView(ListView):
    def __init__(self):
        super().__init__(ID_LOG, 'fullscreen') 

        self.set_header_item(WindowTopBarItem('Logs', [
            ButtonSegment("[Clear]", lambda: self.clear(), 30),
            HighlightToggleSegment("[Autoscroll]", lambda: self.autoscroll, self.toggle_autoscroll, 30),
            TextSegment("  Log level:", 30),
            DynamicTextSegment(lambda: Gitkcli.log.level, 30),
            ButtonSegment("[+]", lambda: self.change_log_level(+1), 30),
            ButtonSegment("[-]", lambda: self.change_log_level(-1), 30)], title_color = 5))

        self.set_search_dialog(SearchDialogPopup(ID_LOG_SEARCH))

    def change_log_level(self, value):
        Gitkcli.log.level = max(0, min(5, Gitkcli.log.level + value))
        self.dirty = True

    def toggle_autoscroll(self):
        self.autoscroll = not self.autoscroll
        if self.autoscroll:
            self._offset_y = max(0, len(self.items) - self.height)
        self.dirty = True

class ContextMenuItem(TextListItem):
    def __init__(self, text, action, args=[], is_selectable=True):
        super().__init__(text, selectable = is_selectable, dim = not is_selectable)
        self.action = action
        self.args = args if args else []

    def activate(self) -> bool:
        if self.is_selectable:
            Gitkcli.screen.hide_active_view()
            self.action(*self.args)
        return True

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type in ('left-click', 'double-click', 'right-release'):
            return self.activate()
        return super().handle_mouse_input(mouse)

class ContextMenu(ListView):
    def __init__(self):
        super().__init__(ID_CONTEXT_MENU, 'window')
        self.is_popup = True

    def on_activated(self):
        super().on_activated()
        Gitkcli.mouse.capture_mouse_movement(True, self)

    def on_deactivated(self):
        super().on_deactivated()
        Gitkcli.mouse.capture_mouse_movement(False, self)
        
    def _append_copy_items(self, view, item):
        """The line/range/all clipboard trio shared by the git-log, git-diff and
        log context menus."""
        self.append(ContextMenuItem("Copy line to clipboard", item.copy_text_to_clipboard))
        self.append(ContextMenuItem("Copy range to clipboard", view.copy_text_range_to_clipboard, [item]))
        self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard))

    def show_context_menu(self, item, view_id:str = '') -> bool:
        if Gitkcli.screen.showed_views[-1] == self:
            return True
        self.clear()
        self._selected = -1
        if not view_id:
            view_id = Gitkcli.screen.showed_views[-1].id
        view = Gitkcli.screen.get_active_view()
        x = Gitkcli.mouse.screen_x
        y = Gitkcli.mouse.screen_y
        if item == Gitkcli: # main menu
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
            self.append(ContextMenuItem("Preferences", Gitkcli.preferences.show))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Quit", item.exit_program))
        elif view_id == 'git-log' and hasattr(item, 'id'):
            if item.id == 'local-staged':
                self.append(ContextMenuItem("Clear staged changes", view.clean_uncommitted_changes, [True]))
            elif item.id == 'local-working':
                self.append(ContextMenuItem("Clear unstaged changes", view.clean_uncommitted_changes, [False]))
            else:
                self.append(ContextMenuItem("Create new branch", Gitkcli.git_refs.view_new_ref.create_ref, [item.id]))
                self.append(ContextMenuItem("Create new tag", Gitkcli.git_refs.view_new_ref.create_ref, [item.id, 'tag']))
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
                self.append(ContextMenuItem("Rename this branch", Gitkcli.git_refs.view_new_ref.create_ref, [item.data['name']]))
                self.append(ContextMenuItem("Copy branch name", copy_to_clipboard, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Push branch to remote", self.push_ref_to_remote, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Remove this branch", self.remove_branch, [item.data['name']]))
            elif item.data['type'] == 'tags':
                self.append(ContextMenuItem("Copy tag name", copy_to_clipboard, [item.data['name']]))
                self.append(ContextMenuItem("Show tag annotation", Gitkcli.git_diff.job.show_tag_annotation, [item.data.get('tag_id')], 'tag_id' in item.data))
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
        Gitkcli.run_git(args, ok=f'Switched to branch {branch_name}',
                        err='Error checking out branch',
                        refresh_head=True, reload_refs=True, check_uncommitted=True,
                        force=force, reasons=('would be overwritten by checkout',),
                        retry=lambda: self.checkout_branch(branch_name, True),
                        title=' Checkout blocked',
                        lines=[(f"Local files conflict with switching to '{branch_name}'.", 4),
                               ("Force checkout? Conflicting local files will be lost.", 2)],
                        label='[Force checkout]')

    def push_ref_to_remote(self, branch_name):
        Gitkcli.git_refs.view_ref_push.ref_name = branch_name
        Gitkcli.git_refs.view_ref_push.header_item.set_text(f"Push ref: {branch_name}")
        Gitkcli.git_refs.view_ref_push.clear()
        Gitkcli.git_refs.view_ref_push.show()

    def remove_branch(self, branch_name):
        Gitkcli.run_git(['git', 'branch', '-D', branch_name],
                        ok=f'Deleted branch {branch_name}',
                        err='Error deleting branch', reload_refs=True)

    def remove_tag(self, tag_name):
        remotes = Job.run_job(['git', 'remote']).stdout.splitlines()
        removed_from_remotes = []

        result = Job.run_job(['git', 'tag', '-d', tag_name])
        if result.returncode == 0:
            removed_from_remotes.append('<local>')

        for remote in remotes:
            result = Job.run_job(['git', 'push', '--delete', remote, tag_name])
            if result.returncode == 0:
                removed_from_remotes.append(remote)

        if removed_from_remotes:
            Gitkcli.git_refs.reload_refs()
            Gitkcli.log.success(f'Deleted tag {tag_name} from remotes: ' + ' '.join(removed_from_remotes))
        else:
            Gitkcli.log.error(f"Error deleting tag: {result.stderr}")

    def remove_remote_ref(self, remote_ref):
        remote, branch = remote_ref.split('/', 1)
        Gitkcli.run_git(['git', 'push', '--delete', remote, branch],
                        ok=f'Deleted remote branch {remote_ref}',
                        err='Error deleting remote branch', reload_refs=True)

class UserInputListItem(Item):
    def __init__(self, color = 1):
        super().__init__()
        self.txt = ''
        self.offset = 0
        self.cursor_pos = 0
        self.color = color

    def clear(self):
        self.txt = ''
        self.offset = 0
        self.cursor_pos = 0

    def get_text(self):
        return self.txt

    def set_text(self, txt:str):
        self.txt = txt
        self.offset = 0
        self.cursor_pos = len(txt)

    def prev_word_pos(self):
        pos = self.cursor_pos
        while pos > 0 and self.txt[pos-1].isspace():
            pos -= 1
        while pos > 0 and not self.txt[pos-1].isspace():
            pos -= 1
        return pos

    def next_word_pos(self):
        pos = self.cursor_pos
        length = len(self.txt)
        while pos < length and self.txt[pos].isspace():
            pos += 1
        while pos < length and not self.txt[pos].isspace():
            pos += 1
        return pos

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_BACKSPACE or key == 127:  # Backspace
            if self.cursor_pos > 0:
                self.txt = self.txt[:self.cursor_pos-1] + self.txt[self.cursor_pos:]
                self.cursor_pos -= 1

        elif key == KEY_CTRL_BACKSPACE:  # Ctrl+Backspace: delete previous word
            start = self.prev_word_pos()
            self.txt = self.txt[:start] + self.txt[self.cursor_pos:]
            self.cursor_pos = start

        elif key == curses.KEY_DC:  # Delete key
            if self.cursor_pos < len(self.txt):
                self.txt = self.txt[:self.cursor_pos] + self.txt[self.cursor_pos+1:]

        elif key == KEY_CTRL_DEL:  # Ctrl+Delete: clear whole text
            self.txt = ''
            self.cursor_pos = 0
            self.offset = 0

        elif key == curses.KEY_LEFT:  # Left arrow
            if self.cursor_pos > 0:
                self.cursor_pos -= 1

        elif key == curses.KEY_RIGHT:  # Right arrow
            if self.cursor_pos < len(self.txt):
                self.cursor_pos += 1

        elif key == KEY_CTRL_LEFT:  # Ctrl+Left: previous word
            self.cursor_pos = self.prev_word_pos()

        elif key == KEY_CTRL_RIGHT:  # Ctrl+Right: next word
            self.cursor_pos = self.next_word_pos()

        elif key == curses.KEY_HOME:  # Home key
            self.cursor_pos = 0
            
        elif key == curses.KEY_END:  # End key
            self.cursor_pos = len(self.txt)
            
        elif 32 <= key <= 126:  # Printable characters
            self.txt = self.txt[:self.cursor_pos] + chr(key) + self.txt[self.cursor_pos:]
            self.cursor_pos += 1

        else:
            return super().handle_input(keyboard)

        return True

    def handle_mouse_input(self, mouse) -> bool:
        if mouse.event_type == 'left-click' or mouse.event_type == 'double-click':
            self.cursor_pos = mouse.x if mouse.x < len(self.txt) else len(self.txt)
            return True
        else:
            return super().handle_mouse_input(mouse)

    def draw_line(self, win, offset, width, selected, matched, marked):
        # TODO: update self.offset according to offset so that cursor is always visible

        left_txt = self.txt[self.offset:self.offset+self.cursor_pos]
        right_txt = self.txt[self.offset+self.cursor_pos:self.offset+width-1]

        win.addstr(left_txt, Screen.color(self.color, selected, marked, matched))
        win.addch(ord(' '), curses.A_REVERSE | curses.A_BLINK)
        win.addstr(right_txt, Screen.color(self.color, selected, marked, matched))
        win.addstr(' ' * (width - len(left_txt) - len(right_txt) - 1), Screen.color(self.color, selected, marked, matched))

class ResetModeItem(TextListItem):
    def __init__(self, dialog, mode, txt, color = 1):
        super().__init__(txt, color = color)
        self.dialog = dialog
        self.mode = mode

    def activate(self) -> bool:
        self.dialog.hide()
        Gitkcli.git_log.reset(self.mode, self.dialog.commit_id)
        return True

    def draw_line(self, win, offset, width, selected, matched, marked):
        # Keep the chosen mode highlighted even when focus moves to the buttons
        # (ListView.draw always passes marked=False, so we can't use that flag).
        if self.dialog.selected_mode == self.mode:
            selected = True
        return super().draw_line(win, offset, width, selected, matched, marked)


class ResetDialogPopup(ListView):
    def __init__(self):
        super().__init__(ID_GIT_RESET, 'window', height = 9, width = 68)
        self.is_popup = True
        self.commit_id = ''
        self.selected_mode = '--mixed'
        self.set_header_item(TextListItem(' Reset current branch', 30, expand = True))

        self.target_item = TextListItem('', 4, selectable = False)
        self.append(self.target_item)
        self.append(SeparatorItem())
        self.append(ResetModeItem(self, '--soft',  '  Soft    keep index + working tree (move HEAD)', 3))
        self.append(ResetModeItem(self, '--mixed', '  Mixed   reset index, keep working tree (default)'))
        self.append(ResetModeItem(self, '--hard',  '  Hard    discard index + working tree changes', 2))
        self.append(SeparatorItem())
        self._button_row = button_row(ButtonSegment('[Ok]', self._confirm, 3),
                                      TextSegment('   '),
                                      ButtonSegment('[Cancel]', self.hide))
        self.append(self._button_row)

    def _confirm(self):
        # [Ok] applies whichever reset mode is currently highlighted.
        self.hide()
        Gitkcli.git_log.reset(self.selected_mode, self.commit_id)
        return True

    def set_selected(self, what, visible_mode = 'center'):
        # Track the highlighted mode (keyboard AND mouse funnel through here) so
        # [Ok] knows which reset to run once focus moves to the buttons row.
        result = super().set_selected(what, visible_mode)
        item = self.get_selected()
        if isinstance(item, ResetModeItem):
            self.selected_mode = item.mode
        return result

    def open(self, commit_id):
        self.commit_id = commit_id
        self.selected_mode = '--mixed'
        title = Gitkcli.git_log.commits.get(commit_id, {}).get('title', '')
        if len(title) > 34:
            title = title[:33] + '…'
        self.target_item.set_text(f'  Reset HEAD → {commit_id[:8]}  {title}')
        self.set_selected(3)   # highlight Mixed by default
        self._button_row.reset_focus()
        self.show()

    def handle_input(self, keyboard):
        key = keyboard.key
        if key in (curses.KEY_EXIT, ord('q')):
            self.hide()
            return True
        return super().handle_input(keyboard)


class RefPushDialogPopup(ListView):
    def __init__(self):
        # Fixed width (like the other input dialogs) instead of half the
        # terminal, so the box stays tight on wide screens.
        super().__init__(ID_GIT_REF_PUSH, 'window', height = 5, width = 60)
        self.set_header_item(TextListItem('', 30, expand = True))
        self.is_popup = True

        self.remotes = []
        for remote in Job.run_job(['git', 'remote']).stdout.rstrip().split('\n'):
            self.remotes.append(ToggleSegment(remote, callback = lambda val: self.change_remote(val.txt)))
        self.change_remote(self.remotes[0].txt)

        self.force = ToggleSegment("<Force>")
        self.append(SegmentedListItem([TextSegment("Select remote:")] + self.remotes + [FillerSegment(), TextSegment("Flags:"), self.force]))

        self.append(SpacerListItem())
        self._button_row = button_row(ButtonSegment("[Push]", self._confirm),
                                      ButtonSegment("[Cancel]", self.hide))
        self.append(self._button_row)
        self.ref_name = ''

        # Make the buttons row navigable (Left/Right pick a button, Enter
        # activates it); default focus is [Push] so a bare Enter still pushes.
        self._focus_button_row()

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
        self._do_push(self.remote, self.ref_name, self.force.toggled)

    def _do_push(self, remote, ref_name, force):
        args = ['git', 'push'] + (['-f'] if force else []) + [remote, ref_name]
        Gitkcli.run_git(args, ok=f'Branch pushed {ref_name} to {remote}',
                        err=f"Error pushing ref '{ref_name}'", reload_refs=True,
                        force=force, reasons=('non-fast-forward', 'fetch first', 'would clobber'),
                        retry=lambda: self._do_push(remote, ref_name, True),
                        title=' Push rejected',
                        lines=[(f"Push of '{ref_name}' to '{remote}' was rejected.", 4),
                               "The remote has changes you don't have locally.",
                               ("Force push? This may overwrite remote commits.", 2)],
                        label='[Force push]')

    def handle_input(self, keyboard):
        key = keyboard.key
        # Enter is routed through the buttons row (super -> ButtonRowItem) so it
        # activates the focused button instead of always pushing.
        if key == curses.KEY_EXIT:
            self.hide()
        elif key == curses.KEY_F1:
            self.force.toggle()
        elif key == KEY_TAB: # cycle through remotes
            names = [r.txt for r in self.remotes]
            self.change_remote(names[(names.index(self.remote) + 1) % len(names)])
        else:
            return super().handle_input(keyboard)
        return True

class _RedMessageBoxPopup(ListView):
    """Modal red message box: a red banner header and matching red border,
    sized to its content. Base for the confirm and error dialogs."""
    def __init__(self, id, banner):
        super().__init__(id, 'window', height = 7)
        self.set_header_item(TextListItem(banner, 31, expand = True))  # red banner
        self.is_popup = True

    def border_color(self):
        return curses.color_pair(2)

class ConfirmDialogPopup(_RedMessageBoxPopup):
    """Generic yes/no popup. Used to offer a forced retry after a git
    operation is rejected (ref already exists, non-fast-forward push, ...)."""
    def __init__(self):
        super().__init__(ID_CONFIRM_DIALOG, '')
        self._on_confirm = lambda: None

    def confirm(self, title, lines, on_confirm, confirm_label = '[Yes]', cancel_label = '[Cancel]'):
        # Each entry in `lines` is either a string or a (text, color) tuple
        # (color 4 = yellow, 2 = red) for emphasis. These are destructive
        # force/overwrite confirmations, so default focus to [Cancel].
        self._on_confirm = on_confirm
        self.header_item.set_text(title)
        self._show_message_box(lines,
            button_row(ButtonSegment(confirm_label, self._confirm, 2),
                       TextSegment('   '),
                       ButtonSegment(cancel_label, self.hide)),
            focus = 'last')

    def _confirm(self):
        self.hide()
        self._on_confirm()
        return True

    def handle_input(self, keyboard):
        key = keyboard.key
        if key in (ord('y'), ord('Y')):
            self._confirm()
        elif key in (curses.KEY_EXIT, ord('n'), ord('N'), ord('q')):
            self.hide()
        else:
            # Left/Right move focus between buttons; Enter activates the focused
            # button. Default focus is [Cancel], so a bare Enter cancels; the
            # user Left-arrows to the confirm button to proceed. (y/Y always
            # confirms regardless of focus.)
            super().handle_input(keyboard)
        # Modal: swallow every other key. Otherwise global shortcuts (F1-F5,
        # Ctrl+o/i) would fall through and could bury this popup behind a
        # fullscreen view while its force callback is still armed.
        return True

class ErrorDialogPopup(_RedMessageBoxPopup):
    """Modal red alert with a single [Ok] button. Replaces the old status-bar
    error line: Log.error() pops this with the message. Errors that arrive while
    it is still open (e.g. a job emitting several stderr lines) are coalesced
    into the same dialog instead of stacking a new popup per line."""

    MAX_LINES = 12

    def __init__(self):
        super().__init__(ID_ERROR_DIALOG, ' Error')
        self._lines = []

    def show_error(self, message):
        incoming = [line for line in message.splitlines() if line.strip()] or [message]
        if not self.is_active():
            self._lines = []
        for line in incoming:
            if len(self._lines) < self.MAX_LINES:
                self._lines.append(line)
        self._render()

    def _render(self):
        self._show_message_box([(line, 2) for line in self._lines],
                               button_row(ButtonSegment('[Ok]', self.hide, 2)))

    def handle_input(self, keyboard):
        # Any of Enter / Esc / o / q dismisses; Left/Right keep focus on [Ok].
        if keyboard.key in ENTER_KEYS or keyboard.key in (curses.KEY_EXIT, ord('o'), ord('O'), ord('q')):
            self.hide()
        else:
            super().handle_input(keyboard)
        return True  # modal: swallow every other key

class UserInputDialogPopup(ListView):
    def __init__(self, id:str, title:str, header_item:Item, bottom_item:typing.Optional[Item] = None, width = 60):
        # Compact 3-row layout (no blank spacers): the label/flags header, the
        # input field right below it, and the buttons. A fixed width keeps the
        # box from ballooning to half the terminal on wide screens.
        super().__init__(id, 'window', height = 5, width = width)
        self.set_header_item(TextListItem(title, 30, expand = True))
        self.input = UserInputListItem()
        self.is_popup = True
        self.history_queries = []
        self.history_index = -1

        if not bottom_item:
            bottom_item = SegmentedListItem([FillerSegment(),
                                         ButtonSegment("[Execute]", lambda: self.handle_input(KeyboardState(curses.KEY_ENTER))),
                                         ButtonSegment("[Cancel]", lambda: self.handle_input(KeyboardState(curses.KEY_EXIT))),
                                         FillerSegment()])
            bottom_item.is_selectable = False

        header_item.is_selectable = False

        self.append(header_item)
        self.append(self.input)
        self.append(bottom_item)
        self._selected = 1

    def add_query_to_history(self):
        if self.input.txt and (len(self.history_queries) == 0 or self.history_queries[0] != self.input.txt):
            self.history_queries.insert(0, self.input.txt)

    def execute(self):
        self.add_query_to_history()

    def clear(self):
        self.input.clear()
        self.history_index = -1

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.hide()
            self.execute()

        elif key == curses.KEY_EXIT:
            self.hide()
                
        elif key == curses.KEY_DOWN or key == KEY_CTRL('n'):
            if self.history_index > 0:
                self.history_index -= 1
                self.input.set_text(self.history_queries[self.history_index])
                
        elif key == curses.KEY_UP or key == KEY_CTRL('p') or key == KEY_CTRL('o'):
            if self.history_index + 1 < len(self.history_queries):
                self.history_index += 1
                self.input.set_text(self.history_queries[self.history_index])

        else:
            return super().handle_input(keyboard)
            
        return True

class OnOffToggleSegment(ToggleSegment):
    def __init__(self, toggled=False, color=1):
        super().__init__('', toggled, color=color)
        self.set_toggled(toggled)

    def set_toggled(self, value):
        self.toggled = value
        # Display form: active side in CAPS, inactive side lowercase
        self.txt = '[ON|off]' if self.toggled else '[on|OFF]'

    def toggle(self):
        self.set_toggled(not self.toggled)

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        # Chunks: (text, is_active_side). The active side is highlighted (blue) and CAPS.
        chunks = [
            ('[', None),
            ('ON' if self.toggled else 'on', True),
            ('|', None),
            ('off' if self.toggled else 'OFF', False),
            (']', None),
        ]
        drawn = 0
        pos = 0
        for txt, side in chunks:
            seg_start = pos
            seg_end = pos + len(txt)
            pos = seg_end
            # Intersect this chunk with the visible window [offset, width)
            s = max(seg_start, offset)
            e = min(seg_end, width)
            if s >= e:
                continue
            sub = txt[s - seg_start:e - seg_start]
            highlighted = (side is True) if self.toggled else (side is False)
            win.addstr(sub, Screen.color(self.color, selected, highlighted, marked, dim=not self.enabled))
            drawn += len(sub)
        return drawn

class ChoiceSegment(ButtonSegment):
    """Button that cycles through a fixed list of (value, label) options."""
    def __init__(self, options, value, color=1):
        self.options = options
        self.value = value
        super().__init__('', self._cycle, color)

    def _cycle(self):
        values = [v for v, _ in self.options]
        i = values.index(self.value) if self.value in values else 0
        self.value = values[(i + 1) % len(values)]
        return True

    def set_value(self, value):
        self.value = value

    def get_text(self):
        return '<' + dict(self.options).get(self.value, self.value) + '>'

class PreferenceRow(SegmentedListItem):
    """A label + interactive control (toggle/choice). Enter activates the control."""
    def __init__(self, label, control):
        super().__init__([TextSegment(f'  {label}  '), FillerSegment(), control, TextSegment('  ')])
        self.control = control

    def activate(self) -> bool:
        self.control.activate()
        return True

class PreferencesDialogPopup(ListView):
    def __init__(self):
        super().__init__(ID_PREFERENCES, 'window', height=15, width=50)
        self.is_popup = True
        self.set_header_item(TextListItem(' Preferences', 30, expand=True))

        self.t_show_id     = OnOffToggleSegment()
        self.t_show_date   = OnOffToggleSegment()
        self.t_show_author = OnOffToggleSegment()
        self.t_ign_ws      = OnOffToggleSegment()
        self.t_autoscroll  = OnOffToggleSegment()
        self.c_view_mode   = ChoiceSegment([('fullscreen', 'Fullscreen'),
                                            ('side',       'Horizontal split'),
                                            ('stacked',    'Vertical split')], 'fullscreen')
        self.input_flags   = UserInputListItem()

        self.append(PreferenceRow('Show commit ID',           self.t_show_id))
        self.append(PreferenceRow('Show commit date',         self.t_show_date))
        self.append(PreferenceRow('Show commit author',       self.t_show_author))
        self.append(SeparatorItem())
        self.append(PreferenceRow('Ignore whitespace (diff)', self.t_ign_ws))
        self.append(SeparatorItem())
        self.append(PreferenceRow('Autoscroll (log view)',    self.t_autoscroll))
        self.append(SeparatorItem())
        self.append(PreferenceRow('Default view mode',         self.c_view_mode))
        self.append(SeparatorItem())
        self.append(TextListItem('  Git log default flags:', selectable=False))
        self.append(self.input_flags)

        self._button_row = button_row(ButtonSegment('[Save]', self.on_save),
                                      TextSegment('  '),
                                      ButtonSegment('[Close]', self.on_cancel))
        self.append(self._button_row)
        self._selected = 0

    def on_activated(self):
        self.t_show_id.set_toggled(Gitkcli.git_log.show_commit_id)
        self.t_show_date.set_toggled(Gitkcli.git_log.show_commit_date)
        self.t_show_author.set_toggled(Gitkcli.git_log.show_commit_author)
        self.t_ign_ws.set_toggled(Gitkcli.git_diff.ignore_whitespace)
        self.t_autoscroll.set_toggled(Gitkcli.log.view.autoscroll)
        self.c_view_mode.set_value(Gitkcli.default_view_mode)
        self.input_flags.set_text(Gitkcli.git_log.pref_flags)
        self._button_row.reset_focus()
        self.dirty = True
        super().on_activated()

    def on_save(self):
        Gitkcli.git_log.show_commit_id     = self.t_show_id.toggled
        Gitkcli.git_log.show_commit_date   = self.t_show_date.toggled
        Gitkcli.git_log.show_commit_author = self.t_show_author.toggled
        Gitkcli.log.view.autoscroll        = self.t_autoscroll.toggled
        Gitkcli.git_log.dirty  = True
        Gitkcli.log.view.dirty = True
        if Gitkcli.git_diff.ignore_whitespace != self.t_ign_ws.toggled:
            job = Gitkcli.git_diff.job
            if job.commit_id or job.tag_id or job.old_commit_id:
                Gitkcli.git_diff.change_ignore_whitespace(self.t_ign_ws.toggled)
            else:
                Gitkcli.git_diff.ignore_whitespace = self.t_ign_ws.toggled

        new_flags = self.input_flags.txt.strip()
        if new_flags != Gitkcli.git_log.pref_flags:
            Gitkcli.git_log.set_pref_flags(new_flags)
            Gitkcli.git_log.reload_commits()

        Gitkcli.default_view_mode = self.c_view_mode.value
        # Apply the chosen layout right away; entering a split raises the
        # log/diff panes, so re-show this dialog to keep it on top.
        Gitkcli.set_split_mode(self.c_view_mode.value if self.c_view_mode.value in ('side', 'stacked') else 'off')
        self.show()

        cfg = {
            'git_log':  {'show_commit_id':     self.t_show_id.toggled,
                         'show_commit_date':   self.t_show_date.toggled,
                         'show_commit_author': self.t_show_author.toggled,
                         'flags':              new_flags},
            'git_diff': {'ignore_whitespace':  self.t_ign_ws.toggled},
            'log':      {'autoscroll':         self.t_autoscroll.toggled},
            'view':     {'default_mode':       self.c_view_mode.value},
        }
        if save_config(cfg):
            Gitkcli.log.success('Preferences saved')

    def on_cancel(self):
        self.hide()

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_EXIT:
            self.on_cancel()
            return True
        return super().handle_input(keyboard)

class NewRefDialogPopup(UserInputDialogPopup):
    def __init__(self):
        self.force = ToggleSegment("<Force>")
        self.commit_id = ''
        self.ref_type = '' # branch or tag
        self.prompt = TextSegment("Specify the new branch name:")
        super().__init__(ID_NEW_GIT_REF, ' New Branch',
            SegmentedListItem([self.prompt, FillerSegment(), TextSegment("Flags:"), self.force]))

    def create_ref(self, commit_id, ref_type='branch'):
        self.commit_id = commit_id
        self.ref_type = ref_type
        self.header_item.set_text(f' New {ref_type.capitalize()}')
        self.prompt.set_text(f"Specify the new {ref_type} name:")
        self.clear()
        self.show()

    def clear(self):
        self.force.toggled = False
        super().clear()

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_F1:
            self.force.toggle()
        else:
            return super().handle_input(keyboard)
        return True

    def execute(self):
        self._create_ref(self.ref_type, self.input.txt, self.commit_id, self.force.toggled)
        super().execute()

    def _create_ref(self, ref_type, name, commit_id, force):
        args = ['git', ref_type] + (['-f'] if force else []) + [name, commit_id]
        Gitkcli.run_git(args, ok=f'{ref_type} {name} created successfully',
                        err=f'Error creating {ref_type}', reload_refs=True,
                        force=force, reasons=('already exists',),
                        retry=lambda: self._create_ref(ref_type, name, commit_id, True),
                        title=f' {ref_type.capitalize()} already exists',
                        lines=[(f"A {ref_type} named '{name}' already exists.", 4),
                               f"Overwrite it? (uses git {ref_type} --force)"],
                        label='[Overwrite]')

class SearchDialogPopup(UserInputDialogPopup):
    def __init__(self, id:str, width = 60):
        self.parent_list_view:ListView
        self.case_sensitive = ToggleSegment("<Case>", True)
        self.use_regexp = ToggleSegment("<Regexp>")
        # Single leading filler right-aligns the "Flags:" group against the
        # right edge (subclasses prepend a left-aligned "Type:" group).
        self.header = SegmentedListItem([FillerSegment(), TextSegment("Flags:"), self.case_sensitive, self.use_regexp])
        buttons = SegmentedListItem([FillerSegment(),
                                     ButtonSegment("[Search Next]", lambda: self.do_search(backward = False)),
                                     ButtonSegment("[Search Previous]", lambda: self.do_search(backward = True)),
                                     ButtonSegment("[Clear]", self.clear_input),
                                     FillerSegment()])
        buttons.is_selectable = False
        super().__init__(id, ' Search', self.header, buttons, width = width)

    def clear_input(self):
        self.clear()
        self.dirty = True
        self.parent_list_view.dirty = True

    def do_search(self, backward:bool):
        self.parent_list_view.search(backward)
        self.dirty = True
        super().execute()

    def matches(self, item):
        if not self.input.txt:
            return False
        text = item.get_text()
        if self.use_regexp.toggled:
            return re.search(self.input.txt, text, 0 if self.case_sensitive.toggled else re.IGNORECASE)
        if self.case_sensitive.toggled:
            return self.input.txt in text
        return self.input.txt.lower() in text.lower()

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_DC or key == curses.KEY_BACKSPACE or key == 127 or 32 <= key <= 126:
            self.parent_list_view.dirty = True

        if key == curses.KEY_F1:
            self.case_sensitive.toggle()
        elif key == curses.KEY_F2:
            self.use_regexp.toggle()
        else:
            return super().handle_input(keyboard)
        return True

    def execute(self):
        self.parent_list_view.search(repeat = True)
        super().execute()

class GitSearchDialogPopup(SearchDialogPopup):
    _TYPES = [('txt', '[Txt]'), ('id', '[ID]'), ('message', '[Message]'),
              ('path', '[Filepaths]'), ('diff', '[Diff]')]

    def __init__(self):
        # Wider than the plain search popup: the "Type:" group plus the right-
        # aligned "Flags:" group don't fit in the default width.
        super().__init__(ID_GIT_LOG_SEARCH, width = 76)
        self._type_segments = [(t, ToggleSegment(label, callback=lambda val, t=t: self.change_search_type(t)))
                               for t, label in self._TYPES]
        self.header.segments[0:0] = [TextSegment("Type:")] + [s for _, s in self._type_segments]
        self.change_search_type('txt')

    def change_search_type(self, new_type):
        self.search_type = new_type
        for t, seg in self._type_segments:
            seg.toggled = (t == new_type)
        self.use_regexp.enabled = self.case_sensitive.enabled = new_type != 'path'

    def matches(self, item):
        if self.search_type == "txt":
            return super().matches(item)
        elif hasattr(item, 'id'):
            return item.id in Gitkcli.git_log.job_git_search.found_ids
        return False

    def handle_input(self, keyboard):
        key = keyboard.key
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            if self.search_type == "txt":
                return super().handle_input(keyboard)

            self.hide()

            args = []
            if not self.case_sensitive.toggled:
                args.append('-i')
            if self.search_type == "message":
                if not self.use_regexp.toggled:
                    args.append('-F')
                args.append("--grep")
                args.append(self.input.txt)
            elif self.search_type == "id":
                args.append(f"{self.input.txt}^!")
            elif self.search_type == "diff":
                if self.use_regexp.toggled:
                    args.append("-G")
                else:
                    args.append("-S")
                args.append(self.input.txt)
            elif self.search_type == "path":
                args.append('--')
                args.append(f"*{self.input.txt}*")

            self.add_query_to_history()
            Gitkcli.git_log.job_git_search.start_job(args)

        elif key == KEY_TAB: # cycle through search types
            self.parent_list_view.dirty = True
            types = [t for t, _ in self._TYPES]
            self.change_search_type(types[(types.index(self.search_type) + 1) % len(types)])

        else:
            return super().handle_input(keyboard)

        return True

class Screen:

    FLASH_DURATION = 2.0  # seconds a success flash replaces the bottom bar

    @classmethod
    def _init_color(cls, pair_number: int, nfg:int, nbg:int = -1, hfg:int = -1, hbg:int = -1, sfg:int = -1, sbg:int = -1, shfg:int = -1, shbg:int = -1) -> None:
        # normal
        fg = nfg
        bg = nbg
        curses.init_pair(pair_number, fg, bg)
        # highlighted
        if hfg >= 0: fg = hfg
        if hbg >= 0: bg = hbg
        else: bg = 20
        curses.init_pair(50 + pair_number, fg, bg)
        # selected
        if sfg >= 0: fg = sfg
        if sbg >= 0: bg = sbg
        else: bg = 235
        curses.init_pair(100 + pair_number, fg, bg)
        # selected+highlighted
        if shfg >= 0: fg = shfg
        if shbg >= 0: bg = shbg
        else: bg = 21
        curses.init_pair(150 + pair_number, fg, bg)

    @classmethod
    def color(cls, number, selected = False, highlighted = False, matched = False, bold = None, dim = False):
        if matched:
            bold = True
            if number == 1:
                number = 16
            elif number == 18:
                number = 16
                dim = True
        if selected and highlighted:
            color = curses.color_pair(150 + number)
        elif selected:
            color = curses.color_pair(100 + number)
        elif highlighted:
            color = curses.color_pair(50 + number)
        else:
            color = curses.color_pair(number)
        if bold or (selected and bold is None):
            color = color | curses.A_BOLD
        if dim:
            color = color | curses.A_DIM
        return color

    def __init__(self, stdscr:curses.window):

        # Run with curses
        curses.use_default_colors()

        curses.start_color()

        Screen._init_color(1, curses.COLOR_WHITE)    # Normal text
        Screen._init_color(2, curses.COLOR_RED)      # Error text
        Screen._init_color(3, curses.COLOR_GREEN)    # Status text
        Screen._init_color(4, curses.COLOR_YELLOW)   # Git ID
        Screen._init_color(5, curses.COLOR_BLUE)     # Data
        Screen._init_color(6, curses.COLOR_GREEN)    # Author
        Screen._init_color(8, curses.COLOR_RED)      # diff -
        Screen._init_color(9, curses.COLOR_GREEN)    # diff +
        Screen._init_color(10, curses.COLOR_CYAN)    # diff ranges
        Screen._init_color(11, curses.COLOR_GREEN)   # local ref
        Screen._init_color(12, curses.COLOR_YELLOW)  # tag
        Screen._init_color(13, curses.COLOR_BLUE)    # head
        Screen._init_color(14, curses.COLOR_CYAN)    # stash
        Screen._init_color(15, curses.COLOR_RED)     # remote ref
        Screen._init_color(16, curses.COLOR_YELLOW) # search match
        Screen._init_color(17, curses.COLOR_BLUE)    # diff info lines
        Screen._init_color(18, 245)                  # debug text

        Screen._init_color(30,
                   curses.COLOR_BLACK, 245, -1, 247,              # Inactive window title
                   curses.COLOR_WHITE, curses.COLOR_BLUE, -1, 20) # Active window title

        Screen._init_color(31,
                   curses.COLOR_WHITE, curses.COLOR_RED, -1, curses.COLOR_RED,    # Warning title bar
                   curses.COLOR_WHITE, curses.COLOR_RED)                          # (white on red)

        curses.init_pair(204, curses.COLOR_BLACK, curses.COLOR_CYAN)  # Bottom-bar label block (Midnight Commander style)
        curses.init_pair(201, curses.COLOR_BLACK, curses.COLOR_GREEN) # Success flash over the bottom bar

        curses.curs_set(0)  # Hide cursor
        stdscr.timeout(5)
        if hasattr(curses, 'set_escdelay'):
            curses.set_escdelay(20)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)

        self.stdscr = stdscr
        self.showed_views = []
        self.views = {}
        # Set on terminal resize: every window changed, so clear the background and
        # re-touch every panel for a full recomposition next frame.
        self._full_redraw = False

        # Midnight-Commander-style function-key panel pinned to the bottom row.
        # Each entry is (key label, name, callback); callbacks reference Gitkcli
        # views lazily, so they are safe to define before those views exist.
        self.bottom_bar_entries = [
            ('F1',  'Git Log',  lambda: Gitkcli.git_log.show()),
            ('F2',  'Git Refs', lambda: Gitkcli.git_refs.show()),
            ('F3',  'Git Diff', lambda: Gitkcli.git_diff.show()),
            ('F4',  'Logs',     lambda: Gitkcli.log.view.show()),
            ('F5',  'Refresh',  lambda: Gitkcli.refresh_all()),
            ('F6',  'Search',   lambda: Gitkcli.open_search()),
            ('F7',  'Context',  lambda: Gitkcli.open_context_menu(at_selection=False)),
            ('F9',  'Config',   lambda: Gitkcli.preferences.show()),
            ('F10', 'Quit',     lambda: Gitkcli.exit_program()),
        ]
        # Same bindings driven from the keyboard (single source of truth with the
        # bar). F7 is special-cased in the main loop - from the keyboard it opens
        # at the selected row, so it never reaches this table.
        self.fkey_actions = {getattr(curses, 'KEY_' + label): cb
                             for label, name, cb in self.bottom_bar_entries}
        # Filled by draw_bottom_bar each frame: (x_start, x_end, callback) ranges
        # used to route clicks on the bottom row to the right entry.
        self.bar_hitmap = []

        # Success "flash": for FLASH_DURATION seconds after a command succeeds the
        # whole bottom bar is replaced by this message on a green background, then
        # reverts to the function-key panel. Empty when no flash is showing.
        self.flash_message = ''
        self.flash_time = 0.0

        stdscr.clear()
        stdscr.refresh()

    def getmaxyx(self) -> tuple[int, int]:
        y, x = self.stdscr.getmaxyx()
        return y-1, x # substrack status bar

    def add_view(self, id, view):
        self.views[id] = view

    def get_active_view(self) -> typing.Any:
        if len(self.showed_views) > 0:
            return self.showed_views[-1]
        return None

    def _restack(self):
        """Re-assert the panel deck order to match showed_views (bottom -> top),
        after an op (e.g. resizing a non-active window) perturbed it."""
        for view in self.showed_views:
            view.panel.top()

    def hide_active_view(self):
        if len(self.showed_views) > 0:
            # Closing a split pane (the [X] button) leaves split view and brings
            # the *other* pane up fullscreen, instead of popping a single pane
            # and leaving a gap with no backdrop.
            closing = self.showed_views[-1]
            if Gitkcli.split_active() and closing in (Gitkcli.git_log, Gitkcli.git_diff):
                other = Gitkcli.git_log if closing is Gitkcli.git_diff else Gitkcli.git_diff
                Gitkcli.set_split_mode('off')
                other.show()
                return
            # Same as closing any top view: blank its footprint (damage-based
            # redraw repaints what was underneath) and restyle the new top view.
            closing.hide()

    def is_view_visible(self, view) -> bool:
        """True if `view` is on screen and not fully hidden by a fullscreen view
        stacked above it. The panel deck handles pixel-level occlusion; this only
        decides whether keeping a window's content live is worth the work."""
        views = self.showed_views
        if view not in views:
            return False
        return not any(v.view_mode == 'fullscreen' for v in views[views.index(view) + 1:])

    def show_flash(self, message:str):
        """Replace the bottom bar with `message` on green for FLASH_DURATION."""
        self.flash_message = message.splitlines()[0] if message else ''
        self.flash_time = time.time()

    def flash_active(self) -> bool:
        """True while a flash should keep the main loop redrawing the bottom bar
        (either still showing, or set-but-expired and awaiting one clearing draw)."""
        return bool(self.flash_message)

    def draw_bottom_bar(self, stdscr):
        """Draw the global function-key panel on the bottom row and rebuild the
        click hit-map. Midnight-Commander style: a 2-wide key number followed by
        the label on a cyan block, with the entries spread evenly across the full
        width (e.g. `` 1Log        2Refs       …``). Written to stdscr (the bottom
        of the panel deck); composited under the panels by update_panels().

        While a success flash is active the whole row is drawn green instead."""
        lines, cols = stdscr.getmaxyx()
        if cols < 2 or lines < 1:
            return
        y = lines - 1

        if self.flash_message:
            if time.time() - self.flash_time < self.FLASH_DURATION:
                # cols - 1: the bottom-right cell raises addwstr() ERR (see below).
                stdscr.addstr(y, 0, self.flash_message[:cols - 1].ljust(cols - 1),
                              curses.color_pair(201))
                self.bar_hitmap = []  # the F-key cells are hidden, so swallow clicks
                return
            self.flash_message = ''  # expired: fall through and redraw the F-key bar

        num_attr = Screen.color(1)             # key number: light text on default bg
        label_attr = curses.color_pair(204)    # label: black on cyan
        # cols - 1: writing the bottom-right cell advances the cursor off-screen
        # and raises addwstr() ERR.
        stdscr.addstr(y, 0, ' ' * (cols - 1), num_attr)

        # Spread the entries evenly over the whole width: equal cells, with the
        # remainder handed to the leftmost cells. Each cell is a 2-wide key
        # number then the label padded out on cyan to fill the cell.
        self.bar_hitmap = []
        entries = self.bottom_bar_entries
        total = cols - 1
        n = len(entries)
        x = 0
        for i, (key, name, callback) in enumerate(entries):
            cell_w = total // n + (1 if i < total % n else 0)
            if cell_w < 2:
                break
            num = (key[1:] if key.startswith('F') else key).rjust(2)  # ' 1', '10'
            label = name[:cell_w - len(num)].ljust(cell_w - len(num))
            stdscr.addstr(y, x, num, num_attr)
            if label:
                stdscr.addstr(y, x + len(num), label, label_attr)
            self.bar_hitmap.append((x, x + cell_w, callback))
            x += cell_w

    def draw_visible_views(self):
        # Refresh only the content of windows whose content changed; the panel
        # deck handles occlusion and uncovered regions. On a full redraw (terminal
        # resize) clear the background and re-touch every panel so the whole stack
        # is recomposed. Then push the background (with the bottom bar), composite
        # the panels over it, and flush - all in a single doupdate().
        force = self._full_redraw
        if force:
            self._full_redraw = False
            self.stdscr.clear()

        for view in self.showed_views:
            view.redraw(force)

        self.draw_bottom_bar(self.stdscr)
        self.stdscr.noutrefresh()
        curses.panel.update_panels()
        curses.doupdate()

class Log:
    def __init__(self):
        self.view = LogView()
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
            screen = getattr(Gitkcli, 'screen', None)
            if screen is not None:
                screen.show_flash(txt)

    def warning(self, txt):
        if self.level > 1: self.log(12, txt)

    def error(self, txt):
        if self.level > 0:
            self.log(2, txt)
            # Surface errors as a modal red dialog (the status bar is gone).
            # Guarded: errors can fire during start-up before the dialog exists.
            dialog = getattr(Gitkcli, 'error_dialog', None)
            if dialog is not None:
                dialog.show_error(txt)

    def log(self, color, txt):
        now = datetime.datetime.now()
        for line in txt.splitlines():
            self.view.append(TextListItem(f'{now} {line}', color))

@dataclasses.dataclass
class KeyboardState:
    """Whole keyboard input state passed to handle_input()."""
    key: int = -1                                   # normalized key code
    sequence: list = dataclasses.field(default_factory=list)  # raw escape bytes

    def read(self, stdscr) -> bool:
        """Read and normalize a key from curses. Returns False when nothing
        usable was read (timeout or an unrecognized escape sequence)."""
        key = stdscr.getch()
        if key < 0:
            return False

        # parse escape sequences
        if key == 27: # Esc key
            sequence = []
            while key >= 0:
                if key == 27: sequence.clear()
                sequence.append(key)
                key = stdscr.getch()
            Gitkcli.log.debug('Escape sequence: ' + str(sequence))
            self.sequence = sequence
            if len(sequence) == 1:
                key = curses.KEY_EXIT
            elif sequence == [27, 91, 49, 53, 59, 50, 126]:
                key = KEY_SHIFT_F5
            elif sequence == [27, 91, 49, 59, 53, 68]:
                key = KEY_CTRL_LEFT
            elif sequence == [27, 91, 49, 59, 53, 67]:
                key = KEY_CTRL_RIGHT
            elif sequence == [27, 91, 51, 59, 53, 126]:
                key = KEY_CTRL_DEL
            else:
                return False
        else:
            Gitkcli.log.debug('Key: ' + str(key))
            self.sequence = [key]

        # Ctrl+Backspace arrives as ^H (8) on most terminals; plain
        # Backspace arrives as KEY_BACKSPACE / 127
        if key == 8:
            key = KEY_CTRL_BACKSPACE

        self.key = key
        return True

@dataclasses.dataclass
class MouseState:
    """Whole mouse input state passed to handle_mouse_input()."""
    event_type: str = ''        # current event ('left-click', 'double-click', ...)
    state: int = 0              # raw curses bstate
    screen_x: int = 0           # absolute screen position (persistent)
    screen_y: int = 0
    x: int = 0                  # coordinates relative to the handler being invoked
    y: int = 0
    rel_x: int = 0              # delta since previous event (used for resize)
    rel_y: int = 0
    click_x: int = 0            # position/time of last left-press (double-click detection)
    click_y: int = 0
    click_time: float = dataclasses.field(default_factory=time.time)
    left_pressed: bool = False
    right_pressed: bool = False
    movement_capture: set = dataclasses.field(default_factory=set)
    clicked_view: 'View|None' = None    # drag targets that captured a press
    clicked_item: 'Item|None' = None

    def capture_mouse_movement(self, enable:bool, id = None):
        enabled = len(self.movement_capture) > 0
        if enable:
            self.movement_capture.add(id)
            if not enabled:
                print("\033[?1003h", end='', flush=True) # start capturing mouse movement
        elif id in self.movement_capture:
            self.movement_capture.remove(id)
            if enabled and len(self.movement_capture) == 0:
                print("\033[?1000h", end='', flush=True) # end capturing mouse movement

    def read_curses_event(self, stdscr) -> bool:
        """Decode a curses mouse event into this state. Returns False when the
        event should be ignored (a release with no matching press, or an
        unrecognized button state)."""
        _, screen_x, screen_y, _, self.state = curses.getmouse()
        self.rel_x = screen_x - self.screen_x
        self.rel_y = screen_y - self.screen_y
        self.screen_x = screen_x
        self.screen_y = screen_y
        Gitkcli.log.debug('Mouse state: ' + str(self.state))

        self.event_type = ''
        if self.state == curses.BUTTON1_PRESSED:
            now = time.time()
            self.left_pressed = True
            if now - self.click_time < 0.3 and self.screen_x == self.click_x and self.screen_y == self.click_y:
                self.event_type = 'double-click'
            else:
                self.click_time = now
                self.event_type = 'left-click'
            self.click_x = self.screen_x
            self.click_y = self.screen_y

        elif self.state == curses.BUTTON1_RELEASED:
            if not self.left_pressed:
                return False
            self.left_pressed = False
            self.event_type = 'left-release'

        elif self.state == curses.BUTTON3_PRESSED:
            self.right_pressed = True
            self.event_type = 'right-click'

        elif self.state == curses.BUTTON3_RELEASED:
            if not self.right_pressed:
                return False
            self.right_pressed = False
            self.event_type = "right-release"

        elif self.state == curses.REPORT_MOUSE_POSITION:
            if self.left_pressed:
                self.event_type = 'left-move'
            elif self.right_pressed:
                self.event_type = 'right-move'
            else:
                self.event_type = 'move'

        elif self.state & curses.BUTTON1_DOUBLE_CLICKED:
            self.event_type = 'double-click'

        elif self.state == curses.BUTTON4_PRESSED:
            self.event_type = 'wheel-up'

        elif self.state == curses.BUTTON5_PRESSED:
            self.event_type = 'wheel-down'

        return self.event_type != ''

    def process_mouse_event(self, active_view:View, event_type:str = None):
        if event_type is None:
            event_type = self.event_type

        if 'click' in event_type:
            self.capture_mouse_movement(True)
        if 'release' in event_type:
            self.capture_mouse_movement(False)

        if self.clicked_item:
            if self.click_y == self.screen_y:
                if event_type == 'left-move':
                    event_type = 'left-move-in'
            elif event_type == 'left-move':
                event_type = 'left-move-out'
            elif event_type == 'left-release':
                event_type = 'left-release-out'

        # expose the (possibly adjusted) type to handlers reading mouse.event_type
        self.event_type = event_type

        # The function-key bar lives on the reserved bottom row, outside every
        # view's rect. Route a single click there to its entries — but not while
        # a modal popup is open: let those fall through so an outside-click
        # dismisses it. (Only 'left-click', never 'double-click': a bar entry has
        # no double-click meaning, and firing twice would e.g. open then instantly
        # close the menu/preferences popup an entry just raised.)
        if event_type == 'left-click':
            bar_y = Gitkcli.screen.stdscr.getmaxyx()[0] - 1
            active = Gitkcli.screen.get_active_view()
            if self.screen_y == bar_y and not (active and active.is_popup):
                for x_start, x_end, callback in Gitkcli.screen.bar_hitmap:
                    if x_start <= self.screen_x < x_end:
                        callback()
                        break
                return

        enclosed_view = None
        for view in reversed(Gitkcli.screen.showed_views):
            if view.is_popup or view.win.enclose(self.screen_y, self.screen_x):
                enclosed_view = view
                break

        if enclosed_view and event_type == 'left-click':
            self.clicked_view = enclosed_view
            if enclosed_view and enclosed_view != active_view:
                enclosed_view.show()
                enclosed_view.dirty = True
                active_view.dirty = True

        send_event_to = None
        view_to_process = enclosed_view
        item_x = 0
        item_y = 0
        if 'move' in event_type or 'release' in event_type:
            if self.clicked_view:
                view_to_process = self.clicked_view
            if self.clicked_item:
                send_event_to = self.clicked_item
                if self.clicked_view:
                    item_x = self.clicked_view.x
                    item_y = self.clicked_view.y

        if not send_event_to:
            send_event_to = view_to_process

        if view_to_process and send_event_to:
            begin_y, begin_x = view_to_process.win.getbegyx()
            self.x = (self.screen_x - begin_x) - item_x
            self.y = (self.screen_y - begin_y) - item_y
            if send_event_to.handle_mouse_input(self):
                view_to_process.dirty = True

        if 'release' in event_type:
            self.clicked_view = None
            self.clicked_item = None

class Gitkcli:
    running = True
    screen:Screen
    mouse:MouseState
    keyboard:KeyboardState
    log:Log
    git_log:GitLogView
    git_diff:GitDiffView
    git_refs:GitRefsView
    context_menu:ContextMenu
    preferences:"PreferencesDialogPopup"
    confirm_dialog:"ConfirmDialogPopup"
    error_dialog:"ErrorDialogPopup"

    # Split view tiles the git-log and git-diff panes side by side.
    #   'off'     - normal single-view behaviour
    #   'side'    - git-log left, git-diff right
    #   'stacked' - git-log top, git-diff bottom
    split_mode = 'off'
    split_ratio = 0.5             # fraction of the screen given to the git-log pane
    _raising_split_sibling = False

    # Layout the app opens in: 'fullscreen' (single view), 'side' or 'stacked'.
    default_view_mode = 'fullscreen'

    @classmethod
    def run_git(cls, args, ok=None, err='Error', refresh_head=False, reload_refs=False,
                check_uncommitted=False, force=False, reasons=(), retry=None,
                title='', lines=(), label='[Yes]'):
        """Run a git command and react to the result. On success: run the
        requested refreshes and log `ok`. On a forceable rejection (`retry` set,
        not already forcing, and a `reasons` substring in stderr): pop a confirm
        dialog. Otherwise log `err` + stderr. Returns the CompletedProcess."""
        result = Job.run_job(args)
        if result.returncode == 0:
            if refresh_head: cls.git_log.refresh_head()
            if reload_refs: cls.git_refs.reload_refs()
            if check_uncommitted: cls.git_log.check_uncommitted_changes()
            if ok: cls.log.success(ok)
        elif retry and not force and any(r in result.stderr for r in reasons):
            cls.confirm_dialog.confirm(title, list(lines), retry, confirm_label=label)
        else:
            cls.log.error(f"{err}: {result.stderr}")
        return result

    @classmethod
    def refresh_all(cls):
        """Refresh new commits on HEAD and reload refs (the F5 action)."""
        cls.git_log.refresh_head()
        cls.git_refs.reload_refs()

    @classmethod
    def open_search(cls):
        """Open the active view's search dialog (the F6 / '/' action)."""
        view = cls.screen.get_active_view()
        if view:
            view.handle_input(KeyboardState(ord('/')))

    @classmethod
    def open_context_menu(cls, at_selection=True):
        """Open the context menu for the active view's selected item.
        at_selection=True (the F7 *key*) opens it at the selected row, since the
        keyboard has no cursor; at_selection=False (a mouse click on the F7 bar
        button) leaves it at the current mouse position."""
        view = cls.screen.get_active_view()
        if not view or not hasattr(view, 'get_selected'):
            return
        item = view.get_selected()
        if item is None:
            return
        if at_selection:
            win_y, win_x = view.win.getbegyx()
            cls.mouse.screen_x = win_x + view.x
            cls.mouse.screen_y = win_y + view.y + (view._selected - view._offset_y)
        cls.context_menu.show_context_menu(item)

    @classmethod
    def reload_refs_commits(cls):
        cls.git_refs.reload_refs()
        cls.git_log.reload_commits()

    @classmethod
    def exit_program(cls):
        cls.running = False
        for job in Job.jobs.values():
            job.stop_job()

    @classmethod
    def split_active(cls):
        """True only when the split is currently shown as two tiled panes.

        `split_mode` is the user's intent; on a terminal too small to tile, the
        panes fall back to fullscreen (view_mode != 'window'). Behaviours that
        only make sense with a visible split (Esc/q stepping, divider drag,
        pane focus pairing) key off this, not off `split_mode` alone.
        """
        return cls.split_mode != 'off' and cls.git_log.view_mode == 'window'

    @classmethod
    def cycle_split_view(cls):
        cls.set_split_mode({'off': 'side', 'side': 'stacked', 'stacked': 'off'}[cls.split_mode])
        return True

    @classmethod
    def set_split_mode(cls, mode):
        cls.split_mode = mode
        cls.apply_split_layout()
        if mode != 'off':
            # Seed the diff pane from the current selection if it has no content yet.
            if not cls.git_diff.items:
                item = cls.git_log.get_selected()
                if item and hasattr(item, 'load_to_view'):
                    item.load_to_view()
            cls.git_log.show()   # focus the log pane (raises the diff pane with it)

    @classmethod
    def apply_split_layout(cls):
        """Position the git-log/git-diff panes for the current split mode."""
        lines, cols = cls.screen.getmaxyx()
        min_w, min_h = 12, 4
        # Both axes must clear their minimum, otherwise a pane would be tiled
        # into a degenerate (<=0 content) window.
        fits = ((cls.split_mode == 'side' and cols >= 2 * min_w and lines >= min_h) or
                (cls.split_mode == 'stacked' and lines >= 2 * min_h and cols >= min_w))
        if cls.split_mode != 'off' and fits:
            if cls.split_mode == 'side':
                log_w = max(min_w, min(cols - min_w, int(round(cols * cls.split_ratio))))
                cls.git_log.set_tiled(0, 0, lines, log_w)
                cls.git_diff.set_tiled(log_w, 0, lines, cols - log_w)
            else:
                log_h = max(min_h, min(lines - min_h, int(round(lines * cls.split_ratio))))
                cls.git_log.set_tiled(0, 0, log_h, cols)
                cls.git_diff.set_tiled(0, log_h, lines - log_h, cols)
        else:
            # split off, or terminal too small to tile: both panes go fullscreen.
            # Clear the tiled geometry so a later toggle_window_mode floats a
            # centered window again instead of reusing the last pane rect.
            for v in (cls.git_log, cls.git_diff):
                v.fixed_x = v.fixed_y = v.fixed_width = v.fixed_height = None
                v.set_fullscreen()
                v.dirty = True

def launch_curses(stdscr, git_args:typing.List, cmd_args:typing.List):

    Gitkcli.screen = Screen(stdscr)
    Gitkcli.mouse = MouseState()
    Gitkcli.keyboard = KeyboardState()
    Gitkcli.log = Log()
    Gitkcli.git_log = GitLogView(git_args, cmd_args)
    Gitkcli.git_diff = GitDiffView()
    Gitkcli.git_refs = GitRefsView()
    Gitkcli.context_menu = ContextMenu()
    Gitkcli.preferences = PreferencesDialogPopup()
    Gitkcli.confirm_dialog = ConfirmDialogPopup()
    Gitkcli.error_dialog = ErrorDialogPopup()

    _cfg = load_config()
    Gitkcli.git_log.show_commit_id     = _cfg['git_log']['show_commit_id']
    Gitkcli.git_log.show_commit_date   = _cfg['git_log']['show_commit_date']
    Gitkcli.git_log.show_commit_author = _cfg['git_log']['show_commit_author']
    Gitkcli.git_log.set_pref_flags(_cfg['git_log']['flags'])
    Gitkcli.git_diff.ignore_whitespace = _cfg['git_diff']['ignore_whitespace']
    Gitkcli.log.view.autoscroll        = _cfg['log']['autoscroll']
    Gitkcli.default_view_mode          = _cfg['view']['default_mode']

    Gitkcli.log.info('Application started')

    Gitkcli.git_refs.job.start_job()
    Gitkcli.git_log.job.start_job()
    Gitkcli.git_log.check_uncommitted_changes()

    if Gitkcli.default_view_mode in ('side', 'stacked'):
        Gitkcli.set_split_mode(Gitkcli.default_view_mode)
    else:
        Gitkcli.git_log.show()

    try:
        user_input = True

        while Gitkcli.running:

            update_jobs = Job.process_all_jobs()

            # A success flash auto-expires on a timer, so keep redrawing the bottom
            # bar while one is showing even if nothing else changed.
            flash = Gitkcli.screen.flash_active()

            if update_jobs or user_input or flash:
                try:
                    # Draws dirty content, then composites the panel deck and the
                    # bottom bar in one doupdate().
                    Gitkcli.screen.draw_visible_views()
                except curses.error as e:
                    Gitkcli.log.warning(f"Curses exception: {str(e)}\n{traceback.format_exc()}")

            active_view = Gitkcli.screen.get_active_view()
            if not active_view:
                break;

            stdscr.timeout(5 if update_jobs or flash else 100)

            user_input = Gitkcli.keyboard.read(stdscr)
            if not user_input:
                # no key pressed (or an unrecognized escape sequence)
                continue

            key = Gitkcli.keyboard.key

            if key == curses.KEY_MOUSE:
                if not Gitkcli.mouse.read_curses_event(stdscr):
                    continue

                event_type = Gitkcli.mouse.event_type

                if event_type == 'right-click' and Gitkcli.mouse.left_pressed:
                    Gitkcli.mouse.left_pressed = False
                    Gitkcli.mouse.process_mouse_event(active_view, 'right-release')

                if (event_type == 'left-click' or event_type == 'double-click') and Gitkcli.mouse.right_pressed:
                    Gitkcli.mouse.right_pressed = False
                    Gitkcli.mouse.process_mouse_event(active_view, 'left-release')

                Gitkcli.mouse.process_mouse_event(active_view, event_type)

            elif key == curses.KEY_RESIZE:
                Gitkcli.screen._full_redraw = True
                lines, cols = Gitkcli.screen.getmaxyx()
                for view in Gitkcli.screen.views.values():
                    view.screen_size_changed(lines, cols)
                if Gitkcli.split_mode != 'off':
                    Gitkcli.apply_split_layout()

            elif active_view.handle_input(Gitkcli.keyboard):
                active_view.dirty = True

            else:
                if key == ord('q') or key == curses.KEY_EXIT:
                    Gitkcli.screen.hide_active_view()
                elif key == KEY_CTRL_LEFT or key == KEY_CTRL('o'):
                    Gitkcli.git_log.move_in_jump_list(+1)
                elif key == KEY_CTRL_RIGHT or key == KEY_CTRL('i'):
                    Gitkcli.git_log.move_in_jump_list(-1)
                elif key == ord('|'):
                    Gitkcli.cycle_split_view()
                elif key == KEY_CTRL('w') and Gitkcli.split_active():
                    # toggle focus between the two split panes
                    (Gitkcli.git_diff if Gitkcli.git_log.is_active() else Gitkcli.git_log).show()
                elif key == KEY_SHIFT_F5:
                    Gitkcli.reload_refs_commits()
                elif key == curses.KEY_F7:
                    # From the keyboard, open at the selected row (no mouse cursor).
                    Gitkcli.open_context_menu()
                elif key in Gitkcli.screen.fkey_actions:
                    Gitkcli.screen.fkey_actions[key]()

    except KeyboardInterrupt:
        pass

    Gitkcli.exit_program()

    Gitkcli.log.info('Application ended')

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
        if arg == '--graph':
            git_args.append(arg)
        else:
            cmd_args.append(arg)

    curses.wrapper(lambda stdscr: launch_curses(stdscr, git_args, cmd_args))

if __name__ == "__main__":
    main()
