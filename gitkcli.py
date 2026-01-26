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
        exit_code = None
        if self.job:
            exit_code = self.job.poll()
        return exit_code

    def job_running(self):
        return self.running

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

    def handle_input(self, key) -> bool:
        return False

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'right-click':
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
                 new_file_path:typing.Optional[str] = None, new_file_line:typing.Optional[int] = None):
        self.line = line
        self.old_file_line = old_file_line
        self.old_file_path = old_file_path
        self.new_file_line = new_file_line
        self.new_file_path = new_file_path
        super().__init__(txt, color)

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

class Segment:
    def __init__(self):
        pass

    def get_text(self) -> str:
        return ''

    def set_text(self, txt:str):
        pass

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        return 0

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        return False

class FillerSegment(Segment):
    def __init__(self):
        super().__init__()

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
        visible_txt = self.get_text()[offset:width]
        win.addstr(visible_txt, Screen.color(self.color, selected, marked, matched))
        return len(visible_txt)

class RefSegment(TextSegment):
    def __init__(self, ref):
        self.ref = ref
        color, txt = GitRefsView.get_ref_color_and_title(ref)
        super().__init__(txt, color)

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'right-click':
            return Gitkcli.context_menu.show_context_menu(RefListItem(self.ref), 'git-refs')
        elif event_type == 'double-click' and 'tag_id' in self.ref:
            Gitkcli.git_diff.job.show_tag_annotation(self.ref['tag_id'])
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

class ButtonSegment(TextSegment):
    def __init__(self, txt, callback, color = 1):
        super().__init__(txt, color)
        self.callback = callback
        self.is_pressed = False

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'left-click' or event_type == 'double-click' or event_type == 'left-move-in':
            self.is_pressed = True
            return True

        if event_type == 'left-move-out':
            self.is_pressed = False
            return True

        if event_type == 'left-release':
            self.is_pressed = False
            return self.callback()
        else:
            return super().handle_mouse_input(event_type, x, y)

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        if self.is_pressed:
            visible_txt = self.get_text()[offset:width]
            dim = False
            bold = False
            if not selected:
                selected = True
            else:
                bold = True
                dim = True
            win.addstr(visible_txt, Screen.color(self.color, selected, marked, bold = bold, dim = dim))
            return len(visible_txt)
        return super().draw(win, offset, width, selected, matched, marked)

class ToggleSegment(TextSegment):
    def __init__(self, txt, toggled = False, callback = lambda val: None, color = 1):
        super().__init__(txt, color)
        self.callback = callback
        self.toggled = toggled
        self.enabled = True

    def toggle(self):
        if self.toggled:
            self.toggled = False
        else:
            self.toggled = True

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'left-click' or event_type == 'double-click':
            self.toggle()
            self.callback(self)
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        visible_txt = self.txt[offset:width]
        win.addstr(visible_txt, Screen.color(self.color, selected, self.toggled, dim = not self.enabled))
        return len(visible_txt)

class SegmentedListItem(Item):
    def __init__(self, segments = [], bg_color = 1):
        super().__init__()
        self.segment_separator = ' '
        self.segments = segments
        self.filler_width = 0
        self.bg_color = bg_color
        self.clicked_segment = None

    def get_segments(self):
        return self.segments

    def get_text(self):
        text = ''
        first = True
        for segment in self.get_segments():
            if first:
                first = False
            elif self.segment_separator:
                text += self.segment_separator
            text += segment.get_text()

        return text

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

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        segment = self.clicked_segment or self.get_segment_on_offset(x)
        if 'left-click' == event_type or 'double-click' == event_type:
            self.clicked_segment = segment
        elif self.clicked_segment:
            if 'release' in event_type:
                self.clicked_segment = None
            if 'move-in' in event_type and self.clicked_segment != self.get_segment_on_offset(x):
                event_type = event_type.replace('in', 'out')
        if segment and segment.handle_mouse_input(event_type, x, y):
            return True
        return super().handle_mouse_input(event_type, x, y)

    def get_fill_txt(self, width):
        fillers_count = 0
        for segment in self.get_segments():
            if isinstance(segment, FillerSegment):
                fillers_count += 1
        if fillers_count:
            self.fill_width = int((width - len(self.get_text())) / fillers_count)
            return self.fill_width * ' '
        return ''

    def draw_line(self, win, offset, width, selected, matched, marked):
        draw_separator = False
        remaining_width = width
        for segment in self.get_segments():
            if draw_separator and self.segment_separator:
                draw_separator = False
                remaining_width -= len(self.segment_separator)
                win.addstr(self.segment_separator, Screen.color(self.bg_color, selected, marked, matched))
            if isinstance(segment, FillerSegment):
                txt = self.get_fill_txt(width)
                win.addstr(txt, Screen.color(self.bg_color, selected, marked, matched, matched))
                length = len(txt)
            else:
                length = segment.draw(win, offset, remaining_width, selected, matched, marked)
                txt = segment.get_text()
            draw_separator = length > 0
            remaining_width -= length
            if remaining_width <= 0:
                break
            offset -= len(txt) - length

        if remaining_width > 0:
            if selected or marked:
                win.addstr(' ' * remaining_width, Screen.color(self.bg_color, selected, marked, matched))
            else:
                win.clrtoeol()

