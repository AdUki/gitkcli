#!/usr/bin/python

import curses
import datetime
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import typing

# Import UI library
from ui import (
    UIContext,
    Item, SeparatorItem, SpacerListItem,
    TextListItem, UserInputListItem,
    Segment, FillerSegment, TextSegment, ButtonSegment, ToggleSegment,
    SegmentedListItem, WindowTopBarItem,
    View, ListView,
    Screen, Mouse
)


HORIZONTAL_OFFSET_JUMP = 1

KEY_SHIFT_F5 = -100
KEY_CTRL_LEFT = -101
KEY_CTRL_RIGHT = -102
KEY_ENTER = 10
KEY_RETURN = 13
KEY_TAB = 9

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
ID_BRANCH_RENAME = 'git-branch-rename'
ID_GIT_REF_PUSH = 'git-ref-push'
ID_CONTEXT_MENU = 'context-menu'
ID_GIT_REFRESH_HEAD = 'git-refresh-head'
ID_GIT_SEARCH = 'git-search'

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
        return subprocess.run(args, capture_output=True, text=True)

    @classmethod
    def process_all_jobs(cls) -> bool:
        update = False
        for job in cls.jobs.values():
            processed = job.process_items()
            if processed or job.running:
                update = True
        return update

    @classmethod
    def get_job(cls) -> typing.Optional["Job"]:
        if len(Gitkcli.screen.showed_views) > 0:
            id = Gitkcli.screen.showed_views[-1].id
            if id in cls.jobs:
                return cls.jobs[id]
        return None

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

    def process_items(self) -> bool:
        processed = False
        try:
            while True:
                item = self.items.get_nowait()
                self.items.task_done()
                if not item:
                    return processed;
                if not self.stop:
                    self.process_item(item)
                    processed = True
        except queue.Empty:
            pass
        try:
            while True:
                message = self.messages.get_nowait()
                self.messages.task_done()
                if not message:
                    return processed
                if not self.stop:
                    self.process_message(message)
                    processed = True
        except queue.Empty:
            pass
        return processed

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
                line = bytearr.decode('utf-8', errors='replace').replace('\t', ' ' * curses.get_tabsize())[:-1]
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
            Gitkcli.git_log.append(TextListItem(item, selectable=False, ui_context=Gitkcli.ui_context))
        else:
            id, commit = item
            if Gitkcli.git_log.add_commit(id, commit):
                Gitkcli.git_log.append(CommitListItem(id, ui_context=Gitkcli.ui_context))

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
            Gitkcli.git_log.prepend_commit(CommitListItem(id, ui_context=Gitkcli.ui_context))

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
            args = ['cat-file', '-p', self.tag_id]
            return args

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

    def show_diff(self, old_commit_id, new_commit_id = None, cached = False, title = None):
        self.commit_id = None
        self.tag_id = None
        self.cached = cached
        self.old_commit_id = old_commit_id
        self.new_commit_id = new_commit_id
        Gitkcli.git_diff.clear()
        Gitkcli.git_diff.commit_id = old_commit_id
        Gitkcli.git_diff.is_diff = True
        if not title:
            title = f'Diff {old_commit_id[:7]} {new_commit_id[:7]}'
        Gitkcli.git_diff.header_item.set_title(title)
        self.start_job(self._get_args())

    def show_commit(self, commit_id, on_finished = None, add_to_jump_list = True):
        self.commit_id = commit_id
        self.tag_id = None
        self.cached = False
        self.old_commit_id = None
        self.new_commit_id = None
        Gitkcli.git_diff.clear()
        Gitkcli.git_diff.commit_id = commit_id
        Gitkcli.git_diff.is_diff = False
        Gitkcli.git_diff.header_item.set_title(f'Commit {commit_id[:7]}')
        if on_finished == None and commit_id in self.selected_line_map:
            on_finished = lambda: Gitkcli.git_diff.set_selected(self.selected_line_map[commit_id])
        self.start_job(self._get_args(), on_finished = on_finished)
        if add_to_jump_list:
            Gitkcli.git_log.add_to_jump_list(commit_id)

    def show_tag_annotation(self, tag_id):
        self.tag_id = tag_id
        self.cached = False
        self.commit_id = None
        self.old_commit_id = None
        self.new_commit_id = None
        Gitkcli.git_diff.clear()
        Gitkcli.git_diff.commit_id = tag_id
        Gitkcli.git_diff.is_diff = True
        Gitkcli.git_diff.header_item.set_title(f'Tag {tag_id}')
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
                        return StatListItem(line, color, stat_match.group(1), ui_context=Gitkcli.ui_context)
                    return TextListItem(line, color, ui_context=Gitkcli.ui_context)
                self.old_file_line += 1
                self.new_file_line += 1
                return DiffListItem(self.line_count, line, color, self.old_file_path, self.old_file_line, self.new_file_path, self.new_file_line, ui_context=Gitkcli.ui_context)
            elif match.group(2): # '+++' new file
                color = 17
                self.new_file_path = str(match.group(2))
                return TextListItem(line, color, ui_context=Gitkcli.ui_context)
            elif match.group(3): # '---' old file
                color = 17
                self.old_file_path = str(match.group(3))
                return TextListItem(line, color, ui_context=Gitkcli.ui_context)
            elif match.group(4): # infos
                color = 17
                return TextListItem(line, color, ui_context=Gitkcli.ui_context)
            elif match.group(5): # '+' added code lines
                color = 9
                self.new_file_line += 1
                return DiffListItem(self.line_count, line, color, None, None, self.new_file_path, self.new_file_line, ui_context=Gitkcli.ui_context)
            elif match.group(6): # '-' remove code lines
                color = 8
                self.old_file_line += 1
                return DiffListItem(self.line_count, line, color, self.old_file_path, self.old_file_line, None, None, ui_context=Gitkcli.ui_context)
            elif match.group(7): # diff numbers
                color = 10
                self.old_file_line = int(match.group(8)) - 1
                self.new_file_line = int(match.group(9)) - 1
                return DiffListItem(self.line_count, line, color, self.old_file_path, self.old_file_line, self.new_file_path, self.new_file_line, ui_context=Gitkcli.ui_context)

        return TextListItem(line, color, ui_context=Gitkcli.ui_context)

    def process_item(self, item):
        Gitkcli.git_diff.append(item)

class GitSearchJob(Job):
    def __init__(self, args = []):
        super().__init__(ID_GIT_SEARCH) 
        self.cmd = 'git log --format=%H'
        self.args = args
        self.found_ids = set()

    def start_job(self, args = [], on_finished = None):
        self.found_ids.clear()
        Gitkcli.git_log.dirty = True
        super().start_job(args, on_finished) 

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

        ref = {}
        ref['id'] = id
        if value == 'HEAD':
            ref['name'] = value
            ref['type'] = 'head'
        else:
            parts = value.split('/', 2)
            if len(parts) == 2:
                ref['type'] = parts[1]
                ref['name'] = parts[1]
            else:
                ref['type'] = parts[1]
                ref['name'] = parts[2]

        return ref

    def process_item(self, item):
        id = item['id']

        if item['type'] == 'tags' and item['name'].endswith('^{}'): 
            # process link to annotated tag
            last_item_data = Gitkcli.git_refs.items[-1].data
            last_item_data['tag_id'] = last_item_data['id']
            last_item_data['id'] = id
            item = last_item_data
        else:
            Gitkcli.git_refs.append(RefListItem(item, Gitkcli.ui_context))

        Gitkcli.git_refs.refs.setdefault(id,[]).append(item)
        Gitkcli.git_log.dirty = True
        if item['type'] == 'head':
            Gitkcli.git_log.head_id = id

