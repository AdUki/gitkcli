#!/usr/bin/python

import argparse
import curses
import datetime
import pprint
import queue
import re
import subprocess
import threading

def log(color, txt):
    now = datetime.datetime.now()
    view = Gitkcli.get_view('log')
    if view:
        for line in txt.splitlines():
            view.append(TextListItem(f'{now} {line}', color))

def log_info(txt): log(1, txt)
def log_error(txt): log(2, txt)

def refresh_refs():
    Gitkcli.get_job('git-refs').start_job()

def refresh_head():
    commit_id = Gitkcli.get_job('git-refs').head_id
    if commit_id:
        Gitkcli.get_job('git-refresh-head').start_job(['--reverse', f'{commit_id}..HEAD'], clear_view = False)

def curses_ctrl(key):
    return ord(key) & 0x1F

def curses_color(number, selected = False, bold = False, reverse = False, dim = False, underline = False):
    if selected:
        color = curses.color_pair(100 + number)
    else:
        color = curses.color_pair(number)
    if reverse:
        color = color | curses.A_REVERSE
    if bold or selected:
        color = color | curses.A_BOLD
    if dim:
        color = color | curses.A_DIM
    if underline:
        color = color | curses.A_UNDERLINE
    return color

def get_ref_color_and_title(ref):
    title = f"({ref['name']})"
    color = 11
    if ref['type'] == 'head':
        color = 13
        if Gitkcli.get_job('git-refs').head_branch:
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

class Gitkcli:
    showed_views = []
    jobs = {}
    views = {}
    running = True

    @classmethod
    def add_job(cls, id, job):
        if id in cls.jobs:
            cls.jobs[id].stop_job()
        cls.jobs[id] = job

    @classmethod
    def add_view(cls, id, view):
        cls.views[id] = view

    @classmethod
    def exit_program(cls):
        cls.running = False
        for job in cls.jobs.values():
            job.stop_job()

    @classmethod
    def process_all_jobs(cls):
        for job in cls.jobs.values():
            job.process_items()

    @classmethod
    def get_job(cls, id = None):
        if len(cls.showed_views) > 0:
            id = cls.showed_views[-1] if not id else id
            if id in cls.jobs:
                return cls.jobs[id]
        return None

    @classmethod
    def get_view(cls, id = None):
        if len(cls.showed_views) > 0:
            id = cls.showed_views[-1] if not id else id
            if id in cls.views:
                return cls.views[id]
            if id in cls.jobs:
                return cls.jobs[id].view
        return None

    @classmethod
    def show_view(cls, id):
        if len(cls.showed_views) > 0 and cls.showed_views[-1] == id:
            return
        if id in cls.showed_views:
            cls.showed_views.remove(id)
        cls.showed_views.append(id)

    @classmethod
    def clear_and_show_view(cls, id):
        cls.get_view(id).clear()
        cls.show_view(id)

    @classmethod
    def hide_view(cls):
        if len(cls.showed_views) > 0:
            cls.showed_views.pop(-1)

    @classmethod
    def hide_current_and_show_view(cls, id):
        cls.hide_view()
        cls.show_view(id)

    @classmethod
    def draw_title_bar(cls, stdscr):
        lines, cols = stdscr.getmaxyx()

        title = "Gitkcli git browser"
        for view_id in reversed(cls.showed_views):
            view = cls.get_view(view_id)
            if view.title:
                title = view.title
                break

        stdscr.addstr(0, 0, title.ljust(cols-1), curses_color(7))

    @classmethod
    def draw_status_bar(cls, stdscr):
        lines, cols = stdscr.getmaxyx()
        job = cls.get_job()

        if not job or not job.view:
            return

        job_status = ''
        if job.job_running():
            job_status = 'Running'
        elif job.get_exit_code() == None:
            job_status = f"Not started"
        else:
            job_status = f"Exited with code {job.get_exit_code()}"

        stdscr.addstr(lines-1, 0, f"Line {job.view.selected+1}/{len(job.view.items)} - Offset {job.view.offset_x} - Process '{cls.showed_views[-1]}' {job_status}".ljust(cols - 1), curses_color(7))