class WindowTopBarItem(SegmentedListItem):
    def __init__(self, title:str, additional_segments = [], color = 30):
        self.title_segment = TextSegment(title, color)
        segments = [ButtonSegment('[Menu]', lambda: Gitkcli.context_menu.show_context_menu(Gitkcli), color),
                    self.title_segment,
                    FillerSegment()]
        segments.extend(additional_segments)
        segments.append(ButtonSegment("[X]", lambda: Gitkcli.screen.hide_active_view(), color))
        super().__init__(segments, color)

    def set_title(self, txt:str):
        self.title_segment.set_text(txt)

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        handled = super().handle_mouse_input(event_type, x, y)
        if handled:
            return True
        if 'double-click' == event_type:
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

class View:

    def __init__(self, id:str,
                 view_mode:str = 'fullscreen',
                 x:typing.Optional[int] = None, y:typing.Optional[int] = None,
                 height:typing.Optional[int] = None, width:typing.Optional[int] = None):

        self.id:str = id
        self.view_mode:str = view_mode
        self.header_item:typing.Any = None
        self.footer_item:typing.Any = None
        self.is_popup:bool = False

        # coordinates and sizes when view is 'window'
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width

        self.dirty:bool = True
        self.resized:bool = False
        self.resize_mode:str = ''
        
        height, width, y, x = self._calculate_dimensions()
        self.win = curses.newwin(height, width, y, x)

        Gitkcli.screen.add_view(id, self)
        
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

        if self.header_item or self.view_mode == 'window':
            # substract header or window "box"
            self.height -= 1
            self.y += 1

        if self.footer_item or self.view_mode == 'window':
            # substract footer or window "box"
            self.height -= 1

        if self.view_mode == 'window':
            # substract window "box" width
            self.x += 1
            self.width -= 2

        return win_height, win_width, win_y, win_x

    def get_rect(self):
        y, x = self.win.getbegyx()
        h, w = self.win.getmaxyx()
        return (y, x, y + h, x + w)

    def set_header_item(self, item):
        self.header_item = item
        self._calculate_dimensions()

    def set_footer_item(self, item):
        self.footer_item = item
        self._calculate_dimensions()

    def set_view_mode(self, view_mode:str):
        if self.view_mode == view_mode:
            return
        stdscr_height, stdscr_width = Gitkcli.screen.getmaxyx()
        if view_mode == 'left':
            view_mode = 'window'
            self.set_dimensions(0, 0, stdscr_height, int(stdscr_width/2))
        if view_mode == 'right':
            view_mode = 'window'
            self.set_dimensions(int(stdscr_width/2), 0, stdscr_height, int(stdscr_width/2))
        if view_mode == 'top':
            view_mode = 'window'
            self.set_dimensions(0, 0, int(stdscr_height/2), stdscr_width)
        if view_mode == 'bottom':
            view_mode = 'window'
            self.set_dimensions(0, int(stdscr_height/2), int(stdscr_height/2), stdscr_width)
        self.view_mode = view_mode
        self.dirty = True
        if self.view_mode == 'window':
            self.resized = True
        height, width, y, x = self._calculate_dimensions(stdscr_height, stdscr_width)
        self.win.resize(height, width)
        self.win.mvwin(y, x)

    def toggle_window_mode(self):
        self.set_view_mode('fullscreen' if self.view_mode == 'window' else 'window')

    def set_dimensions(self, x, y, height, width):
        self.fixed_x = x
        self.fixed_y = y
        self.fixed_height = height
        self.fixed_width = width
        self.dirty = True
        self.resized = True
        height, width, y, x = self._calculate_dimensions()
        self.win.resize(height, width)
        self.win.mvwin(y, x)

    def start_resize(self, x:int, y:int) -> bool:
        self.resize_mode = ''
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
        stdscr_height, stdscr_width = Gitkcli.screen.getmaxyx()
        win_y, win_x = self.win.getbegyx()
        win_height, win_width = self.win.getmaxyx()

        if 'm' in self.resize_mode:
            new_x = max(0, min(win_x + Gitkcli.mouse.mouse_rel_x, stdscr_width - win_width))
            new_y = max(0, min(win_y + Gitkcli.mouse.mouse_rel_y, stdscr_height - win_height))
            self.win.mvwin(new_y, new_x)
            self.dirty = True
            self.resized = True
        else:
            new_x = win_x
            new_y = win_y
            new_width = win_width
            new_height = win_height
            if 'w' in self.resize_mode:
                new_x = max(0, win_x + Gitkcli.mouse.mouse_rel_x)
                new_width = win_width - (new_x - win_x)
            if 'e' in self.resize_mode:
                new_width = max(5, min(stdscr_width - new_x, win_width + Gitkcli.mouse.mouse_rel_x))
            if 's' in self.resize_mode:
                new_height = max(5, min(stdscr_height - new_y, win_height + Gitkcli.mouse.mouse_rel_y))
            self.set_dimensions(new_x, new_y, new_height, new_width)

    def screen_size_changed(self, lines, cols):
        self.dirty = True
        self.resized = True
        height, width, y, x = self._calculate_dimensions(lines, cols)
        self.win.resize(height, width)
        self.win.mvwin(y, x)

    def redraw(self, force=False):
        if self.dirty or force:
            self.dirty = False
            self.resized = False
            self.draw()
            return True
        else:
            return False

    def draw(self):
        if self.view_mode == 'window':
            self.win.attrset(curses.color_pair(5 if self.is_active() else 18))
            self.win.box()

        # draw header
        if self.header_item:
            _, cols = self.win.getmaxyx()
            self.win.move(0, 0)
            self.header_item.draw_line(self.win, 0, cols, self.is_active(), False, False)

        # draw footer
        if self.footer_item:
            rows, cols = self.win.getmaxyx()
            self.win.move(rows - 1, 0)
            self.footer_item.draw_line(self.win, 0, cols, self.is_active(), False, False)

        self.win.refresh()
        if self != Gitkcli.log.view and self.get_parent() != Gitkcli.log.view:
            Gitkcli.log.debug(f'Draw view {self.id}')

    def on_activated(self):
        Gitkcli.log.debug(f'View {self.id} activated')

    def on_deactivated(self):
        Gitkcli.log.debug(f'View {self.id} deactivated')

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'left-release':
            self.stop_resize()
        if event_type == 'left-move' and self.resize_mode:
            self.handle_resize()
            return True
        if self.win.enclose(Gitkcli.mouse.mouse_y, Gitkcli.mouse.mouse_x):
            if y == 0 and self.header_item and self.header_item.handle_mouse_input(event_type, x, y):
                if 'left-click' == event_type or 'double-click' == event_type:
                    Gitkcli.mouse.clicked_item = self.header_item
                return True
            if y == self.y + self.height - 1 and self.footer_item and self.footer_item.handle_mouse_input(event_type, x, y):
                if 'left-click' == event_type or 'double-click' == event_type:
                    Gitkcli.mouse.clicked_item = self.footer_item
                return True
            if event_type == 'left-click' and self.start_resize(Gitkcli.mouse.mouse_x, Gitkcli.mouse.mouse_y):
                return True
        elif self.is_popup and 'click' in event_type:
            self.hide()
            return True
        return False

    def handle_input(self, key) -> bool:
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
        self.dirty = True
        self.resized = True
        if prev_view:
            prev_view.on_deactivated()
        self.on_activated()

    def hide(self):
        if len(Gitkcli.screen.showed_views) > 0:
            if not self in Gitkcli.screen.showed_views:
                return
            deactivated = Gitkcli.screen.showed_views[-1] == self
            Gitkcli.screen.showed_views.remove(self)
            active_view = Gitkcli.screen.get_active_view()
            if active_view:
                active_view.dirty = True
                active_view.resized = True
            if deactivated:
                self.on_deactivated()

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

    def toggle_autoscroll(self):
        self.autoscroll = not self.autoscroll

    def set_search_dialog(self, search_dialog:"SearchDialogPopup"):
        self._search_dialog = search_dialog
        self._search_dialog.parent_list_view = self

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

    def size(self) -> int:
        return len(self.items)

    def append(self, item):
        """Add item to end of list"""
        self.items.append(item)
        if len(self.items) - self._offset_y < self.height:
            self.dirty = True
        if self.autoscroll:
            self._offset_y = max(0, len(self.items) - self.height)
        
    def insert(self, item, position=None):
        """Insert item at position or selected position"""
        pos = position if position is not None else self._selected
        self.items.insert(pos, item)
        if pos <= self._selected:
            self._selected += 1
        if pos <= self._offset_y:
            self._offset_y += 1
        self.dirty = True

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
        elif isinstance(what, str):
            for i, item in enumerate(Gitkcli.git_diff.items):
                if what in item.get_text():
                    new_index = i
                    break
        elif isinstance(what, re.Pattern):
            for i, item in enumerate(Gitkcli.git_diff.items):
                if what.match(item.get_text()):
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

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'wheel-up':
            self._offset_y -= 5
            if self._offset_y < 0:
                self._offset_y = 0
            return True
        if event_type == 'wheel-down':
            self._offset_y += 5
            if self._offset_y >= len(self.items) - self.height:
                self._offset_y = max(0, len(self.items) - self.height)
            return True

        if not self.resize_mode:
            view_x = x - self.x
            view_y = y - self.y
            index = self._offset_y + view_y

            if 0 <= view_y < self.height and 0 <= view_x < self.width and 0 <= index < len(self.items):
                selected = False
                if 'move' in event_type:
                    if self._selected == index:
                        return False # do not redraw when hovering over same item
                if event_type == 'left-click' or event_type == 'double-click' or ('move' in event_type and self in Gitkcli.mouse.mouse_movement_capture):
                    if self.items[index].is_selectable:
                        self.set_selected(index)
                        selected = True
                item = self.items[index]
                handled = item.handle_mouse_input(event_type, view_x + self._offset_x, index)
                if handled and ('left-click' == event_type or 'double-click' == event_type):
                    Gitkcli.mouse.clicked_item = item
                if selected or handled:
                    return True

        return super().handle_mouse_input(event_type, x, y)

    def handle_input(self, key):
        if not self.items:
            return super().handle_input(key)

        selected_item = self.get_selected()
        if selected_item and selected_item.handle_input(key):
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
            return super().handle_input(key)

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
                separator_items.append(i)
            else:
                self.win.move(self.y + i, self.x)
                item.draw_line(self.win, self._offset_x, width, selected, matched, False)

        self.win.clrtobot()
        super().draw()

        if separator_items:
            color = 5 if self.is_active() else 16
            for i in separator_items:
                if self.view_mode == 'window':
                    self.win.move(self.y + i, self.x-1)
                    self.win.addstr('├', Screen.color(color))
                    self.win.addstr('─' * self.width, Screen.color(color))
                    self.win.addstr('┤', Screen.color(color))
                else:
                    self.win.move(self.y + i, self.x)
                    self.win.addstr('─' * self.width, Screen.color(color))
            self.win.refresh()

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

        self.job = GitLogJob(ID_GIT_LOG, git_args + cmd_args)
        self.job_git_refresh_head = GitRefreshHeadJob()
        self.job_git_search = GitSearchJob(cmd_args)

        repo_name = os.path.basename(Job.run_job(['git', 'rev-parse', '--show-toplevel']).stdout.strip())
        self.set_header_item(WindowTopBarItem('Repository: ' + repo_name, [
                ButtonSegment("[<---]", lambda: self.move_in_jump_list(+1), 30),
                ButtonSegment("[--->]", lambda: self.move_in_jump_list(-1), 30)
            ]))

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
        idx = 0
        for item in self.items:
            if isinstance(item, CommitListItem) and id == item.id:
                self.set_selected(idx)
                return self.items[idx]
            idx += 1
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
                if self.select_commit(self.jump_list[new_index]):
                    pass
                else:
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
            Gitkcli.git_refs.view_new_ref.create_branch(self.get_selected_commit_id())
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
    def __init__(self, color):
        super().__init__('', color)

    def get_text(self):
        return str(Gitkcli.git_diff.context_size)

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
            ShowContextSegment(30),
            ButtonSegment("[ + ]", lambda: self.change_context(+1), 30),
            ButtonSegment("[ - ]", lambda: self.change_context(-1), 30),
            ButtonSegment("[<---]", lambda: Gitkcli.git_log.move_in_jump_list(+1), 30),
            ButtonSegment("[--->]", lambda: Gitkcli.git_log.move_in_jump_list(-1), 30)
        ]))

        self.set_search_dialog(SearchDialogPopup(ID_GIT_DIFF_SEARCH))

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
    def __init__(self):
        super().__init__(ID_GIT_REFS, 'window') 

        self.refs = {} # map: git_id --> [ { 'type':<ref-type>, 'name':<ref-name> } ]

        self.set_header_item(WindowTopBarItem('Git references'))
        self.set_search_dialog(SearchDialogPopup(ID_GIT_REFS_SEARCH))

        self.view_new_ref = NewRefDialogPopup()
        self.view_branch_rename = BranchRenameDialogPopup()
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