class RefListItem(Item):
    def __init__(self, data, ui_context):
        super().__init__(ui_context)
        self.data = data

    def get_text(self):
        return self.data['name']

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.get_text()
        line = line[offset:]
        color, _ = GitRefsView.get_ref_color_and_title(self.data)
        if selected or marked:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]

        win.addstr(line, Screen.color(color, selected, marked, matched))
        win.clrtoeol()

    def jump_to_ref(self):
        if Gitkcli.git_log.select_commit(self.data['id']):
            Gitkcli.git_log.show()
        else:
            Gitkcli.log.warning(f"Commit with hash {self.data['id']} not found")

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'double-click':
            self.jump_to_ref()
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.jump_to_ref()
            return True
        else:
            return False

class StatListItem(TextListItem):
    def __init__(self, txt:str, color:int, stat_file_path:str, ui_context=None):
        self.stat_file_path = stat_file_path
        super().__init__(txt, color, ui_context=ui_context)

    def jump_to_file(self):
        Gitkcli.git_diff.set_selected(re.compile(f'diff.*{self.stat_file_path}'), 'top')

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'double-click':
            self.jump_to_file()
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.jump_to_file()
            return True
        else:
            return super().handle_input(key)

class DiffListItem(TextListItem):
    def __init__(self, line:int, txt:str, color:int,
                 old_file_path:typing.Optional[str] = None, old_file_line:typing.Optional[int] = None,
                 new_file_path:typing.Optional[str] = None, new_file_line:typing.Optional[int] = None,
                 ui_context=None):
        self.line = line
        self.old_file_line = old_file_line
        self.old_file_path = old_file_path
        self.new_file_line = new_file_line
        self.new_file_path = new_file_path
        super().__init__(txt, color, ui_context=ui_context)

    def jump_to_origin(self):
        if self.old_file_path and self.old_file_line:
            args = ['git', 'blame', '-lsfn', '-L',
                    f'{self.old_file_line},{self.old_file_line}',
                    f'{Gitkcli.git_diff.commit_id}^', # get parent commit-id
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
                        Gitkcli.git_diff.job.show_commit(commit.id, on_finished = lambda: Gitkcli.git_diff.select_line(file_path, file_line))

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'double-click':
            self.jump_to_origin()
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.jump_to_origin()
            return True
        else:
            return super().handle_input(key)

class RefSegment(TextSegment):
    def __init__(self, ref, ui_context):
        self.ref = ref
        color, txt = GitRefsView.get_ref_color_and_title(ref)
        super().__init__(txt, color, ui_context)

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'right-click':
            return Gitkcli.context_menu.show_context_menu(RefListItem(self.ref, self._ui_context), 'git-refs')
        elif event_type == 'double-click' and 'tag_id' in self.ref:
            Gitkcli.git_diff.job.show_tag_annotation(self.ref['tag_id'])
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

class UncommittedChangesListItem(TextListItem):
    def __init__(self, staged:bool = False, ui_context=None):
        self._staged = staged
        self.id = 'local-staged' if staged else 'local-working'
        if self._staged:
            super().__init__('Uncommitted changes (staged)', 3, ui_context=ui_context)
        else:
            super().__init__('Uncommitted changes (working directory)', 2, ui_context=ui_context)

    def load_to_view(self):
        Gitkcli.git_diff.job.show_diff('HEAD', cached = self._staged, title = self.txt)

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if super().handle_mouse_input(event_type, x, y):
            return True
        if event_type == 'double-click':
            self.load_to_view()
            Gitkcli.git_diff.show()
            return True
        return False

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.load_to_view()
            Gitkcli.git_diff.show()
        else:
            return False
        return True

class CommitListItem(SegmentedListItem):
    def __init__(self, id:str, ui_context=None):
        super().__init__(ui_context=ui_context)
        self.id = id

    def get_segments(self):
        commit = Gitkcli.git_log.commits[self.id]
        segments = []

        if commit['prefix']:
            segments.append(TextSegment(commit['prefix'], ui_context=self._ui_context))
        if Gitkcli.git_log.show_commit_id:
            segments.append(TextSegment(self.id[:7], 4, ui_context=self._ui_context))
        if Gitkcli.git_log.show_commit_date:
            segments.append(TextSegment(commit['date'].strftime("%Y-%m-%d %H:%M"), 5, ui_context=self._ui_context))
        if Gitkcli.git_log.show_commit_author:
            segments.append(TextSegment(commit['author'], 6, ui_context=self._ui_context))
        segments.append(TextSegment(commit['title'], ui_context=self._ui_context))

        head_position = len(segments) + 1 # +1, because we want to skip 'HEAD ->' segment
        for ref in Gitkcli.git_refs.refs.get(self.id, []):
            segments.insert(head_position if ref['name'] == Gitkcli.git_log.head_branch else len(segments), RefSegment(ref, self._ui_context))

        return segments

    def draw_line(self, win, offset, width, selected, matched, marked):
        super().draw_line(win, offset, width, selected, matched, Gitkcli.git_log.marked_commit_id == self.id)

    def load_to_view(self):
        if Gitkcli.git_diff.commit_id != self.id or Gitkcli.git_diff.is_diff:
            Gitkcli.git_diff.job.show_commit(self.id)

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if super().handle_mouse_input(event_type, x, y):
            return True
        if event_type == 'double-click':
            self.load_to_view()
            Gitkcli.git_diff.show()
            return True
        return False

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.load_to_view()
            Gitkcli.git_diff.show()
        else:
            return False
        return True

class GitLogView(ListView):
    def __init__(self, git_args:typing.List, cmd_args:typing.List, ui_context):
        super().__init__(ID_GIT_LOG, 'fullscreen', ui_context=ui_context)

        self.commits = {} # map: git_id --> { parents, date, author, title }

        self.marked_commit_id = ''
        self.jump_list = []
        self.jump_index = 0
        self.head_branch = ''
        self.head_id = ''

        self.show_commit_id = True
        self.show_commit_date = True
        self.show_commit_author = True

        self.job = GitLogJob(ID_GIT_LOG, git_args + cmd_args)
        self.job_git_refresh_head = GitRefreshHeadJob()
        self.job_git_search = GitSearchJob(cmd_args)

        repo_name = os.path.basename(Job.run_job(['git', 'rev-parse', '--show-toplevel']).stdout.strip())
        self.set_header_item(WindowTopBarItem(
            'Repository: ' + repo_name,
            additional_segments=[
                ButtonSegment("[<---]", lambda: self.move_in_jump_list(+1), 30, self._ui_context),
                ButtonSegment("[--->]", lambda: self.move_in_jump_list(-1), 30, self._ui_context)
            ],
            color=30,
            ui_context=self._ui_context,
            on_menu_click=lambda: self._ui_context.show_context_menu(Gitkcli),
            on_close_click=lambda: self._ui_context.screen.hide_active_view(),
            on_double_click=lambda: self.toggle_window_mode()
        ))

        self.set_search_dialog(GitSearchDialogPopup(self._ui_context))

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

    def set_selected(self, what:int|str|re.Pattern, visible_mode = 'center') -> bool:
        ret = super().set_selected(what, visible_mode)
        if Gitkcli.git_diff in Gitkcli.screen.get_visible_views():
            item = self.get_selected()
            if item:
                item.load_to_view()
        return ret

    def toggle_show_commit_id(self):
        self.show_commit_id = not self.show_commit_id
        self.dirty = True

    def toggle_show_commit_date(self):
        self.show_commit_date = not self.show_commit_date
        self.dirty = True

    def toggle_show_commit_author(self):
        self.show_commit_author = not self.show_commit_author
        self.dirty = True

    def check_uncommitted_changes(self):
        to_remove = 0
        for i in range(min(2, len(self.items))):
            if self.items[i].id.startswith('local'):
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
            self.prepend_commit(UncommittedChangesListItem(staged=True, ui_context=self._ui_context))

        # Check for working directory changes
        result = Job.run_job(['git', 'diff', '--quiet'])
        has_working = result.returncode != 0
        if has_working:
            self.prepend_commit(UncommittedChangesListItem(ui_context=self._ui_context))

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

    def add_to_jump_list(self, id:str):
        if len(self.jump_list) > 0 and id == self.jump_list[self.jump_index]:
            return
        self.jump_list = self.jump_list[self.jump_index:]
        if id in self.jump_list:
            self.jump_list.remove(id)
        self.jump_list.insert(0, id)
        self.jump_index = 0

    def move_in_jump_list(self, jump:int):
        if len(self.jump_list) > 0:
            new_index = self.jump_index + jump
            if 0 <= new_index < len(self.jump_list):
                self.jump_index = new_index
                if not self.select_commit(self.jump_list[new_index]):
                    # when commit id not found, go to next item
                    self.move_in_jump_list(jump)
        return True

    def get_selected_commit_id(self):
        selected_item = self.get_selected()
        if selected_item:
            return selected_item.id
        return ''

    def cherry_pick(self, commit_id = None):
        Job.run_job(['git', 'cherry-pick', '--abort'])
        commit_id = commit_id or self.get_selected_commit_id()
        result = Job.run_job(['git', 'cherry-pick', '-m', '1', commit_id])
        if result.returncode == 0:
            self.refresh_head()
            Gitkcli.git_refs.reload_refs()
            Gitkcli.log.success(f'Commit {commit_id} cherry picked successfully')
        else:
            Gitkcli.log.error(f"Error during cherry-pick: " + result.stderr)

    def revert(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        result = Job.run_job(['git', 'revert', '--no-edit', '-m', '1', commit_id])
        if result.returncode == 0:
            self.refresh_head()
            Gitkcli.git_refs.reload_refs()
            Gitkcli.log.success(f'Commit {commit_id} reverted successfully')
        else:
            Gitkcli.log.error(f"Error during revert: " + result.stderr)
    
    def reset(self, hard, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        reset_type = '--hard' if hard else '--soft'
        result = Job.run_job(['git', 'reset', reset_type, commit_id])
        if result.returncode == 0:
            Gitkcli.git_refs.reload_refs()
            Gitkcli.git_log.check_uncommitted_changes()
        else:
            Gitkcli.log.error(f"Error during {reset_type} reset:" + result.stderr)

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
            Gitkcli.log.error("Error during cleaning " +
                      "staged" if staged else "unstaged" +
                      " changes: " + result.stderr)
    
    def mark_commit(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        self.marked_commit_id = commit_id
        self.dirty = True
    
    def diff_commits(self, old_commit_id, new_commit_id):
        Gitkcli.git_diff.job.show_diff(old_commit_id, new_commit_id)
        Gitkcli.git_diff.show()

    def handle_input(self, key):
        if key == ord('q') or key == curses.KEY_EXIT:
            Gitkcli.exit_program()
        elif key == ord('b'):
            Gitkcli.git_refs.view_new_ref.create_ref(self.get_selected_commit_id())
        elif key == ord('r'):
            self.reset(False)
        elif key == ord('R'):
            self.reset(True)
        elif key == ord('c'):
            self.cherry_pick()
        elif key == ord('v'):
            self.revert()
        elif key == ord('m'):
            self.mark_commit()
        elif key == ord('M'):
            self.select_commit(self.marked_commit_id)
        else:
            return super().handle_input(key)
        return True

class ShowContextSegment(TextSegment):
    def __init__(self, color, ui_context):
        super().__init__('', color, ui_context)

    def get_text(self):
        return str(Gitkcli.git_diff.context_size)

class GitDiffView(ListView):
    def __init__(self, ui_context):
        super().__init__(ID_GIT_DIFF, 'fullscreen', ui_context=ui_context)

        self.context_size = 3
        self.rename_limit = 1570
        self.ignore_whitespace = False
        self.job = GitDiffJob()

        self.commit_id = ''
        self.is_diff = False

        self.set_header_item(WindowTopBarItem(
            'Git commit diff',
            additional_segments=[
                TextSegment("Context:", 30, self._ui_context),
                ShowContextSegment(30, self._ui_context),
                ButtonSegment("[ + ]", lambda: self.change_context(+1), 30, self._ui_context),
                ButtonSegment("[ - ]", lambda: self.change_context(-1), 30, self._ui_context),
                ButtonSegment("[<---]", lambda: Gitkcli.git_log.move_in_jump_list(+1), 30, self._ui_context),
                ButtonSegment("[--->]", lambda: Gitkcli.git_log.move_in_jump_list(-1), 30, self._ui_context)
            ],
            color=30,
            ui_context=self._ui_context,
            on_menu_click=lambda: self._ui_context.show_context_menu(Gitkcli),
            on_close_click=lambda: self._ui_context.screen.hide_active_view(),
            on_double_click=lambda: self.toggle_window_mode()
        ))

        self.set_search_dialog(SearchDialogPopup(ID_GIT_DIFF_SEARCH, self._ui_context))

    def clear(self):
        self.commit_id = ''
        self.is_diff = False
        super().clear()

    def set_selected(self, what:int|str|re.Pattern, visible_mode = 'center') -> bool:
        ret = super().set_selected(what, visible_mode)
        if self.commit_id and self.is_diff == False:
            self.job.selected_line_map[self.commit_id] = self._selected
        return ret

    def select_line(self, file:str, line:int):
        for item in self.items:
            if isinstance(item, DiffListItem) and item.new_file_path == file and item.new_file_line == line:
                self.set_selected(item.line)

    def change_context(self, size:int):
        self.context_size = max(0, self.context_size + size)
        self.clear()
        self.job.selected_line_map.clear()
        self.job.restart_job()

    def change_ignore_whitespace(self, val:typing.Optional[bool] = None):
        if val is None:
            val = not self.ignore_whitespace
        self.ignore_whitespace = val
        self.clear()

        self.job.selected_line_map.clear()
        self.job.restart_job()

    def handle_input(self, key) -> bool:
        if key == KEY_CTRL('n'):
            Gitkcli.git_log.handle_input(curses.KEY_DOWN)
        elif key == KEY_CTRL('p'):
            Gitkcli.git_log.handle_input(curses.KEY_UP)
        else:
            return super().handle_input(key)
        return True

class GitRefsView(ListView):
    def __init__(self, ui_context):
        super().__init__(ID_GIT_REFS, 'window', ui_context=ui_context)

        self.refs = {} # map: git_id --> [ { 'type':<ref-type>, 'name':<ref-name> } ]

        self.set_header_item(WindowTopBarItem(
            'Git references',
            color=30,
            ui_context=self._ui_context,
            on_menu_click=lambda: self._ui_context.show_context_menu(Gitkcli),
            on_close_click=lambda: self._ui_context.screen.hide_active_view(),
            on_double_click=lambda: self.toggle_window_mode()
        ))
        self.set_search_dialog(SearchDialogPopup(ID_GIT_REFS_SEARCH, self._ui_context))

        self.view_new_ref = NewRefDialogPopup(self._ui_context)
        self.view_branch_rename = BranchRenameDialogPopup(self._ui_context)
        self.view_ref_push = RefPushDialogPopup(self._ui_context)

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

class ShowLogLevelSegment(TextSegment):
    def __init__(self, color, ui_context):
        super().__init__('', color, ui_context)

    def get_text(self):
        return str(Gitkcli.log.level)

class LogView(ListView):
    def __init__(self, ui_context):
        super().__init__(ID_LOG, 'fullscreen', ui_context=ui_context)

        self.set_header_item(WindowTopBarItem(
            'Logs',
            additional_segments=[
                ButtonSegment("[Clear]", lambda: self.clear(), 30, self._ui_context),
                TextSegment("  Log level:", 30, self._ui_context),
                ShowLogLevelSegment(30, self._ui_context),
                ButtonSegment("[ + ]", lambda: self.change_log_level(+1), 30, self._ui_context),
                ButtonSegment("[ - ]", lambda: self.change_log_level(-1), 30, self._ui_context)
            ],
            color=30,
            ui_context=self._ui_context,
            on_menu_click=lambda: self._ui_context.show_context_menu(Gitkcli),
            on_close_click=lambda: self._ui_context.screen.hide_active_view(),
            on_double_click=lambda: self.toggle_window_mode()
        ))

        self.set_search_dialog(SearchDialogPopup(ID_LOG_SEARCH, self._ui_context))

    def change_log_level(self, value):
        Gitkcli.log.level = max(0, min(5, Gitkcli.log.level + value))
        self.dirty = True

class ContextMenuItem(TextListItem):
    def __init__(self, text, action, args=[], is_selectable=True, ui_context=None):
        super().__init__(text, selectable=is_selectable, dim=not is_selectable, ui_context=ui_context)
        self.action = action
        self.args = args if args else []

    def execute_action(self):
        if self.is_selectable:
            Gitkcli.screen.hide_active_view()
            self.action(*self.args)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.execute_action()
        else:
            return False
        return True

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'left-click' or event_type == 'double-click' or event_type == 'right-release':
            self.execute_action()
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

class ToggleContextMenuItem(TextListItem):
    def __init__(self, on_text, off_text, do_toggle, is_toggled, ui_context=None):
        self.on_text = on_text
        self.off_text = off_text
        self.do_toggle = do_toggle
        self.is_toggled = is_toggled
        super().__init__(self.on_text if self.is_toggled() else self.off_text, ui_context=ui_context)

    def execute_action(self):
        self.do_toggle()
        self.set_text(self.on_text if self.is_toggled() else self.off_text)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.execute_action()
        else:
            return False
        return True

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'left-click' or event_type == 'double-click' or event_type == 'right-release':
            self.execute_action()
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

class ContextMenu(ListView):
    def __init__(self, ui_context):
        super().__init__(ID_CONTEXT_MENU, 'window', ui_context=ui_context)
        self.is_popup = True

    def on_activated(self):
        super().on_activated()
        Gitkcli.mouse.capture_mouse_movement(True, self)

    def on_deactivated(self):
        super().on_deactivated()
        Gitkcli.mouse.capture_mouse_movement(False, self)
        
    def show_context_menu(self, item, view_id:str = '') -> bool:
        if Gitkcli.screen.showed_views[-1] == self:
            return True
        self.clear()
        self._selected = -1
        if not view_id:
            view_id = Gitkcli.screen.showed_views[-1].id
        view = Gitkcli.screen.get_active_view()
        x = Gitkcli.mouse.mouse_x
        y = Gitkcli.mouse.mouse_y
        if item == Gitkcli: # main menu
            win_y, win_x = view.win.getbegyx()
            x = win_x + view.x
            y = win_y + view.y
            self.append(ContextMenuItem("Show Git commit log <F1>", item.git_log.show, ui_context=self._ui_context))
            self.append(ContextMenuItem("Show Git references <F2>", item.git_refs.show, ui_context=self._ui_context))
            self.append(ContextMenuItem("Show Git commit diff <F3>", item.git_diff.show, ui_context=self._ui_context))
            self.append(ContextMenuItem("Show Logs <F4>", item.log.view.show, ui_context=self._ui_context))
            self.append(SeparatorItem(self._ui_context))
            self.append(ContextMenuItem("Toggle window/fullscreen", view.toggle_window_mode, ui_context=self._ui_context))
            self.append(ContextMenuItem("Pin to left", view.set_view_mode, ['left'], ui_context=self._ui_context))
            self.append(ContextMenuItem("Pin to right", view.set_view_mode, ['right'], ui_context=self._ui_context))
            self.append(ContextMenuItem("Pin to top", view.set_view_mode, ['top'], ui_context=self._ui_context))
            self.append(ContextMenuItem("Pin to bottom", view.set_view_mode, ['bottom'], ui_context=self._ui_context))
            self.append(SeparatorItem(self._ui_context))
            self.append(ContextMenuItem("Search </>", view.handle_input, [ord('/')], ui_context=self._ui_context))
            self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard, ui_context=self._ui_context))
            self.append(SeparatorItem(self._ui_context))
            if view_id == 'git-log':
                self.append(ContextMenuItem("Refresh <F5>", item.git_log.refresh_head, ui_context=self._ui_context))
                self.append(ContextMenuItem("Reload <Shift+F5>", item.reload_refs_commits, ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                # def __init__(self, on_text, off_text, do_toggle, is_toggled):
                self.append(ToggleContextMenuItem("Hide commit ID", "Show commit ID", view.toggle_show_commit_id, lambda: view.show_commit_id, ui_context=self._ui_context))
                self.append(ToggleContextMenuItem("Hide commit date", "Show commit date", view.toggle_show_commit_date, lambda: view.show_commit_date, ui_context=self._ui_context))
                self.append(ToggleContextMenuItem("Hide commit author", "Show commit author", view.toggle_show_commit_author, lambda: view.show_commit_author, ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
            elif view_id == 'git-diff':
                self.append(ToggleContextMenuItem("Show space change", "Ignore space change", view.change_ignore_whitespace, lambda: view.ignore_whitespace, ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
            elif view_id == 'git-refs':
                self.append(ContextMenuItem("Reread references", item.reload_refs, ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
            elif view_id == 'log':
                self.append(ContextMenuItem("Clear log", view.clear, ui_context=self._ui_context))
                self.append(ToggleContextMenuItem("Disable autoscroll", "Enable autoscroll", view.toggle_autoscroll, lambda: view.autoscroll, ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
            self.append(ContextMenuItem("Quit", item.exit_program, ui_context=self._ui_context))
        elif view_id == 'git-log' and hasattr(item, 'id'):
            if item.id == 'local-staged':
                self.append(ContextMenuItem("Clear staged changes", view.clean_uncommitted_changes, [True], ui_context=self._ui_context))
            elif item.id == 'local-working':
                self.append(ContextMenuItem("Clear unstaged changes", view.clean_uncommitted_changes, [False], ui_context=self._ui_context))
            else:
                self.append(ContextMenuItem("Create new branch", Gitkcli.git_refs.view_new_ref.create_ref, [item.id], ui_context=self._ui_context))
                self.append(ContextMenuItem("Create new tag", Gitkcli.git_refs.view_new_ref.create_ref, [item.id, 'tag'], ui_context=self._ui_context))
                self.append(ContextMenuItem("Cherry-pick this commit", view.cherry_pick, [item.id], ui_context=self._ui_context))
                self.append(ContextMenuItem("Revert this commit", view.revert, [item.id], ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                self.append(ContextMenuItem("Reset here", view.reset, [False, item.id], ui_context=self._ui_context))
                self.append(ContextMenuItem("Hard reset here", view.reset, [True, item.id], ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                self.append(ContextMenuItem("Diff this --> selected", view.diff_commits, [item.id, view.get_selected_commit_id()], ui_context=self._ui_context))
                self.append(ContextMenuItem("Diff selected --> this", view.diff_commits, [view.get_selected_commit_id(), item.id], ui_context=self._ui_context))
                self.append(ContextMenuItem("Diff this --> marked commit", view.diff_commits, [item.id, view.marked_commit_id], bool(view.marked_commit_id), ui_context=self._ui_context))
                self.append(ContextMenuItem("Diff marked commit --> this", view.diff_commits, [view.marked_commit_id, item.id], bool(view.marked_commit_id), ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                self.append(ContextMenuItem("Mark this commit", view.mark_commit, [item.id], ui_context=self._ui_context))
                self.append(ContextMenuItem("Return to mark", view.select_commit, [view.marked_commit_id], bool(view.marked_commit_id), ui_context=self._ui_context))
        elif view_id == 'git-diff':
            self.append(ContextMenuItem("Jump to file", StatListItem.jump_to_file, [item], isinstance(item, StatListItem), ui_context=self._ui_context))
            self.append(ContextMenuItem("Show origin of this line", DiffListItem.jump_to_origin, [item], isinstance(item, DiffListItem) and item.old_file_path and item.old_file_line is not None, ui_context=self._ui_context))
            self.append(SeparatorItem(self._ui_context))
            self.append(ContextMenuItem("Copy line to clipboard", item.copy_text_to_clipboard, ui_context=self._ui_context))
            self.append(ContextMenuItem("Copy range to clipboard", view.copy_text_range_to_clipboard, [item], ui_context=self._ui_context))
        elif view_id == 'git-refs' and hasattr(item, 'data'):
            if item.data['type'] == 'heads':
                self.append(ContextMenuItem("Check out this branch", self.checkout_branch, [item.data['name']], ui_context=self._ui_context))
                self.append(ContextMenuItem("Rename this branch", Gitkcli.git_refs.view_new_ref.create_ref, [item.data['name']], ui_context=self._ui_context))
                self.append(ContextMenuItem("Copy branch name", copy_to_clipboard, [item.data['name']], ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                self.append(ContextMenuItem("Push branch to remote", self.push_ref_to_remote, [item.data['name']], ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                self.append(ContextMenuItem("Remove this branch", self.remove_branch, [item.data['name']], ui_context=self._ui_context))
            elif item.data['type'] == 'tags':
                self.append(ContextMenuItem("Copy tag name", copy_to_clipboard, [item.data['name']], ui_context=self._ui_context))
                self.append(ContextMenuItem("Show tag annotation", Gitkcli.git_diff.job.show_tag_annotation, [item.data.get('tag_id')], 'tag_id' in item.data, ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                self.append(ContextMenuItem("Push tag to remote", self.push_ref_to_remote, [item.data['name']], ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                self.append(ContextMenuItem("Remove this tag", self.remove_tag, [item.data['name']], ui_context=self._ui_context))
            elif item.data['type'] == 'remotes':
                self.append(ContextMenuItem("Copy remote branch name", copy_to_clipboard, [item.data['name']], ui_context=self._ui_context))
                self.append(SeparatorItem(self._ui_context))
                self.append(ContextMenuItem("Remove this remote branch", self.remove_remote_ref, [item.data['name']], ui_context=self._ui_context))
            else:
                self.append(ContextMenuItem("Copy ref name", copy_to_clipboard, [item.data['name']], ui_context=self._ui_context))
        elif view_id == 'log':
            self.append(ContextMenuItem("Copy line to clipboard", item.copy_text_to_clipboard, ui_context=self._ui_context))
            self.append(ContextMenuItem("Copy range to clipboard", view.copy_text_range_to_clipboard, [item], ui_context=self._ui_context))
        else:
            return False
        self.set_dimensions(x, y, len(self.items) + 2, 30)
        self.show()
        return True

    def checkout_branch(self, branch_name):
        result = Job.run_job(['git', 'checkout', branch_name])
        if result.returncode == 0:
            Gitkcli.git_log.refresh_head()
            Gitkcli.git_refs.reload_refs()
            Gitkcli.git_log.check_uncommitted_changes()
            Gitkcli.log.success(f'Switched to branch {branch_name}')
        else:
            Gitkcli.log.error(f"Error checking out branch: {result.stderr}")

    def push_ref_to_remote(self, branch_name):
        Gitkcli.git_refs.view_ref_push.ref_name = branch_name
        Gitkcli.git_refs.view_ref_push.header_item.set_text(f"Push ref: {branch_name}")
        Gitkcli.git_refs.view_ref_push.clear()
        Gitkcli.git_refs.view_ref_push.show()

    def remove_branch(self, branch_name):
        result = Job.run_job(['git', 'branch', '-D', branch_name])
        if result.returncode == 0:
            Gitkcli.git_refs.reload_refs()
            Gitkcli.log.success(f'Deleted branch {branch_name}')
        else:
            Gitkcli.log.error(f"Error deleting branch: {result.stderr}")
    
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
        result = Job.run_job(['git', 'push', '--delete', remote, branch])
        if result.returncode == 0:
            Gitkcli.git_refs.reload_refs()
            Gitkcli.log.success(f'Deleted remote branch {remote_ref}')
        else:
            Gitkcli.log.error(f"Error deleting remote branch: {result.stderr}")
    
class RefPushDialogPopup(ListView):
    def __init__(self, ui_context):
        super().__init__(ID_GIT_REF_PUSH, 'window', height=6, ui_context=ui_context)
        self.set_header_item(TextListItem('', 30, expand=True, ui_context=self._ui_context))
        self.append(SpacerListItem(self._ui_context))
        self.is_popup = True

        self.remotes = []
        for remote in Job.run_job(['git', 'remote']).stdout.rstrip().split('\n'):
            self.remotes.append(ToggleSegment(remote, callback=lambda val: self.change_remote(val.txt), ui_context=self._ui_context))
        self.change_remote(self.remotes[0].txt)

        self.force = ToggleSegment("<Force>", ui_context=self._ui_context)
        self.append(SegmentedListItem([TextSegment(f"Select remote: ", ui_context=self._ui_context)] + self.remotes +
                                      [FillerSegment(self._ui_context), TextSegment("Flags:", ui_context=self._ui_context), self.force, FillerSegment(self._ui_context)],
                                      ui_context=self._ui_context))

        self.append(SpacerListItem(self._ui_context))
        self.append(SegmentedListItem([FillerSegment(self._ui_context),
                                       ButtonSegment("[Push]", lambda: self.handle_input(curses.KEY_ENTER), ui_context=self._ui_context),
                                       ButtonSegment("[Cancel]", lambda: self.handle_input(curses.KEY_EXIT), ui_context=self._ui_context),
                                       FillerSegment(self._ui_context)],
                                      ui_context=self._ui_context))
        self.ref_name = ''

        for item in self.items:
            item.is_selectable = False

    def change_remote(self, new_remote):
        self.remote = new_remote
        for remote in self.remotes:
            remote.toggled = remote.txt == self.remote

    def clear(self):
        self.force.toggled = False

    def push_ref(self):
        args = ['git', 'push']
        if self.force.toggled:
            args += ['-f']
        args += [self.remote, self.ref_name]
        result = Job.run_job(args)
        if result.returncode == 0:
            Gitkcli.git_refs.reload_refs()
            Gitkcli.log.success(f'Branch pushed {self.ref_name} to {self.remote}')
        else:
            Gitkcli.log.error(f"Error pushing ref '{self.ref_name}': {result.stderr}")

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.hide()
            self.push_ref()
        elif key == curses.KEY_EXIT:
            self.hide()
        elif key == curses.KEY_F1:
            self.force.toggle()
        elif key == KEY_TAB: # cycle through remotes
            end = False
            next_remote = self.remotes[0].txt
            for remote in self.remotes:
                if end:
                    next_remote = remote.txt
                    break
                end = remote.txt == self.remote
            self.change_remote(next_remote)
        else:
            return super().handle_input(key)
        return True

class UserInputDialogPopup(ListView):
    def __init__(self, id:str, title:str, header_item:Item, bottom_item:typing.Optional[Item] = None, ui_context=None):
        super().__init__(id, 'window', height=7, ui_context=ui_context)
        self.set_header_item(TextListItem(title, 30, expand=True, ui_context=self._ui_context))
        self.input = UserInputListItem(ui_context=self._ui_context)
        self.is_popup = True
        self.history_queries = []
        self.history_index = -1

        if not bottom_item:
            bottom_item = SegmentedListItem([FillerSegment(self._ui_context),
                                         ButtonSegment("[Execute]", lambda: self.handle_input(curses.KEY_ENTER), ui_context=self._ui_context),
                                         ButtonSegment("[Cancel]", lambda: self.handle_input(curses.KEY_EXIT), ui_context=self._ui_context),
                                         FillerSegment(self._ui_context)],
                                        ui_context=self._ui_context)
            bottom_item.is_selectable = False

        header_item.is_selectable = False

        self.append(header_item)
        self.append(SpacerListItem(self._ui_context))
        self.append(self.input)
        self.append(SpacerListItem(self._ui_context))
        self.append(bottom_item)
        self._selected = 2

    def execute(self):
        if len(self.history_queries) == 0 or self.history_queries[0] != self.input.txt:
            self.history_queries.insert(0, self.input.txt)

    def clear(self):
        self.input.clear()
        self.history_index = -1

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            self.hide()
            self.execute()

        elif key == curses.KEY_EXIT:
            self.input.txt = ""
            self.cursor_pos = 0
            self.hide()
                
        elif key == curses.KEY_DOWN or key == KEY_CTRL('n'):
            if self.history_index > 0:
                self.history_index -= 1
                self.input.set_text(self.history_queries[self.history_index])
                
        elif key == curses.KEY_UP or key == KEY_CTRL('p'):
            if self.history_index + 1 < len(self.history_queries):
                self.history_index += 1
                self.input.set_text(self.history_queries[self.history_index])

        else:
            return super().handle_input(key)
            
        return True

class BranchRenameDialogPopup(UserInputDialogPopup):
    def __init__(self, ui_context):
        super().__init__(ID_BRANCH_RENAME, ' Rename Branch', TextListItem('', ui_context=ui_context), ui_context=ui_context)
        self.old_branch_name = ''

    def rename_branch(self, name):
        self.old_branch_name = name
        self.header_item.txt = f"Rename branch '{name}' to:"
        self.clear()
        self.show()

    def execute(self):
        if not self.input.txt:
            Gitkcli.log.warning("New branch name cannot be empty")
            return
            
        args = ['git', 'branch', '-m', self.old_branch_name, self.input.txt]
        result = Job.run_job(args)
        if result.returncode == 0:
            Gitkcli.git_refs.reload_refs()
            Gitkcli.log.success(f'Branch renamed from {self.old_branch_name} to {self.input.txt}')
        else:
            Gitkcli.log.error(f"Error renaming branch: {result.stderr}")

        super().execute()

class NewRefDialogPopup(UserInputDialogPopup):
    def __init__(self, ui_context):
        self.force = ToggleSegment("<Force>", ui_context=ui_context)
        self.commit_id = ''
        self.ref_type = '' # branch or tag
        self.title_segment = TextSegment('', ui_context=ui_context)
        super().__init__(ID_NEW_GIT_REF, ' New Branch',
            SegmentedListItem([TextSegment(f"Specify the new branch name:", ui_context=ui_context), FillerSegment(ui_context),
                             TextSegment("Flags:", ui_context=ui_context), self.force, FillerSegment(ui_context)],
                            ui_context=ui_context),
            ui_context=ui_context) 

    def create_ref(self, commit_id, ref_type='branch'):
        self.commit_id = commit_id
        self.ref_type = ref_type
        self.title = f' New {ref_type}'
        self.title_segment.txt = f"Specify the new {ref_type} name:"
        self.clear()
        self.show()

    def clear(self):
        self.force.toggled = False
        super().clear()

    def handle_input(self, key):
        if key == curses.KEY_F1:
            self.force.toggle()
        else:
            return super().handle_input(key)
        return True

    def execute(self):
        args = ['git', self.ref_type]
        if self.force.toggled:
            args += ['-f']
        args += [self.input.txt, self.commit_id]
        result = Job.run_job(args)
        if result.returncode == 0:
            Gitkcli.git_refs.reload_refs()
            Gitkcli.log.success(f'{self.ref_type} {self.input.txt} created successfully')
        else:
            Gitkcli.log.error(f"Error creating {self.ref_type}: " + result.stderr)
        super().execute()

class SearchDialogPopup(UserInputDialogPopup):
    def __init__(self, id:str, ui_context):
        self.parent_list_view:ListView
        self.case_sensitive = ToggleSegment("<Case>", True, ui_context=ui_context)
        self.use_regexp = ToggleSegment("<Regexp>", ui_context=ui_context)
        self.header = SegmentedListItem([FillerSegment(ui_context), TextSegment("Flags:", ui_context=ui_context),
                                        self.case_sensitive, self.use_regexp, FillerSegment(ui_context)],
                                       ui_context=ui_context)
        buttons = SegmentedListItem([FillerSegment(ui_context),
                                     ButtonSegment("[Search Next]", lambda: self.do_search(backward=False), ui_context=ui_context),
                                     ButtonSegment("[Search Previous]", lambda: self.do_search(backward=True), ui_context=ui_context),
                                     ButtonSegment("[Close]", lambda: self.handle_input(curses.KEY_EXIT), ui_context=ui_context),
                                     FillerSegment(ui_context)],
                                    ui_context=ui_context)
        buttons.is_selectable = False
        super().__init__(id, ' Search', self.header, buttons, ui_context=ui_context)

    def do_search(self, backward:bool):
        self.parent_list_view.search(backward)
        self.dirty = True
        super().execute()

    def matches(self, item):
        if self.input.txt:
            if self.use_regexp.toggled:
                if self.case_sensitive.toggled:
                    return re.search(self.input.txt, item.get_text())
                else:
                    return re.search(self.input.txt, item.get_text(), re.IGNORECASE)
            elif self.case_sensitive.toggled:
                return self.input.txt in item.get_text()
            else:
                return self.input.txt.lower() in item.get_text().lower()
        else:
            return False

    def handle_input(self, key):
        if key == curses.KEY_DC or key == curses.KEY_BACKSPACE or key == 127 or 32 <= key <= 126:
            self.parent_list_view.dirty = True

        if key == curses.KEY_F1:
            self.case_sensitive.toggle()
        elif key == curses.KEY_F2:
            self.use_regexp.toggle()
        else:
            return super().handle_input(key)
        return True

    def execute(self):
        self.parent_list_view.search(repeat = True)
        super().execute()

class GitSearchDialogPopup(SearchDialogPopup):
    def __init__(self, ui_context):
        super().__init__(ID_GIT_LOG_SEARCH, ui_context)

        self.search_type_txt_segment = ToggleSegment("[Txt]", callback=lambda val: self.change_search_type("txt"), ui_context=ui_context)
        self.search_type_id_segment = ToggleSegment("[ID]", callback=lambda val: self.change_search_type("id"), ui_context=ui_context)
        self.search_type_message_segment = ToggleSegment("[Message]", callback=lambda val: self.change_search_type("message"), ui_context=ui_context)
        self.search_type_file_segment = ToggleSegment("[Filepaths]", callback=lambda val: self.change_search_type("path"), ui_context=ui_context)
        self.search_type_diff_segment = ToggleSegment("[Diff]", callback=lambda val: self.change_search_type("diff"), ui_context=ui_context)

        self.header.segments.insert(0, TextSegment("Type:", ui_context=ui_context))
        self.header.segments.insert(1, self.search_type_txt_segment)
        self.header.segments.insert(2, self.search_type_id_segment)
        self.header.segments.insert(3, self.search_type_message_segment)
        self.header.segments.insert(4, self.search_type_file_segment)
        self.header.segments.insert(5, self.search_type_diff_segment)

        self.change_search_type('txt')

    def change_search_type(self, new_type):
        self.search_type = new_type
        self.search_type_txt_segment.toggled = self.search_type == 'txt'
        self.search_type_id_segment.toggled = self.search_type == 'id'
        self.search_type_message_segment.toggled = self.search_type == 'message'
        self.search_type_file_segment.toggled = self.search_type == 'path'
        self.search_type_diff_segment.toggled = self.search_type == 'diff'
        self.use_regexp.enabled = self.search_type != 'path'
        self.case_sensitive.enabled = self.search_type != 'path'

    def matches(self, item):
        if self.search_type == "txt":
            return super().matches(item)
        elif hasattr(item, 'id'):
            return item.id in Gitkcli.git_log.job_git_search.found_ids
        return False

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == KEY_ENTER or key == KEY_RETURN:
            if self.search_type == "txt":
                return super().handle_input(key)

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

            self.clear()
            Gitkcli.git_log.job_git_search.start_job(args)

        elif key == KEY_TAB: # cycle through search types
            self.parent_list_view.dirty = True
            if self.search_type == "txt":
                self.change_search_type("id")
            elif self.search_type == "id":
                self.change_search_type("message")
            elif self.search_type == "message":
                self.change_search_type("path")
            elif self.search_type == "path":
                self.change_search_type("diff")
            else:
                self.change_search_type("txt")

        else:
            return super().handle_input(key)
            
        return True

class Log:
    def __init__(self):
        self.view = None  # Will be initialized later with ui_context
        self.level = 4

    def init_view(self, ui_context):
        """Initialize the log view with UI context."""
        self.view = LogView(ui_context)

    def debug(self, txt):
        if self.level > 4:
            self.log(18, txt)

    def info(self, txt):
        if self.level > 3:
            self.log(1, txt)

    def success(self, txt):
        if self.level > 2:
            self.log(1, txt, 201)

    def warning(self, txt):
        if self.level > 1:
            self.log(12, txt, 202)

    def error(self, txt):
        if self.level > 0:
            self.log(2, txt, 203)

    def log(self, color, txt, status_color = None):
        now = datetime.datetime.now()
        first_line = ''
        for line in txt.splitlines():
            if self.view:  # Only append if view is initialized
                self.view.append(TextListItem(f'{now} {line}', color, ui_context=Gitkcli.ui_context if hasattr(Gitkcli, 'ui_context') else None))
            if not first_line:
                first_line = line
        if status_color:
            Gitkcli.screen.show_status_bar_message(first_line, status_color)

class Gitkcli:
    running = True
    screen:Screen
    mouse:Mouse
    log:Log
    git_log:GitLogView
    git_diff:GitDiffView
    git_refs:GitRefsView
    context_menu:ContextMenu

    @classmethod
    def reload_refs_commits(cls):
        cls.git_refs.reload_refs()
        cls.git_log.reload_commits()

    @classmethod
    def exit_program(cls):
        cls.running = False
        for job in Job.jobs.values():
            job.stop_job()

def launch_curses(stdscr, git_args:typing.List, cmd_args:typing.List):

    Gitkcli.screen = Screen(stdscr)
    Gitkcli.mouse = Mouse(screen_manager=Gitkcli.screen)
    Gitkcli.log = Log()

    # Create UI context for the UI library
    ui_context = UIContext(
        screen_manager=Gitkcli.screen,
        logger=Gitkcli.log,
        mouse_handler=Gitkcli.mouse
    )

    # Provide callbacks for context menu and clipboard
    ui_context.set_clipboard_handler(copy_to_clipboard)

    # Store for use by git-specific classes
    Gitkcli.ui_context = ui_context

    # Initialize log view
    Gitkcli.log.init_view(ui_context)

    # Initialize views with context
    Gitkcli.git_log = GitLogView(git_args, cmd_args, ui_context)
    Gitkcli.git_diff = GitDiffView(ui_context)
    Gitkcli.git_refs = GitRefsView(ui_context)
    Gitkcli.context_menu = ContextMenu(ui_context)

    # Set context menu handler after ContextMenu is initialized
    ui_context.set_context_menu_handler(
        lambda item, view_id=None: Gitkcli.context_menu.show_context_menu(item, view_id)
    )

    Gitkcli.log.info('Application started')

    Gitkcli.git_refs.job.start_job()
    Gitkcli.git_log.job.start_job()
    Gitkcli.git_log.check_uncommitted_changes()

    Gitkcli.git_log.show()

    try:
        user_input = True

        while Gitkcli.running:

            update_jobs = Job.process_all_jobs()

            if update_jobs or user_input:

                stdscr.refresh()

                try:
                    Gitkcli.screen.draw_visible_views()
                    Gitkcli.screen.draw_status_bar(stdscr, get_job_callback=Job.get_job)
                except curses.error as e:
                    Gitkcli.log.warning(f"Curses exception: {str(e)}\n{traceback.format_exc()}")

            active_view = Gitkcli.screen.get_active_view()
            if not active_view:
                break;
            
            stdscr.timeout(5 if update_jobs else 100)

            key = stdscr.getch()
            user_input = key >= 0
            if not user_input:
                # no key pressed
                continue

            # parse escape sequences
            if key == 27: # Esc key
                sequence = []
                while key >= 0:
                    if key == 27: sequence.clear()
                    sequence.append(key)
                    key = stdscr.getch()
                Gitkcli.log.debug('Escape sequence: ' + str(sequence))
                if len(sequence) == 1:
                    key = curses.KEY_EXIT
                elif sequence == [27, 91, 49, 53, 59, 50, 126]:
                    key = KEY_SHIFT_F5
                elif sequence == [27, 91, 49, 59, 53, 68]:
                    key = KEY_CTRL_LEFT
                elif sequence == [27, 91, 49, 59, 53, 67]:
                    key = KEY_CTRL_RIGHT
                else:
                    continue
            else:
                Gitkcli.log.debug('Key: ' + str(key))

            if key == curses.KEY_MOUSE:
                _, mouse_x, mouse_y, _, Gitkcli.mouse.mouse_state = curses.getmouse()
                Gitkcli.mouse.mouse_rel_x = mouse_x - Gitkcli.mouse.mouse_x
                Gitkcli.mouse.mouse_rel_y = mouse_y - Gitkcli.mouse.mouse_y
                Gitkcli.mouse.mouse_x = mouse_x
                Gitkcli.mouse.mouse_y = mouse_y
                Gitkcli.log.debug('Mouse state: ' + str(Gitkcli.mouse.mouse_state))

                event_type = None
                if Gitkcli.mouse.mouse_state == curses.BUTTON1_PRESSED:
                    now = time.time()
                    Gitkcli.mouse.mouse_left_pressed = True
                    if now - Gitkcli.mouse.mouse_click_time < 0.3 and Gitkcli.mouse.mouse_x == Gitkcli.mouse.mouse_click_x and Gitkcli.mouse.mouse_y == Gitkcli.mouse.mouse_click_y:
                        event_type = 'double-click'
                    else:
                        Gitkcli.mouse.mouse_click_time = now
                        event_type = 'left-click'
                    Gitkcli.mouse.mouse_click_x = Gitkcli.mouse.mouse_x
                    Gitkcli.mouse.mouse_click_y = Gitkcli.mouse.mouse_y

                elif Gitkcli.mouse.mouse_state == curses.BUTTON1_RELEASED:
                    if not Gitkcli.mouse.mouse_left_pressed:
                        continue
                    Gitkcli.mouse.mouse_left_pressed = False
                    event_type = 'left-release'

                elif Gitkcli.mouse.mouse_state == curses.BUTTON3_PRESSED:
                    Gitkcli.mouse.mouse_right_pressed = True
                    event_type = 'right-click'

                elif Gitkcli.mouse.mouse_state == curses.BUTTON3_RELEASED:
                    if not Gitkcli.mouse.mouse_right_pressed:
                        continue
                    Gitkcli.mouse.mouse_right_pressed = False
                    event_type = "right-release"

                elif Gitkcli.mouse.mouse_state == curses.REPORT_MOUSE_POSITION:
                    if Gitkcli.mouse.mouse_left_pressed:
                        event_type = 'left-move'
                    elif Gitkcli.mouse.mouse_right_pressed:
                        event_type = 'right-move'
                    else:
                        event_type = 'move'

                elif Gitkcli.mouse.mouse_state == curses.BUTTON4_PRESSED:
                    event_type = 'wheel-up'

                elif Gitkcli.mouse.mouse_state == curses.BUTTON5_PRESSED:
                    event_type = 'wheel-down'

                if event_type == 'right-click' and Gitkcli.mouse.mouse_left_pressed:
                    Gitkcli.mouse.mouse_left_pressed = False
                    Gitkcli.mouse.process_mouse_event('right-release', active_view)

                if (event_type == 'left-click' or event_type == 'double-click') and Gitkcli.mouse.mouse_right_pressed:
                    Gitkcli.mouse.mouse_right_pressed = False
                    Gitkcli.mouse.process_mouse_event('left-release', active_view)

                if event_type:
                    Gitkcli.mouse.process_mouse_event(event_type, active_view)

            elif key == curses.KEY_RESIZE:
                lines, cols = Gitkcli.screen.getmaxyx()
                for view in Gitkcli.screen.views.values():
                    view.screen_size_changed(lines, cols)

            elif active_view.handle_input(key):
                active_view.dirty = True

            else:
                if key == ord('q') or key == curses.KEY_EXIT:
                    Gitkcli.screen.hide_active_view()
                elif key == KEY_CTRL_LEFT or key == KEY_CTRL('o'):
                    Gitkcli.git_log.move_in_jump_list(+1)
                elif key == KEY_CTRL_RIGHT or key == KEY_CTRL('i'):
                    Gitkcli.git_log.move_in_jump_list(-1)
                elif key == curses.KEY_F1:
                    Gitkcli.git_log.show()
                elif key == curses.KEY_F2:
                    Gitkcli.git_refs.show()
                elif key == curses.KEY_F3:
                    Gitkcli.git_diff.show()
                elif key == curses.KEY_F4:
                    Gitkcli.log.view.show()
                elif key == curses.KEY_F5:
                    Gitkcli.git_log.refresh_head()
                    Gitkcli.git_refs.reload_refs()
                elif key == KEY_SHIFT_F5:
                    Gitkcli.reload_refs_commits()

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