class SubprocessJob:

    def __init__(self):
        self.cmd = ''
        self.args = []
        self.job = None
        self.running = False
        self.stop = False
        self.items = queue.Queue()
        self.messages = queue.Queue()

    def process_line(self, line):
        # This should be implemented by derived classes
        pass

    def process_item(self, item):
        # This should be implemented by derived classes
        pass

    def process_message(self, message):
        if message['type'] == 'error':
            log_error(message)
        elif message['type'] == 'started':
            self.running = True
        elif message['type'] == 'finished':
            self.running = False

    def process_items(self):
        try:
            while True:
                item = self.items.get_nowait()
                self.items.task_done()
                if not item:
                    return;
                self.process_item(item)
        except queue.Empty:
            pass
        try:
            while True:
                line = self.messages.get_nowait()
                self.messages.task_done()
                if not line:
                    return
                self.process_message(line)
        except queue.Empty:
            pass

    def stop_job(self):
        self.stop = True
        if self.job and self.job.poll() is None:
            self.job.terminate()
            try:
                self.job.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.job.kill()
                self.running = False

    def start_job(self, args = [], clear_view = True):
        self.stop_job()
        self.stop = False

        if clear_view and self.view:
            self.view.clear()

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
        return self.get_exit_code() != None

    def _reader_thread(self, stream, is_stderr=False):
        self.messages.put({'type': 'started'})
        for bytearr in iter(stream.readline, b''):
            if self.stop:
                break
            try:
                # curses automatically converts tab to spaces, so we will replace it here and cut off newline
                line = bytearr.decode('utf-8', errors='replace').replace('\t', ' ' * curses.get_tabsize())[:-1]
                if is_stderr:
                    self.messages.put({'type': 'error', 'message' :line})
                else:
                    item = self.process_line(line)
                    if item:
                        self.items.put(item)

            except Exception as e:
                self.messages.put(f"Error processing line: {line}")
                self.messages.put(str(e))
        stream.close()
        self.messages.put({'type': 'finished'})

class GitLogJob(SubprocessJob):
    def __init__(self, view = None):
        super().__init__() 
        self.cmd = 'git log --format=%H|%P|%aI|%an|%s'
        self.view = view

    def process_line(self, line):
        id, parents_str, date_str, author, title = line.split('|', 4)
        self.items.put({
            'id':id,
            'parents': parents_str.split(' '),
            'date': datetime.datetime.fromisoformat(date_str),
            'author': author,
            'title': title,
        })
    def process_item(self, item):
        if self.view:
            self.view.append(CommitListItem(item))

class GitRefreshHeadJob(GitLogJob):
    def process_item(self, item):
        if self.view:
            self.view.prepend(CommitListItem(item))

class GitShowJob(SubprocessJob):
    def __init__(self, view = None):
        super().__init__() 
        self.cmd = 'git show -m --no-color --parents'
        self.view = view

    def process_line(self, line):
        self.items.put(line)

    def process_item(self, item):
        if self.view:
            self.view.append(DiffListItem(item))

class GitSearchJob(SubprocessJob):
    def __init__(self, view = None):
        super().__init__() 
        self.cmd = 'git log --format=%H'
        self.view = view
        self.ids = set()

    def start_job(self, args = []):
        self.ids = set()
        super().start_job(args) 

    def process_line(self, line):
        self.items.put(line)

    def process_item(self, item):
        self.ids.add(item)
        if self.view:
            self.view.append(TextListItem(item))

class GitRefsJob(SubprocessJob):
    def __init__(self, view = None):
        super().__init__() 
        self.cmd = 'git show-ref --head'
        self.view = view
        self.head_id = ''

    def start_job(self, args = []):
        self.refs = {} # map: git_id --> { 'type':<ref-type>, 'name':<ref-name> }

        self.head_branch = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True).stdout.rstrip()
        if self.head_branch == 'HEAD': self.head_branch = ''

        super().start_job(args) 

    def process_line(self, line):
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

        self.items.put(ref)

    def process_item(self, item):
        if self.view:
            self.view.append(RefListItem(item))

        id = item['id']
        if id in self.refs:
            self.refs[id].append(item)
        else:
            self.refs[id] = [item]
        if item['type'] == 'head':
            self.head_id = item['id']

class RefListItem:
    def __init__(self, data):
        self.data = data

    def get_text(self):
        return self.data['name']

    def draw_line(self, stdsrc, y, offset, width, selected, matched):
        line = self.get_text()
        line = line[offset:]
        color, _ = get_ref_color_and_title(self.data)
        if matched:
            color = 16
        if selected:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]

        stdsrc.move(y, 0)
        stdsrc.addstr(line, curses_color(color, selected))
        stdsrc.clrtoeol()

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:
            if Gitkcli.get_view('git-log').jump_to_id(self.data['id']):
                Gitkcli.hide_current_and_show_view('git-log')
        else:
            return False
        return True