class ShowLogLevelSegment(TextSegment):
    def __init__(self, color):
        super().__init__('', color)

    def get_text(self):
        return str(Gitkcli.log.level)

class LogView(ListView):
    def __init__(self):
        super().__init__(ID_LOG, 'fullscreen') 

        self.set_header_item(WindowTopBarItem('Logs', [
            ButtonSegment("[Clear]", lambda: self.clear(), 30),
            TextSegment("  Log level:", 30),
            ShowLogLevelSegment(30),
            ButtonSegment("[ + ]", lambda: self.change_log_level(+1), 30),
            ButtonSegment("[ - ]", lambda: self.change_log_level(-1), 30)]))

        self.set_search_dialog(SearchDialogPopup(ID_LOG_SEARCH))

    def change_log_level(self, value):
        if 0 <= Gitkcli.log.level + value <= 5:
            Gitkcli.log.level += value
        self.dirty = True

class ContextMenuItem(TextListItem):
    def __init__(self, text, action, args=[], is_selectable=True):
        super().__init__(text, selectable = is_selectable, dim = not is_selectable)
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
    def __init__(self, on_text, off_text, do_toggle, is_toggled):
        self.on_text = on_text
        self.off_text = off_text
        self.do_toggle = do_toggle
        self.is_toggled = is_toggled
        super().__init__(self.on_text if self.is_toggled() else self.off_text)

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
    def __init__(self):
        super().__init__(ID_CONTEXT_MENU, 'window')
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
            self.append(ContextMenuItem("Show Git commit log <F1>", item.git_log.show))
            self.append(ContextMenuItem("Show Git references <F2>", item.git_refs.show))
            self.append(ContextMenuItem("Show Git commit diff <F3>", item.git_diff.show))
            self.append(ContextMenuItem("Show Logs <F4>", item.log.view.show))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Toggle window/fullscreen", view.toggle_window_mode))
            self.append(ContextMenuItem("Pin to left", view.set_view_mode, ['left']))
            self.append(ContextMenuItem("Pin to right", view.set_view_mode, ['right']))
            self.append(ContextMenuItem("Pin to top", view.set_view_mode, ['top']))
            self.append(ContextMenuItem("Pin to bottom", view.set_view_mode, ['bottom']))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Search </>", view.handle_input, [ord('/')]))
            self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard))
            self.append(SeparatorItem())
            if view_id == 'git-log':
                self.append(ContextMenuItem("Refresh <F5>", item.git_log.refresh_head))
                self.append(ContextMenuItem("Reload <Shift+F5>", item.reload_refs_commits))
                self.append(SeparatorItem())
                # def __init__(self, on_text, off_text, do_toggle, is_toggled):
                self.append(ToggleContextMenuItem("Hide commit ID", "Show commit ID", view.toggle_show_commit_id, lambda: view.show_commit_id))
                self.append(ToggleContextMenuItem("Hide commit date", "Show commit date", view.toggle_show_commit_date, lambda: view.show_commit_date))
                self.append(ToggleContextMenuItem("Hide commit author", "Show commit author", view.toggle_show_commit_author, lambda: view.show_commit_author))
                self.append(SeparatorItem())
            elif view_id == 'git-diff':
                self.append(ToggleContextMenuItem("Show space change", "Ignore space change", view.change_ignore_whitespace, lambda: view.ignore_whitespace))
                self.append(SeparatorItem())
            elif view_id == 'git-refs':
                self.append(ContextMenuItem("Reread references", item.reload_refs))
                self.append(SeparatorItem())
            elif view_id == 'log':
                self.append(ContextMenuItem("Clear log", view.clear))
                self.append(ToggleContextMenuItem("Disable autoscroll", "Enable autoscroll", view.toggle_autoscroll, lambda: view.autoscroll))
                self.append(SeparatorItem())
            self.append(ContextMenuItem("Quit", item.exit_program))
        elif view_id == 'git-log' and hasattr(item, 'id'):
            if item.id == 'local-staged':
                self.append(ContextMenuItem("Clear staged changes", view.clean_uncommitted_changes, [True]))
            elif item.id == 'local-working':
                self.append(ContextMenuItem("Clear unstaged changes", view.clean_uncommitted_changes, [False]))
            else:
                self.append(ContextMenuItem("Create new branch", Gitkcli.git_refs.view_new_ref.create_branch, [item.id]))
                self.append(ContextMenuItem("Create new tag", Gitkcli.git_refs.view_new_ref.create_tag, [item.id]))
                self.append(ContextMenuItem("Cherry-pick this commit", view.cherry_pick, [item.id]))
                self.append(ContextMenuItem("Revert this commit", view.revert, [item.id]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Reset here", view.reset, [False, item.id]))
                self.append(ContextMenuItem("Hard reset here", view.reset, [True, item.id]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Diff this --> selected", view.diff_commits, [item.id, view.get_selected_commit_id()]))
                self.append(ContextMenuItem("Diff selected --> this", view.diff_commits, [view.get_selected_commit_id(), item.id]))
                self.append(ContextMenuItem("Diff this --> marked commit", view.diff_commits, [item.id, view.marked_commit_id], bool(view.marked_commit_id)))
                self.append(ContextMenuItem("Diff marked commit --> this", view.diff_commits, [view.marked_commit_id, item.id], bool(view.marked_commit_id)))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Mark this commit", view.mark_commit, [item.id]))
                self.append(ContextMenuItem("Return to mark", view.select_commit, [view.marked_commit_id], bool(view.marked_commit_id)))
        elif view_id == 'git-diff':
            self.append(ContextMenuItem("Jump to file", StatListItem.jump_to_file, [item], isinstance(item, StatListItem)))
            self.append(ContextMenuItem("Show origin of this line", DiffListItem.jump_to_origin, [item], isinstance(item, DiffListItem) and item.old_file_path and item.old_file_line is not None))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Copy line to clipboard", item.copy_text_to_clipboard))
            self.append(ContextMenuItem("Copy range to clipboard", view.copy_text_range_to_clipboard, [item]))
        elif view_id == 'git-refs' and hasattr(item, 'data'):
            if item.data['type'] == 'heads':
                self.append(ContextMenuItem("Check out this branch", self.checkout_branch, [item.data['name']]))
                self.append(ContextMenuItem("Rename this branch", Gitkcli.git_refs.view_new_ref.create_branch, [item.data['name']]))
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
            self.append(ContextMenuItem("Copy line to clipboard", item.copy_text_to_clipboard))
            self.append(ContextMenuItem("Copy range to clipboard", view.copy_text_range_to_clipboard, [item]))
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
        Gitkcli.git_refs.view_ref_push.set_ref_name(branch_name)
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
    
    def copy_ref_name(self, ref_name):
        copy_to_clipboard(ref_name)

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
                
    def handle_input(self, key):
        if key == curses.KEY_BACKSPACE or key == 127:  # Backspace
            if self.cursor_pos > 0:
                self.txt = self.txt[:self.cursor_pos-1] + self.txt[self.cursor_pos:]
                self.cursor_pos -= 1
                
        elif key == curses.KEY_DC:  # Delete key
            if self.cursor_pos < len(self.txt):
                self.txt = self.txt[:self.cursor_pos] + self.txt[self.cursor_pos+1:]

        elif key == curses.KEY_LEFT:  # Left arrow
            if self.cursor_pos > 0:
                self.cursor_pos -= 1
                
        elif key == curses.KEY_RIGHT:  # Right arrow
            if self.cursor_pos < len(self.txt):
                self.cursor_pos += 1
                
        elif key == curses.KEY_HOME:  # Home key
            self.cursor_pos = 0
            
        elif key == curses.KEY_END:  # End key
            self.cursor_pos = len(self.txt)
            
        elif 32 <= key <= 126:  # Printable characters
            self.txt = self.txt[:self.cursor_pos] + chr(key) + self.txt[self.cursor_pos:]
            self.cursor_pos += 1

        else:
            return super().handle_input(key)

        return True

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'left-click' or event_type == 'double-click':
            self.cursor_pos = x if x < len(self.txt) else len(self.txt)
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def draw_line(self, win, offset, width, selected, matched, marked):
        # TODO: update self.offset according to offset so that cursor is always visible

        left_txt = self.txt[self.offset:self.offset+self.cursor_pos]
        right_txt = self.txt[self.offset+self.cursor_pos:self.offset+width-1]

        win.addstr(left_txt, Screen.color(self.color, selected, marked, matched))
        win.addch(ord(' '), curses.A_REVERSE | curses.A_BLINK)
        win.addstr(right_txt, Screen.color(self.color, selected, marked, matched))
        win.addstr(' ' * (width - len(left_txt) - len(right_txt) - 1), Screen.color(self.color, selected, marked, matched))

class RefPushDialogPopup(ListView):
    def __init__(self):
        super().__init__(ID_GIT_REF_PUSH, 'window', height = 6)
        self.set_header_item(TextListItem('', 30, expand = True))
        self.append(SpacerListItem())
        self.is_popup = True

        self.remotes = []
        for remote in Job.run_job(['git', 'remote']).stdout.rstrip().split('\n'):
            self.remotes.append(ToggleSegment(remote, callback = lambda val: self.change_remote(val.txt)))
        self.change_remote(self.remotes[0].txt)

        self.force = ToggleSegment("<Force>")
        self.append(SegmentedListItem([TextSegment(f"Select remote: ")] + self.remotes + [FillerSegment(), TextSegment("Flags:"), self.force, FillerSegment()]))

        self.append(SpacerListItem())
        self.append(SegmentedListItem([FillerSegment(),
                                       ButtonSegment("[Push]", lambda: self.handle_input(curses.KEY_ENTER)),
                                       ButtonSegment("[Cancel]", lambda: self.handle_input(curses.KEY_EXIT)),
                                       FillerSegment()]))
        self.ref_name = ''

        for item in self.items:
            item.is_selectable = False

    def change_remote(self, new_remote):
        self.remote = new_remote
        for remote in self.remotes:
            remote.toggled = remote.txt == self.remote

    def clear(self):
        self.force.toggled = False

    def set_ref_name(self, name):
        self.ref_name = name
        self.header_item.set_text(f"Push ref: {self.ref_name}")

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
    def __init__(self, id:str, title:str, header_item:Item, bottom_item:typing.Optional[Item] = None):
        super().__init__(id, 'window', height = 7)
        self.set_header_item(TextListItem(title, 30, expand = True))
        self.input = UserInputListItem()
        self.is_popup = True
        self.history_queries = []
        self.history_index = -1

        if not bottom_item:
            bottom_item = SegmentedListItem([FillerSegment(),
                                         ButtonSegment("[Execute]", lambda: self.handle_input(curses.KEY_ENTER)),
                                         ButtonSegment("[Cancel]", lambda: self.handle_input(curses.KEY_EXIT)),
                                         FillerSegment()])
            bottom_item.is_selectable = False

        header_item.is_selectable = False

        self.append(header_item)
        self.append(SpacerListItem())
        self.append(self.input)
        self.append(SpacerListItem())
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
    def __init__(self):
        super().__init__(ID_BRANCH_RENAME, ' Rename Branch', TextListItem(''))
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
    def __init__(self):
        self.force = ToggleSegment("<Force>")
        self.commit_id = ''
        self.ref_type = '' # branch or tag
        self.title_segment = TextSegment('')
        super().__init__(ID_NEW_GIT_REF, ' New Branch',
            SegmentedListItem([TextSegment(f"Specify the new branch name:"), FillerSegment(), TextSegment("Flags:"), self.force, FillerSegment()])) 

    def _set_ref_type(self, ref_type):
        self.title = f' New {ref_type}'
        self.ref_type = ref_type
        self.title_segment.txt = f"Specify the new {ref_type} name:"
    
    def create_branch(self, commit_id):
        self.commit_id = commit_id
        self._set_ref_type('branch')
        self.clear()
        self.show()
    
    def create_tag(self, commit_id):
        self.commit_id = commit_id
        self._set_ref_type('tag')
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
    def __init__(self, id:str):
        self.parent_list_view:ListView
        self.case_sensitive = ToggleSegment("<Case>", True)
        self.use_regexp = ToggleSegment("<Regexp>")
        self.header = SegmentedListItem([FillerSegment(), TextSegment("Flags:"), self.case_sensitive, self.use_regexp, FillerSegment()])
        buttons = SegmentedListItem([FillerSegment(),
                                     ButtonSegment("[Search Next]", lambda: self.do_search(backward = False)),
                                     ButtonSegment("[Search Previous]", lambda: self.do_search(backward = True)),
                                     ButtonSegment("[Close]", lambda: self.handle_input(curses.KEY_EXIT)),
                                     FillerSegment()])
        buttons.is_selectable = False
        super().__init__(id, ' Search', self.header, buttons)

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
    def __init__(self):
        super().__init__(ID_GIT_LOG_SEARCH) 

        self.search_type_txt_segment = ToggleSegment("[Txt]", callback = lambda val: self.change_search_type("txt"))
        self.search_type_id_segment = ToggleSegment("[ID]", callback = lambda val: self.change_search_type("id"))
        self.search_type_message_segment = ToggleSegment("[Message]", callback = lambda val: self.change_search_type("message"))
        self.search_type_file_segment = ToggleSegment("[Filepaths]", callback = lambda val: self.change_search_type("path"))
        self.search_type_diff_segment = ToggleSegment("[Diff]", callback = lambda val: self.change_search_type("diff"))

        self.header.segments.insert(0, TextSegment("Type:"))
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

class Screen:

    @classmethod
    def _subtract_rect(cls, rect, subtract):
        """Subtract a rectangle from another, returning list of non-overlapping rects"""
        y1, x1, y2, x2 = rect
        sy1, sx1, sy2, sx2 = subtract

        # No overlap
        if x2 <= sx1 or x1 >= sx2 or y2 <= sy1 or y1 >= sy2:
            return [rect]

        result = []

        # Top part (above subtract)
        if y1 < sy1:
            result.append((y1, x1, sy1, x2))

        # Bottom part (below subtract)
        if y2 > sy2:
            result.append((sy2, x1, y2, x2))

        # Left part (left of subtract, between top and bottom)
        if x1 < sx1:
            result.append((max(y1, sy1), x1, min(y2, sy2), sx1))

        # Right part (right of subtract, between top and bottom)
        if x2 > sx2:
            result.append((max(y1, sy1), sx2, min(y2, sy2), x2))

        return result

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
    def color(cls, number, selected = False, highlighted = False, matched = False, bold = None, reverse = False, dim = False, underline = False):
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
        if reverse:
            color = color | curses.A_REVERSE
        if bold or (selected and bold is None):
            color = color | curses.A_BOLD
        if dim:
            color = color | curses.A_DIM
        if underline:
            color = color | curses.A_UNDERLINE
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

        curses.init_pair(200, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Status bar normal
        curses.init_pair(201, curses.COLOR_BLACK, curses.COLOR_GREEN) # Status bar success
        curses.init_pair(202, curses.COLOR_BLACK, curses.COLOR_YELLOW)# Status bar warning
        curses.init_pair(203, curses.COLOR_WHITE, curses.COLOR_RED)   # Status bar error

        curses.curs_set(0)  # Hide cursor
        stdscr.timeout(5)
        curses.set_escdelay(20)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)

        self.status_bar_message = ''
        self.status_bar_color = None
        self.status_bar_time = time.time()

        self.stdscr = stdscr
        self.showed_views = []
        self.views = {}


    def getmaxyx(self) -> tuple[int, int]:
        y, x = self.stdscr.getmaxyx()
        return y-1, x # substrack status bar

    def add_view(self, id, view):
        self.views[id] = view

    def get_active_view(self) -> typing.Any:
        if len(self.showed_views) > 0:
            return self.showed_views[-1]
        return None

    def hide_active_view(self):
        if len(self.showed_views) > 0:
            view = self.showed_views.pop(-1)
            view.on_deactivated()
            view.win.erase()
            view.win.refresh()
            if self.get_active_view():
                self.get_active_view().dirty = True

    def get_visible_views(self):
        visible_views = []
        for view in self.showed_views:
            if view.view_mode == 'fullscreen':
                visible_views.clear()
            visible_views.append(view)
        
        # Compute visible regions for each window
        result = []
        for i, view in enumerate(visible_views):
            # Start with single rectangle region
            regions = [view.get_rect()]
            
            # Subtract all occluding windows
            for j in range(i + 1, len(visible_views)):
                new_regions = []
                for rect in regions:
                    new_regions.extend(Screen._subtract_rect(rect, visible_views[j].get_rect()))
                regions = new_regions
                
                if not regions:
                    break
            
            if regions:
                result.append(view)
        
        return result

    def show_status_bar_message(self, message:str, color:int):
        self.status_bar_message = message
        self.status_bar_color = color
        self.status_bar_time = time.time()

    def draw_status_bar(self, stdscr):
        lines, cols = stdscr.getmaxyx()

        if self.status_bar_message:
            # show status bar message for 2 seconds
            if time.time() - self.status_bar_time < 2:
                stdscr.addstr(lines-1, 0, self.status_bar_message.ljust(cols - 1), Screen.color(self.status_bar_color))
                return
            else:
                self.status_bar_message = ''

        job = Job.get_job()
        view = self.get_active_view()
        if not job or not view:
            return

        job_status = ''
        if job.job_running():
            job_status = 'Running'
        elif job.get_exit_code() == None:
            job_status = f"Not started"
        else:
            job_status = f"Exited with code {job.get_exit_code()}"

        stdscr.addstr(lines-1, 0, f"Line {view._selected+1}/{len(view.items)} - Offset {view._offset_x} - Process '{self.showed_views[-1].id}' {job_status}".ljust(cols - 1), Screen.color(200))

    def draw_visible_views(self):
        visible_views = self.get_visible_views()

        force_redraw = False
        for view in visible_views:
            if view.resized:
                force_redraw = True
                break

        if force_redraw and visible_views[0].view_mode != 'fullscreen':
                Gitkcli.screen.stdscr.clear()
                Gitkcli.screen.stdscr.refresh()

        for view in visible_views:
            force_redraw = view.redraw(force_redraw)

class Log:
    def __init__(self):
        self.view = LogView()
        self.level = 4

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
            self.view.append(TextListItem(f'{now} {line}', color))
            if not first_line:
                first_line = line
        if status_color:
            Gitkcli.screen.show_status_bar_message(first_line, status_color)

class Mouse:
    def __init__(self):
        self.mouse_x = 0
        self.mouse_y = 0
        self.mouse_state = 0
        self.mouse_click_x = 0
        self.mouse_click_y = 0
        self.mouse_rel_x = 0
        self.mouse_rel_y = 0
        self.mouse_click_time = time.time()
        self.mouse_left_pressed = False
        self.mouse_right_pressed = False
        self.mouse_movement_capture = set()

        self.clicked_view:View|None = None
        self.clicked_item:Item|None = None

    def capture_mouse_movement(self, enable:bool, id = None):
        enabled = len(self.mouse_movement_capture) > 0
        if enable:
            self.mouse_movement_capture.add(id)
            if not enabled:
                print("\033[?1003h", end='', flush=True) # start capturing mouse movement
        elif id in self.mouse_movement_capture:
            self.mouse_movement_capture.remove(id)
            if enabled and len(self.mouse_movement_capture) == 0:
                print("\033[?1000h", end='', flush=True) # end capturing mouse movement

    def process_mouse_event(self, event_type:str, active_view:View):
        if 'click' in event_type:
            self.capture_mouse_movement(True)
        if 'release' in event_type:
            self.capture_mouse_movement(False)

        if self.clicked_item:
            if self.mouse_click_y == self.mouse_y:
                if event_type == 'left-move':
                    event_type = 'left-move-in'
            elif event_type == 'left-move':
                event_type = 'left-move-out'
            elif event_type == 'left-release':
                event_type = 'left-release-out'

        enclosed_view = None
        for view in reversed(Gitkcli.screen.showed_views):
            if view.is_popup or view.win.enclose(self.mouse_y, self.mouse_x):
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
            win_x = self.mouse_x - begin_x
            win_y = self.mouse_y - begin_y
            if send_event_to.handle_mouse_input(event_type, win_x - item_x, win_y - item_y):
                view_to_process.dirty = True

        if 'release' in event_type:
            self.clicked_view = None
            self.clicked_item = None

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
    Gitkcli.mouse = Mouse()
    Gitkcli.log = Log()
    Gitkcli.git_log = GitLogView(git_args, cmd_args)
    Gitkcli.git_diff = GitDiffView()
    Gitkcli.git_refs = GitRefsView()
    Gitkcli.context_menu = ContextMenu()

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
                    Gitkcli.screen.draw_status_bar(stdscr)
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