class TextListItem:
    def __init__(self, txt, color = 1):
        self.txt = txt
        self.color = color

    def get_text(self):
        return self.txt

    def draw_line(self, stdsrc, y, offset, width, selected, matched):
        line = self.txt[offset:]
        if selected:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]

        stdsrc.move(y, 0)
        stdsrc.addstr(line, curses_color(16 if matched else self.color, selected))
        stdsrc.clrtoeol()

    def handle_input(self, key):
        return False

class DiffListItem:
    def __init__(self, line):
        self.line = line

    def get_text(self):
        return self.line

    def draw_line(self, stdsrc, y, offset, width, selected, matched):
        stdsrc.move(y, 0)

        line = self.line[offset:]
        if selected:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]
        
        if matched:
            stdsrc.addstr(line, curses_color(16, selected))
        elif self.line.startswith('commit '):
            stdsrc.addstr('commit ' + line.split()[1], curses_color(4, selected))
        elif self.line.startswith(('diff', 'new', 'index', '+++', '---')):
            stdsrc.addstr(line, curses_color(17, selected))
        elif self.line.startswith('-'):
            stdsrc.addstr(line, curses_color(8, selected))
        elif self.line.startswith('+'):
            stdsrc.addstr(line, curses_color(9, selected))
        elif self.line.startswith('@@'):
            stdsrc.addstr(line, curses_color(10, selected))
        else:
            stdsrc.addstr(line, curses_color(1, selected))
        
        stdsrc.clrtoeol()

    def handle_input(self, key):
        return False

class CommitListItem:
    def __init__(self, data):
        self.data = data

    def get_id(self):
        return self.data['id']

    def get_text(self):
        text = ''
        text += self.data['id'][:7] + ' '
        text += self.data['date'].strftime("%Y-%m-%d %H:%M") + ' '
        text += self.data['author'].ljust(22) + ' '
        text += self.data['title']

        refs_map = Gitkcli.get_job('git-refs').refs
        refs = refs_map.get(self.data['id'], [])
        for ref in refs:
            text += ' '
            _, title = get_ref_color_and_title(ref)
            text += title

        return text

    def draw_line(self, stdsrc, y, offset, width, selected, matched):
        stdsrc.move(y, 0)

        marked = Gitkcli.get_job('git-log').view.marked == self.data['id']

        segments = [
            (self.data['id'][:7] + ' ', curses_color(4, selected, underline=marked)),
            (self.data['date'].strftime("%Y-%m-%d %H:%M") + ' ', curses_color(5, selected, underline=marked)),
            (self.data['author'].ljust(22) + ' ', curses_color(6, selected, underline=marked)),
            (self.data['title'], curses_color(16 if matched else 1, selected, underline=marked))
        ]

        head_branch = Gitkcli.get_job('git-refs').head_branch
        head_position = 0
        refs_map = Gitkcli.get_job('git-refs').refs
        refs = refs_map.get(self.data['id'], [])
        for ref in refs:
            if ref['name'] == head_branch:
                position = head_position + 2
            else:
                position = len(segments)
            if ref['type'] == 'head':
                head_position = position
            segments.insert(position, (' ', curses_color(1, selected)))
            color, title = get_ref_color_and_title(ref)
            segments.insert(position + 1, (title, curses_color(color, selected, True)))

        current_pos = 0
        for text, attr in segments:
            if offset <= current_pos + len(text):
                # This segment is partially or fully visible
                seg_offset = max(0, offset - current_pos)
                visible_text = text[seg_offset:]
  
                # Truncate if it exceeds remaining width
                if len(visible_text) > width:
                    visible_text = visible_text[:width]
  
                if visible_text:
                    stdsrc.addstr(visible_text, attr)
                    width -= len(visible_text)
  
                if width <= 0:
                    break
  
            current_pos += len(text)

        if selected:
            stdsrc.addstr(' ' * width, curses_color(1, selected))
        else:
            stdsrc.clrtoeol()

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:
            Gitkcli.get_job('git-diff').start_job([self.data['id']])
            Gitkcli.clear_and_show_view('git-diff')
        else:
            return False
        return True

class ListView:
    def __init__(self, win, search_dialog = None):
        self.title = ''
        self.win = win
        self.items = []
        self.selected = 0
        self.offset_y = 0
        self.offset_x = 0
        self.search_dialog = search_dialog
        
    def append(self, item):
        """Add item to end of list"""
        self.items.append(item)
        
    def prepend(self, item):
        """Add item to beginning of list"""
        height, _ = self.win.getmaxyx()
        self.items.insert(0, item)
        self.selected += 1
        if self.offset_y > 0:
            self.offset_y += 1
        else:
            self.ensure_selection_is_visible()
        
    def insert(self, item, position=None):
        """Insert item at position or selected position"""
        pos = position if position is not None else self.selected
        self.items.insert(pos, item)
        if pos <= self.selected:
            self.selected += 1
        if pos <= self.offset_y:
            self.offset_y += 1

    def clear(self):
        self.items = []
        self.selected = 0
        self.offset_y = 0
        self.offset_x = 0

    def ensure_selection_is_visible(self):
        height, _ = self.win.getmaxyx()
        if self.selected < self.offset_y:
            if self.offset_y - self.selected > 1:
                self.offset_y = max(0, self.selected - int(height / 2))
            else:
                self.offset_y = self.selected
        elif self.selected >= self.offset_y + height:
            if self.selected - self.offset_y - height > 1:
                self.offset_y = min(max(0, len(self.items) - height), self.selected - int(height / 2))
            else:
                self.offset_y = self.selected - height + 1

    def execute_search(self, search_dialog_view):
        for i in range(self.selected, len(self.items)):
            if search_dialog_view.matches(self.items[i]):
                self.selected = i
                self.ensure_selection_is_visible()
                return
        for i in range(0, self.selected):
            if search_dialog_view.matches(self.items[i]):
                self.selected = i
                self.ensure_selection_is_visible()
                return

    def handle_input(self, key):
        if not self.items:
            return False

        height, width = self.win.getmaxyx()
        offset_jump = int(width / 4)

        if key == curses.KEY_UP or key == ord('k'):
            if self.selected > 0:
                self.selected -= 1
            self.ensure_selection_is_visible()
        elif key == curses.KEY_DOWN or key == ord('j'):
            if self.selected < len(self.items) - 1:
                self.selected += 1
            self.ensure_selection_is_visible()
        elif key == curses.KEY_LEFT or key == ord('h'):
            if self.offset_x - offset_jump >= 0:
                self.offset_x -= offset_jump
            else:
                self.offset_x = 0
        elif key == curses.KEY_RIGHT or key == ord('l'):
            self.offset_x += offset_jump
        elif key == curses.KEY_PPAGE or key == curses_ctrl('b'):
            self.selected -= height
            self.offset_y -= height
            if self.selected < 0:
                self.selected = 0
            if self.offset_y < 0:
                self.offset_y = 0
            self.ensure_selection_is_visible()
        elif key == curses.KEY_NPAGE or key == curses_ctrl('f'):
            self.selected += height
            self.offset_y += height
            if self.selected >= len(self.items):
                self.selected = max(0, len(self.items) - 1)
            if self.offset_y >= len(self.items) - height:
                self.offset_y = max(0, len(self.items) - height)
            self.ensure_selection_is_visible()
        elif key == curses.KEY_HOME or key == ord('g'):
            self.selected = 0
            self.ensure_selection_is_visible()
        elif key == curses.KEY_END or key == ord('G'):
            self.selected = max(0, len(self.items) - 1)
            self.ensure_selection_is_visible()
        elif key == curses.KEY_MOUSE:
            _, mouse_x, mouse_y, _, mouse_state = curses.getmouse()
            begin_y, begin_x = self.win.getbegyx()
            click_x = mouse_x - begin_x
            click_y = mouse_y - begin_y
            if 0 <= click_y < height and 0 <= click_x < width:
                if mouse_state == curses.BUTTON1_PRESSED or mouse_state == curses.BUTTON1_CLICKED or mouse_state == curses.BUTTON1_DOUBLE_CLICKED:
                    new_selected = self.offset_y + click_y
                    if 0 <= new_selected < len(self.items):
                        self.selected = new_selected
                    if mouse_state == curses.BUTTON1_DOUBLE_CLICKED:
                        return self.handle_input(curses.KEY_ENTER)
                elif mouse_state == curses.BUTTON4_PRESSED: # wheel up
                    self.offset_y -= 5
                    if self.offset_y < 0:
                        self.offset_y = 0
                elif mouse_state == curses.BUTTON5_PRESSED: # wheel down
                    self.offset_y += 5
                    if self.offset_y >= len(self.items) - height:
                        self.offset_y = max(0, len(self.items) - height)
        elif key == ord('/'):
            if self.search_dialog:
                Gitkcli.clear_and_show_view(self.search_dialog)
        elif key == ord('n'):
            if self.search_dialog:
                for i in range(self.selected + 1, len(self.items)):
                    if Gitkcli.get_view(self.search_dialog).matches(self.items[i]):
                        self.selected = i
                        self.ensure_selection_is_visible()
                        break
        elif key == ord('N'):
            if self.search_dialog:
                for i in reversed(range(0, self.selected)):
                    if Gitkcli.get_view(self.search_dialog).matches(self.items[i]):
                        self.selected = i
                        self.ensure_selection_is_visible()
                        break
        else: 
            return self.items[self.selected].handle_input(key)

        return True

    def draw(self):
        height, width = self.win.getmaxyx()
        
        for i in range(0, min(height, len(self.items) - self.offset_y)):
            idx = i + self.offset_y
            item = self.items[idx]
            selected = idx == self.selected
            matched = Gitkcli.get_view(self.search_dialog).matches(item) if self.search_dialog else False
            item.draw_line(self.win, i, self.offset_x, width - 1, selected, matched)

        self.win.clrtobot()
        self.win.refresh()

class GitLogView(ListView):
    def __init__(self, win, search_dialog = None):
        super().__init__(win, search_dialog) 
        self.title = "Git commit log"
        self.marked = ''

    def jump_to_id(self, id):
        idx = 0
        for item in self.items:
            if id == item.get_id():
                self.selected = idx
                height, _ = self.win.getmaxyx()
                if self.selected < self.offset_y or self.selected >= self.offset_y + height:
                    self.offset_y = max(0, self.selected - int(height / 2))
                return True
            idx += 1
        log_error(f'Commit with hash {id} not found')
        return False

    def handle_input(self, key):
        if key == ord('q'):
            Gitkcli.exit_program()

        elif key == ord('b'):
            selected_item = self.items[self.selected]
            Gitkcli.get_view('git-log-branch').commit_id = selected_item.get_id()
            Gitkcli.clear_and_show_view('git-log-branch')

        elif key == ord('r') or key == ord('R'):
            selected_item = self.items[self.selected]
            commit_id = selected_item.get_id()
            reset_type = '--hard' if key == ord('R') else '--soft'
            result = subprocess.run(['git', 'reset', reset_type, commit_id], capture_output=True, text=True)
            if result.returncode == 0:
                refresh_refs()
            else:
                log_error(f"Error during {reset_type} reset:" + result.stderr)

        elif key == ord('c'):
            selected_item = self.items[self.selected]
            commit_id = selected_item.get_id()
            result = subprocess.run(['git', 'cherry-pick', '-m', '1', commit_id], capture_output=True, text=True)
            if result.returncode == 0:
                refresh_head()
                refresh_refs()
            else:
                log_error(f"Error during cherry-pick: " + result.stderr)

        elif key == ord('m'):
            selected_item = self.items[self.selected]
            commit_id = selected_item.get_id()
            self.marked = commit_id

        elif key == ord('M'):
            self.jump_to_id(self.marked)

        else:
            return super().handle_input(key)
        return True

class GitDiffView(ListView):
    def __init__(self, win, search_dialog = None):
        super().__init__(win, search_dialog) 
        self.title = "Git commit diff"

    def handle_input(self, key):
        if key == ord('b'):
            if not self.selected or self.items[self.selected].line.startswith('+'):
                return True

            parent_id = self.items[0].line.split(' ')[2]
            file_path = None
            line_number = None
            line_offset = 0
            
            for i in range(self.selected - 1, -1, -1):
                text = self.items[i].get_text()
                
                if text.startswith('---'):
                    file_path = text[6:]  # Remove the "--- b/" prefix
                    break
                
                if line_number is None:
                    if text.startswith(' ') or text.startswith('-'):
                        line_offset += 1
                    else:
                        match = re.search(r'@@ -(\d+),\d+ \+\d+,\d+ @@', text)
                        if match:
                            line_number = int(match.group(1)) + line_offset
            
            if file_path:
                args = ['git', 'blame', '-l', '-s', '-L',
                        f'{line_number},{line_number}', parent_id,
                        '--', file_path]

                result = subprocess.run(args, capture_output=True, text=True)
                if result.returncode == 0:
                    id = result.stdout.split(' ')[0]
                    Gitkcli.get_view('git-log').jump_to_id(id)
                    Gitkcli.hide_view()
                else:
                    log_error({' '.join(args)} + f' - exited with code {result.returncode}' + result.stderr)
            return True
        else:
            return super().handle_input(key)
        return True

class UserInputDialogPopup:
    def __init__(self, parent_win):
        self.title = ''
        self.query = ""
        self.cursor_pos = 0
        self.height = 7
        self.width = 80
        self.title_text = "Title"
        self.help_text = "Enter: Execute | Esc: Cancel"

        parent_height, parent_width = parent_win.getmaxyx()
        start_y = parent_height // 2 - self.height // 2
        start_x = parent_width // 2 - self.width // 2
        self.win = curses.newwin(self.height, self.width, start_y, start_x)

    def draw_top_panel(self):
        pass

    def execute(self):
        pass

    def draw(self):
        self.draw_top_panel()
        
        # Draw query with cursor
        prompt = ""
        max_display = self.width - 4
        query_display = self.query
        
        # Adjust scroll position if needed
        start_pos = max(0, self.cursor_pos - max_display + 5)
        if start_pos > 0:
            query_display = "..." + query_display[start_pos:]
            adjusted_cursor_pos = self.cursor_pos - start_pos + 3
        else:
            adjusted_cursor_pos = self.cursor_pos
        
        # Display query
        display_text = query_display[:max_display]
        self.win.addstr(3, 2 + len(prompt), display_text)
        self.win.clrtoeol()
        
        # Draw help text
        self.win.addstr(5, (self.width - len(self.help_text)) // 2, self.help_text, curses.A_DIM)
        
        self.win.box()
        self.win.addstr(0, (self.width - len(self.title_text)) // 2, self.title_text)
        
        # Move cursor to its position
        self.win.move(3, 2 + len(prompt) + adjusted_cursor_pos)
        
        self.win.refresh()

    def clear(self):
        curses.curs_set(1)
        self.query = ""
        self.cursor_pos = 0

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:  # Enter key
            curses.curs_set(0)
            Gitkcli.hide_view()
            self.execute()

        elif key == curses.KEY_EXIT or key == 27:  # Escape key
            curses.curs_set(0)
            self.query = ""
            self.cursor_pos = 0
            Gitkcli.hide_view()
                
        elif key == curses.KEY_BACKSPACE or key == 127:  # Backspace
            if self.cursor_pos > 0:
                self.query = self.query[:self.cursor_pos-1] + self.query[self.cursor_pos:]
                self.cursor_pos -= 1
                
        elif key == curses.KEY_DC:  # Delete key
            if self.cursor_pos < len(self.query):
                self.query = self.query[:self.cursor_pos] + self.query[self.cursor_pos+1:]
                
        elif key == curses.KEY_LEFT:  # Left arrow
            if self.cursor_pos > 0:
                self.cursor_pos -= 1
                
        elif key == curses.KEY_RIGHT:  # Right arrow
            if self.cursor_pos < len(self.query):
                self.cursor_pos += 1
                
        elif key == curses.KEY_HOME:  # Home key
            self.cursor_pos = 0
            
        elif key == curses.KEY_END:  # End key
            self.cursor_pos = len(self.query)
            
        elif 32 <= key <= 126:  # Printable characters
            self.query = self.query[:self.cursor_pos] + chr(key) + self.query[self.cursor_pos:]
            self.cursor_pos += 1

        else:
            return False
            
        return True

class NewBranchDialogPopup(UserInputDialogPopup):
    def __init__(self, parent_win):
        super().__init__(parent_win) 
        self.force = False
        self.title_text = " New Branch "
        self.help_text = "Enter: Execute | Esc: Cancel | F1: Force"
        self.commit_id = ''

    def draw_top_panel(self):
        self.win.move(1, 2)
        self.win.addstr("Specify the new branch name")
        self.win.addstr("    Flags: ")
        self.win.addstr("<Force>", curses_color(1, self.force))

    def handle_input(self, key):
        if key == curses.KEY_F1:
            self.force = not self.force
        else:
            return super().handle_input(key)
        return True

    def execute(self):
        args = ['git', 'branch']
        if self.force:
            args += ['-f']
        args += [self.query, self.commit_id]
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode == 0:
            refresh_refs()
        else:
            log_error(f"Error creating branch: " + result.stderr)

class SearchDialogPopup(UserInputDialogPopup):
    def __init__(self, parent_win, list_view):
        super().__init__(parent_win) 
        self.list_view = list_view
        self.case_sensitive = True
        self.use_regexp = False
        self.title_text = " Search "
        self.help_text = "Enter: Search | Esc: Cancel | F1: Case | F2: Regexp"

    def matches(self, item):
        if self.query:
            if self.use_regexp:
                if self.case_sensitive:
                    return re.search(self.query, item.get_text())
                else:
                    return re.search(self.query, item.get_text(), re.IGNORECASE)
            elif self.case_sensitive:
                return self.query in item.get_text()
            else:
                return self.query.lower() in item.get_text().lower()
        else:
            return False

    def draw_top_panel(self):
        self.win.move(1, 2)
        self.win.addstr("Flags: ")
        self.win.addstr("<Case>", curses_color(1, self.case_sensitive))
        self.win.addstr(" ")
        self.win.addstr("<Regexp>", curses_color(1, self.use_regexp))

    def handle_input(self, key):
        if key == curses.KEY_F1:
            self.case_sensitive = not self.case_sensitive
        elif key == curses.KEY_F2:
            self.use_regexp = not self.use_regexp
        else:
            return super().handle_input(key)
        return True

    def execute(self):
        self.list_view.execute_search(self)

class GitSearchDialogPopup(SearchDialogPopup):
    def __init__(self, parent_win, list_view):
        super().__init__(parent_win, list_view) 
        self.search_type = "message"
        self.help_text = "Enter: Search | Esc: Cancel | Tab: Change type | F1: Case | F2: Regexp"

    def matches(self, item):
        return item.get_id() in Gitkcli.get_job('git-search-results').ids

    def draw_top_panel(self):
        self.win.move(1, 2)
        self.win.addstr("Type: ")
        self.win.addstr("[Message]", curses_color(1, self.search_type == "message"))
        self.win.addstr(" ")
        self.win.addstr("[Author]", curses_color(1, self.search_type == "author"))
        self.win.addstr(" ")
        self.win.addstr("[Filepaths]", curses_color(1, self.search_type == "path"))
        self.win.addstr(" ")
        self.win.addstr("[Diff]", curses_color(1, self.search_type == "diff"))
        self.win.addstr("    Flags: ")
        self.win.addstr("<Case>", curses_color(1, self.case_sensitive, dim = self.search_type == 'path'))
        self.win.addstr(" ")
        self.win.addstr("<Regexp>", curses_color(1, self.use_regexp, dim = self.search_type == 'path'))

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:  # Enter key
            curses.curs_set(0)
            Gitkcli.hide_view()

            args = []
            if not self.case_sensitive:
                args.append('-i')
            if self.search_type == "message":
                if not self.use_regexp:
                    args.append('-F')
                args.append("--grep")
                args.append(self.query)
            if self.search_type == "author":
                if not self.use_regexp:
                    args.append('-F')
                args.append("--author")
                args.append(self.query)
            elif self.search_type == "diff":
                if self.use_regexp:
                    args.append("-G")
                else:
                    args.append("-S")
                args.append(self.query)
            elif self.search_type == "path":
                args.append('--')
                args.append(f"*{self.query}*")

            Gitkcli.get_job('git-search-results').start_job(args)

        elif key == 9:  # Tab key - cycle through search types
            if self.search_type == "message":
                self.search_type = "author"
            elif self.search_type == "author":
                self.search_type = "path"
            elif self.search_type == "path":
                self.search_type = "diff"
            else:
                self.search_type = "message"

        else:
            return super().handle_input(key)
            
        return True


def launch_curses(stdscr, cmd_args):
    # Run with curses
    curses.use_default_colors()

    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, -1)  # Normal text
    curses.init_pair(2, curses.COLOR_RED, -1)    # Error text
    curses.init_pair(3, curses.COLOR_GREEN, -1)  # Status text
    curses.init_pair(4, curses.COLOR_YELLOW, -1) # Git ID
    curses.init_pair(5, curses.COLOR_BLUE, -1)   # Data
    curses.init_pair(6, curses.COLOR_GREEN, -1)  # Author
    curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE) # Header Footer
    curses.init_pair(8, curses.COLOR_RED, -1)    # diff -
    curses.init_pair(9, curses.COLOR_GREEN, -1)  # diff +
    curses.init_pair(10, curses.COLOR_CYAN, -1)  # diff ranges
    curses.init_pair(11, curses.COLOR_GREEN, -1) # local ref
    curses.init_pair(12, curses.COLOR_YELLOW, -1) # tag
    curses.init_pair(13, curses.COLOR_BLUE, -1) # head
    curses.init_pair(14, curses.COLOR_CYAN, -1) # stash
    curses.init_pair(15, curses.COLOR_RED, -1) # remote ref
    curses.init_pair(16, curses.COLOR_MAGENTA, -1) # search match
    curses.init_pair(17, curses.COLOR_BLUE, -1) # diff info lines

    # selected colors have offset 100
    curses.init_pair(101, curses.COLOR_WHITE, 235)
    curses.init_pair(102, curses.COLOR_RED, 235)
    curses.init_pair(103, curses.COLOR_GREEN, 235)
    curses.init_pair(104, curses.COLOR_YELLOW, 235)
    curses.init_pair(105, curses.COLOR_BLUE, 235)
    curses.init_pair(106, curses.COLOR_GREEN, 235)
    curses.init_pair(108, curses.COLOR_RED, 235)
    curses.init_pair(109, curses.COLOR_GREEN, 235)
    curses.init_pair(110, curses.COLOR_CYAN, 235)
    curses.init_pair(111, curses.COLOR_GREEN, 235)
    curses.init_pair(112, curses.COLOR_YELLOW, 235)
    curses.init_pair(113, curses.COLOR_BLUE, 235)
    curses.init_pair(114, curses.COLOR_CYAN, 235)
    curses.init_pair(115, curses.COLOR_RED, 235)
    curses.init_pair(116, curses.COLOR_MAGENTA, 235)
    curses.init_pair(117, curses.COLOR_BLUE, 235)

    curses.curs_set(0)  # Hide cursor
    stdscr.timeout(100)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

    lines, cols = stdscr.getmaxyx()

    log_view = ListView(curses.newwin(lines-2, cols, 1, 0), 'log-search')
    log_view.title = "Logs"
    Gitkcli.add_view('log', log_view)

    log_search_dialog = SearchDialogPopup(stdscr, log_view)
    Gitkcli.add_view('log-search', log_search_dialog)

    git_log_view = GitLogView(curses.newwin(lines-2, cols, 1, 0), 'git-log-search')
    git_log_job = GitLogJob(git_log_view)
    git_log_job.args = cmd_args
    Gitkcli.add_job('git-log', git_log_job)
    git_log_job.start_job()

    # NOTE: This job will be no longer needed when we will have implemented graph with topology order
    git_refresh_head_job = GitRefreshHeadJob(git_log_view)
    git_refresh_head_job.args = cmd_args
    Gitkcli.add_job('git-refresh-head', git_refresh_head_job)

    git_search_job = GitSearchJob()
    git_search_job.args = cmd_args
    Gitkcli.add_job('git-search-results', git_search_job)

    git_search_dialog = GitSearchDialogPopup(stdscr, git_log_view)
    Gitkcli.add_view('git-log-search', git_search_dialog)

    log_branch_dialog = NewBranchDialogPopup(stdscr)
    Gitkcli.add_view('git-log-branch', log_branch_dialog)

    git_diff_view = GitDiffView(curses.newwin(lines-2, cols, 1, 0), 'git-diff-search')
    git_diff_job = GitShowJob(git_diff_view)
    Gitkcli.add_job('git-diff', git_diff_job)

    git_diff_search_dialog = SearchDialogPopup(stdscr, git_diff_view)
    Gitkcli.add_view('git-diff-search', git_diff_search_dialog)

    git_refs_view = ListView(curses.newwin(lines-2, cols, 1, 0), 'git-refs-search')
    git_refs_view.title = 'Git references'
    git_refs_job = GitRefsJob(git_refs_view)
    Gitkcli.add_job('git-refs', git_refs_job)
    git_refs_job.start_job()

    git_refs_search_dialog = SearchDialogPopup(stdscr, git_refs_view)
    Gitkcli.add_view('git-refs-search', git_refs_search_dialog)

    Gitkcli.show_view('git-log')

    while Gitkcli.running:
        Gitkcli.process_all_jobs()

        Gitkcli.draw_title_bar(stdscr)
        Gitkcli.draw_status_bar(stdscr)
        stdscr.refresh()

        active_view = Gitkcli.get_view()
        if not active_view:
            break;

        active_view.draw()
        
        key = stdscr.getch()
        handled = False

        if not active_view.handle_input(key):
            if key == ord('q'):
                Gitkcli.hide_view()
            elif key == curses.KEY_F1:
                Gitkcli.show_view('git-log')
            elif key == curses.KEY_F2:
                Gitkcli.show_view('git-refs')
            elif key == curses.KEY_F3:
                Gitkcli.show_view('git-diff')
            elif key == curses.KEY_F4:
                Gitkcli.show_view('log')
            elif key == curses.KEY_F5:
                refresh_head()
                refresh_refs()
            elif key == ord('~'): # Shift + F5
                refresh_refs()
                Gitkcli.get_job('git-log').start_job()

    Gitkcli.exit_program()

def main():
    parser = argparse.ArgumentParser(description='')
    args, cmd_args = parser.parse_known_args()

    curses.wrapper(lambda stdscr: launch_curses(stdscr, cmd_args))

if __name__ == "__main__":
    main()
