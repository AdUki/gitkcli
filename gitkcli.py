#!/usr/bin/python

import argparse
import curses
import datetime
import queue
import re
import subprocess
import threading
import time
import traceback
import typing

def log_debug(txt): Gitkcli.log(18, txt)
def log_info(txt): Gitkcli.log(1, txt)
def log_success(txt): Gitkcli.log(1, txt, 201)
def log_error(txt): Gitkcli.log(2, txt, 202)

def curses_ctrl(key):
    return ord(key) & 0x1F

def curses_color(number, selected = False, highlighted = False, bold = False, reverse = False, dim = False, underline = False):
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
        if Gitkcli.head_branch:
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

class SubprocessJob:

    def __init__(self, id):
        self.id = id
        self.cmd = ''
        self.args = []
        self.job = None
        self.running = False
        self.stop = False
        self.items = queue.Queue()
        self.messages = queue.Queue()
        Gitkcli.add_job(id, self)

    def process_line(self, line):
        # This should be implemented by derived classes
        pass

    def process_item(self, item):
        # This should be implemented by derived classes
        pass

    def process_message(self, message):
        if message['type'] == 'error':
            log_error(message['message'])
        elif message['type'] == 'started':
            self.running = True
        elif message['type'] == 'finished':
            self.running = False
            log_debug(f'Job finished {self.id}')

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
                message = self.messages.get_nowait()
                self.messages.task_done()
                if not message:
                    return
                self.process_message(message)
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

        if clear_view:
            view = Gitkcli.get_view(self.id)
            if view:
                view.clear()

        log_info(' '.join(['Job started', self.id + ':', self.cmd] + args + self.args))

        self.job = subprocess.Popen(
                self.cmd.split(' ') + args + self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        
        stdout_thread = threading.Thread(target=self._reader_thread, args=(self.job.stdout, False))
        stderr_thread = threading.Thread(target=self._reader_thread, args=(self.job.stderr, True))
        stdout_thread.start()
        stderr_thread.start()

    def get_exit_code(self):
        if self.job:
            return self.job.poll()
        return None

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
        if not is_stderr:
            self.messages.put({'type': 'finished'})

class GitLogJob(SubprocessJob):
    def __init__(self, id, args = [], start_job = True):
        super().__init__(id) 
        self.cmd = 'git log --format=%H|%P|%aI|%an|%s'
        self.args = args
        if start_job:
            self.start_job()

    def start_job(self, args = [], clear_view = True):
        if clear_view:
            Gitkcli.commits.clear()
            Gitkcli.get_view('git-log').dirty = True
        super().start_job(args, clear_view) 

    def process_line(self, line):
        id, parents_str, date_str, author, title = line.split('|', 4)
        self.items.put((id, {
            'parents': parents_str.split(' '),
            'date': datetime.datetime.fromisoformat(date_str),
            'author': author,
            'title': title,
        }))

    def process_item(self, item):
        id, commit = item
        if Gitkcli.add_commit(id, commit):
            view = Gitkcli.get_view(self.id)
            if view:
                view.append(CommitListItem(id))

class GitRefreshHeadJob(GitLogJob):
    def __init__(self, id):
        super().__init__(id, [], False) 

    def process_item(self, item):
        (id, commit) = item
        if Gitkcli.add_commit(id, commit):
            view = Gitkcli.get_view(self.id)
            if view:
                view.prepend(CommitListItem(id))

class GitDiffJob(SubprocessJob):
    def _get_args(self):
        args = [f'-U{Gitkcli.context_size}']
        if Gitkcli.ignore_whitespace:
            args.append('-w')
        return args

    def start_diff_job(self, old_commit_id, new_commit_id):
        self.cmd = f'git diff --no-color {old_commit_id} {new_commit_id}'
        self.start_job(self._get_args())

    def start_show_job(self, commit_id):
        self.cmd = f'git show -m --patch-with-stat --no-color {commit_id}'
        self.start_job(self._get_args())

    def change_context(self, size:int):
        Gitkcli.context_size = max(0, Gitkcli.context_size + size)
        Gitkcli.get_view(self.id).clear()
        self.start_job(self._get_args())

    def change_ignore_whitespace(self, val:bool):
        Gitkcli.ignore_whitespace = val
        Gitkcli.get_view(self.id).clear()
        self.start_job(self._get_args())

    def process_line(self, line):
        self.items.put(line)

    def process_item(self, item):
        view = Gitkcli.get_view(self.id)
        if view:
            view.append(DiffListItem(view.size(), item))

class GitSearchJob(SubprocessJob):
    def __init__(self, id, args = []):
        super().__init__(id) 
        self.cmd = 'git log --format=%H'
        self.args = args

    def start_job(self, args = [], clear_view = True):
        Gitkcli.found_ids.clear()
        Gitkcli.get_view('git-log').dirty = True
        super().start_job(args, clear_view) 

    def process_line(self, line):
        self.items.put(line)

    def process_item(self, item):
        Gitkcli.found_ids.add(item)
        Gitkcli.get_view('git-log').dirty = True

class GitRefsJob(SubprocessJob):
    def __init__(self, id):
        super().__init__(id) 
        self.cmd = 'git show-ref --head'
        self.start_job()

    def start_job(self, args = [], clear_view = True):
        Gitkcli.refs.clear()

        Gitkcli.head_branch = Gitkcli.run_job(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.rstrip()
        if Gitkcli.head_branch == 'HEAD': Gitkcli.head_branch = ''

        super().start_job(args, clear_view) 

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
        view = Gitkcli.get_view(self.id)
        if view:
            view.append(RefListItem(item))

        id = item['id']
        Gitkcli.refs.setdefault(id,[]).append(item)
        Gitkcli.get_view('git-log').dirty = True
        if item['type'] == 'head':
            Gitkcli.head_id = id

class Item:
    def __init__(self):
        self.is_selectable = True
        self.is_separator = False

    def get_text(self) -> str:
        return ''

    def draw_line(self, win, offset, width, selected, matched, marked):
        pass

    def handle_input(self, key) -> bool:
        return False

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'right-click':
            return Gitkcli.get_view('context-menu').show_context_menu(self)
        return True

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
        color, _ = get_ref_color_and_title(self.data)
        if matched:
            color = 16
        if selected or marked:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]

        win.addstr(line, curses_color(color, selected, marked))
        win.clrtoeol()

    def jump_to_ref(self):
        if Gitkcli.get_view('git-log').jump_to_id(self.data['id']):
            Gitkcli.hide_current_and_show_view('git-log')

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'double-click':
            self.jump_to_ref()
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:
            self.jump_to_ref()
            return True
        else:
            return False

class TextListItem(Item):
    def __init__(self, txt, color = 1, expand = False):
        super().__init__()
        self.txt = txt
        self.color = color
        self.expand = expand

    def get_text(self):
        return self.txt

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.get_text()[offset:]
        clear = True
        if selected or marked or self.expand:
            line += ' ' * (width - len(line))
            clear = False
        if len(line) >= width:
            line = line[:width]
            clear = False

        win.addstr(line, curses_color(16 if matched else self.color, selected, marked, dim = not self.is_selectable))
        if clear:
            win.clrtoeol()

class SpacerListItem(Item):
    def __init__(self):
        super().__init__()
        self.is_selectable = False

    def draw_line(self, win, offset, width, selected, matched, marked):
        win.clrtoeol()

class DiffListItem(TextListItem):
    def __init__(self, line:int, txt:str):
        self.line = line
        if txt.startswith('commit '):
            color = 4
        elif txt.startswith(('diff', 'new', 'index', '+++', '---')):
            color = 17
        elif txt.startswith('-'):
            color = 8
        elif txt.startswith('+'):
            color = 9
        elif txt.startswith('@@'):
            color = 10
        else:
            color = 1
        super().__init__(txt, color)

class Segment:
    def __init__(self):
        pass

    def get_text(self) -> str:
        return ''

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

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        visible_txt = self.get_text()[offset:width]
        win.addstr(visible_txt, curses_color(16 if matched else self.color, selected, marked))
        return len(visible_txt)

class RefSegment(TextSegment):
    def __init__(self, ref):
        self.ref = ref
        color, txt = get_ref_color_and_title(ref)
        super().__init__(txt, color)

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'right-click':
            return Gitkcli.get_view('context-menu').show_context_menu(RefListItem(self.ref), 'git-refs')
        else:
            return super().handle_mouse_input(event_type, x, y)

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
        if event_type == 'left-click':
            self.toggle()
            self.callback(self.toggled)
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        visible_txt = self.txt[offset:width]
        win.addstr(visible_txt, curses_color(self.color, dim = not self.enabled, highlighted = self.toggled))
        return len(visible_txt)

class SegmentedListItem(Item):
    def __init__(self, segments = [], bg_color = 1):
        super().__init__()
        self.segment_separator = ' '
        self.segments = segments
        self.filler_width = 0
        self.bg_color = bg_color

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
        segment = self.get_segment_on_offset(x)
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
        first = True
        remaining_width = width
        for segment in self.get_segments():
            if first:
                first = False
            elif self.segment_separator:
                remaining_width -= len(self.segment_separator)
                win.addstr(self.segment_separator, curses_color(self.bg_color, selected, marked))
            if isinstance(segment, FillerSegment):
                txt = self.get_fill_txt(width)
                win.addstr(txt, curses_color(16 if matched else self.bg_color, selected, marked))
                length = len(txt)
            else:
                length = segment.draw(win, offset, remaining_width, selected, matched, marked)
                txt = segment.get_text()
            remaining_width -= length
            if remaining_width <= 0:
                return
            offset -= len(txt) - length

        if selected or marked:
            win.addstr(' ' * remaining_width, curses_color(self.bg_color, selected, marked))
        else:
            win.clrtoeol()

class CommitListItem(SegmentedListItem):
    def __init__(self, id):
        super().__init__()
        self.id = id

    def get_segments(self):
        commit = Gitkcli.commits[self.id]
        segments = [
            TextSegment(self.id[:7], 4),
            TextSegment(commit['date'].strftime("%Y-%m-%d %H:%M"), 5),
            TextSegment(commit['author'].ljust(22), 6),
            TextSegment(commit['title']),
        ]

        head_position = len(segments) + 1 # +1, because we want to skip 'HEAD ->' segment
        for ref in Gitkcli.refs.get(self.id, []):
            segments.insert(head_position if ref['name'] == Gitkcli.head_branch else len(segments), RefSegment(ref))

        return segments

    def draw_line(self, win, offset, width, selected, matched, marked):
        super().draw_line(win, offset, width, selected, matched, Gitkcli.get_view('git-log').marked_commit_id == self.id)

    def show_commit(self):
        if Gitkcli.get_view('git-diff').commit_id == self.id:
            Gitkcli.show_view('git-diff')
        else:
            Gitkcli.get_view('git-diff').commit_id = self.id
            Gitkcli.get_job('git-diff').start_show_job(self.id)
            Gitkcli.clear_and_show_view('git-diff')

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'double-click':
            self.show_commit()
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13 or key == 9:
            self.show_commit()
        else:
            return False
        return True

class View:
    def __init__(self, id:str,
                 parent_win:curses.window,
                 title_item:Item = Item(),
                 view_position:str='fullscreen',
                 x:typing.Optional[int]=None, y:typing.Optional[int]=None,
                 height:typing.Optional[int]=None, width:typing.Optional[int]=None):

        self.id = id
        self.parent_id = ''
        self.parent_win = parent_win
        self.view_position = view_position
        self.title_item = title_item
        self.win_x = x
        self.win_y = y
        self.win_height = height
        self.win_width = width
        self.dirty = True
        
        parent_lines, parent_cols = parent_win.getmaxyx()
        height, width, y, x = self._calculate_dimensions(parent_lines, parent_cols)
        self.win = curses.newwin(height, width, y, x)

        Gitkcli.add_view(id, self)
        
    def _calculate_dimensions(self, lines, cols):
        self.parent_height = lines
        self.parent_width = cols

        # fullscreen dimensions
        win_height = lines - 1
        win_width = cols
        win_y = 0
        win_x = 0

        if self.view_position == 'top':
            win_height = int(lines / 2)
        elif self.view_position == 'bottom':
            top_height = lines - int(lines / 2)
            win_height = lines - top_height
            win_y = top_height - 1
        elif self.view_position == 'window':
            win_height = min(lines, self.win_height if self.win_height else int(lines / 2))
            win_width = min(cols, self.win_width if self.win_width else int(cols / 2))
            win_y = min(lines - win_height, int((lines - win_height) / 2) if self.win_y is None else self.win_y)
            win_x = min(cols - win_width, int((cols - win_width) / 2) if self.win_x is None else self.win_x)

        # substract title bar
        self.height = win_height - 1
        self.width = win_width
        self.y = 1
        self.x = 0

        if self.view_position == 'window':
            # substract "box"
            self.height -= 1
            self.width -= 2
            self.x += 1

        return win_height, win_width, win_y, win_x

    def move(self, x, y):
        self.dirty = True
        self.win_x = min(x, self.parent_width - self.win_width)
        self.win_y = min(y, self.parent_height - self.win_height)
        self.win.mvwin(self.win_y, self.win_x)

    def resize(self, height, width):
        self.dirty = True
        self.win_height = height
        self.win_width = width
        height, width, y, x = self._calculate_dimensions(self.parent_height, self.parent_width)
        self.win.resize(height, width)
        self.win.mvwin(y, x)
            
    def parent_resize(self, lines, cols):
        self.dirty = True
        self.parent_width = cols
        self.parent_height = lines
        height, width, y, x = self._calculate_dimensions(lines, cols)
        self.win.resize(height, width)
        self.win.mvwin(y, x)

    def redraw(self, force=False):
        if self.dirty or force:
            self.dirty = False
            self.draw()
            return True
        else:
            return False

    def draw(self):
        if self.view_position == 'window':
            self.win.box()

        # draw title bar
        if self.title_item:
            _, cols = self.win.getmaxyx()
            self.win.move(0, 0)
            self.title_item.draw_line(self.win, 0, cols, not Gitkcli.get_view() == self, False, False)

        self.win.refresh()
        if not self.id.startswith('log') and self.parent_id != 'log':
            log_debug(f'Draw view {self.id}')

    def on_show_view(self):
        log_debug(f'Show view {self.id}')

    def on_hide_view(self):
        log_debug(f'Hide view {self.id}')

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if y == 0:
            return self.title_item.handle_mouse_input(event_type, x, y)
        else:
            return False

    def handle_input(self, key):
        return False

class ListView(View):
    def __init__(self, id, parent_win, view_position='fullscreen', title_item = Item(), x=None, y=None, height=None, width=None, autoscroll=False):
        super().__init__(id, parent_win, title_item, view_position, x, y, height, width)
        self.items = []
        self.selected = 0
        self.offset_y = 0
        self.offset_x = 0
        self.autoscroll = autoscroll

    def copy_text_to_clipboard(self):
        text = "\n".join(item.get_text() for item in self.items)
        if not text:
            return
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            log_error("pyperclip module not found. Install with: pip install pyperclip")
        except Exception as e:
            log_error(f"Error copying to clipboard: {str(e)}")

    def size(self) -> int:
        return len(self.items)

    def append(self, item):
        """Add item to end of list"""
        self.items.append(item)
        if len(self.items) - self.offset_y < self.height:
            self.dirty = True
        if self.autoscroll:
            self.offset_y = max(0, len(self.items) - self.height)
        
    def prepend(self, item):
        """Add item to beginning of list"""
        self.items.insert(0, item)
        self.selected += 1
        if self.offset_y > 0:
            self.offset_y += 1
        else:
            self._ensure_selection_is_visible()
        
    def insert(self, item, position=None):
        """Insert item at position or selected position"""
        pos = position if position is not None else self.selected
        self.items.insert(pos, item)
        if pos <= self.selected:
            self.selected += 1
        if pos <= self.offset_y:
            self.offset_y += 1
        self.dirty = True

    def clear(self):
        self.items = []
        self.selected = 0
        self.offset_y = 0
        self.offset_x = 0
        self.dirty = True

    def _ensure_selection_is_visible(self):
        self.dirty = True
        if self.selected < self.offset_y:
            if self.offset_y - self.selected > 1:
                self.offset_y = max(0, self.selected - int(self.height / 2))
            else:
                self.offset_y = self.selected
        elif self.selected >= self.offset_y + self.height:
            if self.selected - self.offset_y - self.height > 1:
                self.offset_y = min(max(0, len(self.items) - self.height), self.selected - int(self.height / 2))
            else:
                self.offset_y = self.selected - self.height + 1

    def _skip_non_selectable_items(self, direction):
        if not self.items:
            return
        new_selected = self.selected
        while True:
            if self.items[new_selected].is_selectable:
                break
            new_selected += direction
            self.dirty = True
            if new_selected < 0 or new_selected >= len(self.items):
                new_selected = self.selected - direction
                break
        self.selected = new_selected

    def search_requested(self, search_dialog_view):
        for i in range(self.selected, len(self.items)):
            if search_dialog_view.matches(self.items[i]):
                self.selected = i
                self._ensure_selection_is_visible()
                return
        for i in range(0, self.selected):
            if search_dialog_view.matches(self.items[i]):
                self.selected = i
                self._ensure_selection_is_visible()
                return

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'wheel-up':
            self.offset_y -= 5
            if self.offset_y < 0:
                self.offset_y = 0
            return True
        if event_type == 'wheel-down':
            self.offset_y += 5
            if self.offset_y >= len(self.items) - self.height:
                self.offset_y = max(0, len(self.items) - self.height)
            return True

        view_x = x - self.x
        view_y = y - self.y
        index = self.offset_y + view_y
        if 0 <= view_y < self.height and 0 <= view_x < self.width and 0 <= index < len(self.items):
            if event_type == 'hover':
                if self.selected == index:
                    return False # do not redraw when hovering over same item
            if event_type == 'left-click' or event_type == 'hover':
                if self.items[index].is_selectable:
                    self.selected = index
            return self.items[index].handle_mouse_input(event_type, view_x + self.offset_x, index)

        return super().handle_mouse_input(event_type, x, y)

    def handle_input(self, key):
        if not self.items:
            return super().handle_input(key)

        offset_jump = int(self.width / 4)

        if self.items[self.selected].handle_input(key):
            return True

        if key == curses.KEY_UP or key == ord('k'):
            if self.selected > 0:
                self.selected -= 1
                self._skip_non_selectable_items(-1)
                self._ensure_selection_is_visible()
        elif key == curses.KEY_DOWN or key == ord('j'):
            if self.selected < len(self.items) - 1:
                self.selected += 1
                self._skip_non_selectable_items(1)
                self._ensure_selection_is_visible()
        elif key == curses.KEY_LEFT or key == ord('h'):
            if self.offset_x - offset_jump >= 0:
                self.offset_x -= offset_jump
            else:
                self.offset_x = 0
        elif key == curses.KEY_RIGHT or key == ord('l'):
            max_length = 0
            for i in range(self.offset_y, min(self.offset_y + self.height, len(self.items))):
                length = len(self.items[i].get_text())
                if length > max_length:
                    max_length = length
            if self.offset_x + self.width < max_length:
                self.offset_x += offset_jump
        elif key == curses.KEY_PPAGE or key == curses_ctrl('b'):
            self.selected -= self.height
            self.offset_y -= self.height
            if self.selected < 0:
                self.selected = 0
            if self.offset_y < 0:
                self.offset_y = 0
            self._skip_non_selectable_items(-1)
            self._ensure_selection_is_visible()
        elif key == curses.KEY_NPAGE or key == curses_ctrl('f'):
            self.selected += self.height
            self.offset_y += self.height
            if self.selected >= len(self.items):
                self.selected = max(0, len(self.items) - 1)
            if self.offset_y >= len(self.items) - self.height:
                self.offset_y = max(0, len(self.items) - self.height)
            self._skip_non_selectable_items(1)
            self._ensure_selection_is_visible()
        elif key == curses.KEY_HOME or key == ord('g'):
            self.selected = 0
            self._skip_non_selectable_items(-1)
            self._ensure_selection_is_visible()
        elif key == curses.KEY_END or key == ord('G'):
            self.selected = max(0, len(self.items) - 1)
            self._skip_non_selectable_items(1)
            self._ensure_selection_is_visible()
        elif key == ord('/'):
            search_dialog = Gitkcli.get_search_dialog(self.id)
            if search_dialog:
                Gitkcli.clear_and_show_view(search_dialog.id)
        elif key == ord('n'):
            search_dialog = Gitkcli.get_search_dialog(self.id)
            if search_dialog:
                for i in range(self.selected + 1, len(self.items)):
                    if search_dialog.matches(self.items[i]):
                        self.selected = i
                        self._ensure_selection_is_visible()
                        break
        elif key == ord('N'):
            search_dialog = Gitkcli.get_search_dialog(self.id)
            if search_dialog:
                for i in reversed(range(0, self.selected)):
                    if search_dialog.matches(self.items[i]):
                        self.selected = i
                        self._ensure_selection_is_visible()
                        break
        else: 
            return super().handle_input(key)

        return True

    def draw(self):
        search_dialog = Gitkcli.get_search_dialog(self.id)
        separator_items = []
        for i in range(0, min(self.height, len(self.items) - self.offset_y)):
            idx = i + self.offset_y
            item = self.items[idx]
            selected = idx == self.selected
            matched = search_dialog.matches(item) if search_dialog else False

            # curses throws exception if you want to write a character in bottom left corner
            width = self.width
            if i == self.height - 1:
                width -= 1

            if item.is_separator:
                separator_items.append(i)
            else:
                self.win.move(self.y + i, self.x)
                item.draw_line(self.win, self.offset_x, width, selected, matched, False)

        self.win.clrtobot()
        super().draw()

        if separator_items:
            for i in separator_items:
                if self.view_position == 'window':
                    self.win.move(self.y + i, self.x-1)
                    self.win.addstr('├', curses_color(1))
                    self.win.addstr('─' * self.width, curses_color(1))
                    self.win.addstr('┤', curses_color(1))
                else:
                    self.win.move(self.y + i, self.x)
                    self.win.addstr('─' * self.width, curses_color(1))
            self.win.refresh()

class GitLogView(ListView):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win, 'fullscreen', TextListItem('Git commit log', 19, expand = True)) 
        self.marked_commit_id = ''

    def jump_to_id(self, id):
        idx = 0
        for item in self.items:
            if id == item.id:
                self.selected = idx
                if self.selected < self.offset_y or self.selected >= self.offset_y + self.height:
                    self.offset_y = max(0, self.selected - int(self.height / 2))
                return True
            idx += 1
        log_error(f'Commit with hash {id} not found')
        return False
    
    def get_selected_commit_id(self):
        if len(self.items) > 0:
            selected_item = self.items[self.selected]
            return selected_item.id
        return ''

    def cherry_pick(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        result = Gitkcli.run_job(['git', 'cherry-pick', '-m', '1', commit_id])
        if result.returncode == 0:
            Gitkcli.refresh_head()
            Gitkcli.refresh_refs()
            log_success(f'Commit {commit_id} cherry picked successfully')
        else:
            log_error(f"Error during cherry-pick: " + result.stderr)

    def revert(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        result = Gitkcli.run_job(['git', 'revert', '--no-edit', '-m', '1', commit_id])
        if result.returncode == 0:
            Gitkcli.refresh_head()
            Gitkcli.refresh_refs()
            log_success(f'Commit {commit_id} reverted successfully')
        else:
            log_error(f"Error during revert: " + result.stderr)
    
    def create_branch(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        Gitkcli.get_view('git-log-ref').commit_id = commit_id
        Gitkcli.get_view('git-log-ref').set_ref_type('branch')
        Gitkcli.clear_and_show_view('git-log-ref')
    
    def create_tag(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        Gitkcli.get_view('git-log-ref').commit_id = commit_id
        Gitkcli.get_view('git-log-ref').set_ref_type('tag')
        Gitkcli.clear_and_show_view('git-log-ref')
    
    def reset(self, hard, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        reset_type = '--hard' if hard else '--soft'
        result = Gitkcli.run_job(['git', 'reset', reset_type, commit_id])
        if result.returncode == 0:
            Gitkcli.refresh_refs()
        else:
            log_error(f"Error during {reset_type} reset:" + result.stderr)
    
    def mark_commit(self, commit_id = None):
        commit_id = commit_id or self.get_selected_commit_id()
        self.marked_commit_id = commit_id
    
    def diff_commits(self, old_commit_id, new_commit_id):
        Gitkcli.get_job('git-diff').start_diff_job(old_commit_id, new_commit_id)
        Gitkcli.get_view('git-diff').commit_id = old_commit_id
        Gitkcli.clear_and_show_view('git-diff')

    def handle_input(self, key):
        if key == ord('q') or key == curses.KEY_EXIT or key == 27:
            Gitkcli.exit_program()
        elif key == ord('b'):
            self.create_branch()
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
            self.jump_to_id(self.marked_commit_id)
        else:
            return super().handle_input(key)
        return True

class ShowContextSegment(TextSegment):
    def __init__(self, color):
        super().__init__('', color)

    def get_text(self):
        return str(Gitkcli.context_size)

class ChangeContextSegment(TextSegment):
    def __init__(self, view_id, txt, color, change:int):
        super().__init__(txt, color)
        self.view_id = view_id
        self.change = change

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'left-click' or event_type == 'double-click':
            return Gitkcli.get_job(self.view_id).change_context(self.change)
        else:
            return super().handle_mouse_input(event_type, x, y)

class GitDiffView(ListView):
    def __init__(self, id, parent_win):
        title_item = SegmentedListItem([TextSegment("Git commit diff", 19), FillerSegment(),
                                        ToggleSegment("[Ignore space change]", Gitkcli.ignore_whitespace, lambda val: Gitkcli.get_job(self.id).change_ignore_whitespace(val), 19),
                                        TextSegment("  Lines of context:", 19),
                                        ShowContextSegment(19),
                                        ChangeContextSegment(id, "[ + ]", 19, +1),
                                        ChangeContextSegment(id, "[ - ]", 19, -1)], 19)
        super().__init__(id, parent_win, 'fullscreen', title_item) 
        self.commit_id = ''

    def show_origin_of_line(self, line_index = None):
        if line_index is None:
            line_index = self.selected

        if line_index >= len(self.items) or self.items[line_index].get_text().startswith('+'):
            return

        file_path = None
        line_number = None
        line_offset = 0
        
        for i in range(line_index - 1, -1, -1):
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
                    f'{line_number},{line_number}',
                    f'{self.commit_id}^', # get parent commit-d
                    '--', file_path]

            result = Gitkcli.run_job(args)
            if result.returncode == 0:
                # Example output:
                # d54cd46b9a960d0a01259a164e5b598e35947b89 309)         self.handle_input(curses.KEY_ENTER)
                id = result.stdout.split(' ')[0]
                # When commit id starts with '^' it means this is initial git-id and is 1 char shorer
                # ^1af87e6c2614c1aea4a81476df0deb8206d5489 451)         except Exception:
                if id.startswith('^'):
                    id = Gitkcli.run_job(['git', 'rev-parse', id]).stdout.lstrip('^').rstrip()
                Gitkcli.get_view('git-log').jump_to_id(id)
                Gitkcli.show_view('git-log')
            else:
                log_error(f"Failed to show origin: " + result.stderr)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:  # Enter key
            self.show_origin_of_line()
            return True
        else:
            return super().handle_input(key)
        return True


class ContextMenuItem(TextListItem):
    def __init__(self, text, action, args=None, is_selectable=True):
        super().__init__(text)
        self.action = action
        self.args = args if args else []
        self.is_selectable = is_selectable

    def execute_action(self):
        Gitkcli.hide_view()
        self.action(*self.args)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:
            self.execute_action()
        else:
            return False
        return True

    def handle_mouse_input(self, event_type:str, x:int, y:int) -> bool:
        if event_type == 'left-click' or event_type == 'right-release':
            self.execute_action()
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

class ContextMenu(ListView):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win, 'window')

    def on_show_view(self):
        super().on_show_view()
        print("\033[?1003h", end='', flush=True) # start capturing mouse movement

    def on_hide_view(self):
        super().on_hide_view()
        print("\033[?1000h", end='', flush=True) # end capturing mouse movement
        
    def show_context_menu(self, item, view_id:typing.Optional[str] = None):
        self.clear()
        self.selected = -1
        if not view_id:
            view_id = Gitkcli.showed_views[-1]
        view = Gitkcli.get_view()
        if view_id == 'git-log' and hasattr(item, 'id'):
            self.append(ContextMenuItem("Create new branch", view.create_branch, [item.id]))
            self.append(ContextMenuItem("Create new tag", view.create_tag, [item.id]))
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
            self.append(ContextMenuItem("Return to mark", view.jump_to_id, [view.marked_commit_id], bool(view.marked_commit_id)))
        elif view_id == 'git-diff' and hasattr(item, 'line'):
            self.append(ContextMenuItem("Show origin of this line", view.show_origin_of_line, [item.line], item.get_text().startswith((' ', '-'))))
            self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard))
        elif view_id == 'git-refs' and hasattr(item, 'data'):
            if item.data['type'] == 'heads':
                self.append(ContextMenuItem("Check out this branch", self.checkout_branch, [item.data['name']]))
                self.append(ContextMenuItem("Rename this branch", self.rename_branch, [item.data['name']]))
                self.append(ContextMenuItem("Copy branch name", self.copy_ref_name, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Remove this branch", self.remove_branch, [item.data['name']]))
            elif item.data['type'] == 'tags':
                self.append(ContextMenuItem("Copy tag name", self.copy_ref_name, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Remove this tag", self.remove_tag, [item.data['name']]))
            elif item.data['type'] == 'remotes':
                self.append(ContextMenuItem("Copy remote branch name", self.copy_ref_name, [item.data['name']]))
                self.append(SeparatorItem())
                self.append(ContextMenuItem("Remove this remote branch", self.remove_remote_ref, [item.data['name']]))
            else:
                self.append(ContextMenuItem("Copy ref name", self.copy_ref_name, [item.data['name']]))
        elif view_id == 'log':
            self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard))
        else:
            return False
        self.parent_id = view_id
        self.resize(len(self.items) + 2, 30)
        self.move(Gitkcli.mouse_x, Gitkcli.mouse_y)
        Gitkcli.show_view('context-menu')
        return True

    def checkout_branch(self, branch_name):
        result = Gitkcli.run_job(['git', 'checkout', branch_name])
        if result.returncode == 0:
            Gitkcli.refresh_head()
            Gitkcli.refresh_refs()
            log_success(f'Switched to branch {branch_name}')
        else:
            log_error(f"Error checking out branch: {result.stderr}")
    
    def rename_branch(self, branch_name):
        Gitkcli.get_view('git-branch-rename').set_old_branch_name(branch_name)
        Gitkcli.clear_and_show_view('git-branch-rename')

    def remove_branch(self, branch_name):
        result = Gitkcli.run_job(['git', 'branch', '-d', branch_name])
        if result.returncode == 0:
            Gitkcli.refresh_refs()
            log_success(f'Deleted branch {branch_name}')
        else:
            log_error(f"Error deleting branch: {result.stderr}")
    
    def remove_tag(self, tag_name):
        remotes = Gitkcli.run_job(['git', 'remote']).stdout.splitlines()
        removed_from_remotes = []

        result = Gitkcli.run_job(['git', 'tag', '-d', tag_name])
        if result.returncode == 0:
            removed_from_remotes.append('<local>')

        for remote in remotes:
            result = Gitkcli.run_job(['git', 'push', '--delete', remote, tag_name])
            if result.returncode == 0:
                removed_from_remotes.append(remote)

        if removed_from_remotes:
            Gitkcli.refresh_refs()
            log_success(f'Deleted tag {tag_name} from remotes: ' + ' '.join(removed_from_remotes))
        else:
            log_error(f"Error deleting tag: {result.stderr}")
    
    def remove_remote_ref(self, remote_ref):
        remote, branch = remote_ref.split('/', 1)
        result = Gitkcli.run_job(['git', 'push', '--delete', remote, branch])
        if result.returncode == 0:
            Gitkcli.refresh_refs()
            log_success(f'Deleted remote branch {remote_ref}')
        else:
            log_error(f"Error deleting remote branch: {result.stderr}")
    
    def copy_ref_name(self, ref_name):
        try:
            import pyperclip
            pyperclip.copy(ref_name)
            log_success(f'Name "{ref_name}" copied to clipboard')
        except ImportError:
            log_error("pyperclip module not found. Install with: pip install pyperclip")
        except Exception as e:
            log_error(f"Error copying to clipboard: {str(e)}")

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
        if event_type == 'left-click':
            self.cursor_pos = x if x < len(self.txt) else len(self.txt)
            return True
        else:
            return super().handle_mouse_input(event_type, x, y)

    def draw_line(self, win, offset, width, selected, matched, marked):
        left_txt = self.txt[self.offset:self.offset+self.cursor_pos]
        right_txt = self.txt[self.offset+self.cursor_pos:self.offset+width-1]

        win.addstr(left_txt, curses_color(16 if matched else self.color, selected, marked))
        win.addch(ord(' '), curses.A_REVERSE | curses.A_BLINK)
        win.addstr(right_txt, curses_color(16 if matched else self.color, selected, marked))
        win.addstr(' ' * (width - len(left_txt) - len(right_txt) - 1), curses_color(16 if matched else self.color, selected, marked))

class UserInputDialogPopup(ListView):
    def __init__(self, id, parent_win, title, header_item, help_text = ''):
        
        super().__init__(id, parent_win, 'window', TextListItem(title, 19, expand = True), height = 7)
        self.input = UserInputListItem()

        help_item = SegmentedListItem([FillerSegment(), TextSegment(help_text or "Enter: Execute | Esc: Cancel", 18), FillerSegment()])

        help_item.is_selectable = False
        header_item.is_selectable = False

        self.append(header_item)
        self.append(SpacerListItem())
        self.append(self.input)
        self.append(SpacerListItem())
        self.append(help_item)
        self.selected = 2

    def execute(self):
        pass

    def clear(self):
        self.input.clear()

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:  # Enter key
            Gitkcli.hide_view()
            self.execute()

        elif key == curses.KEY_EXIT or key == 27:  # Escape key
            self.input.txt = ""
            self.cursor_pos = 0
            Gitkcli.hide_view()

        else:
            return super().handle_input(key)
            
        return True

class BranchRenameDialogPopup(UserInputDialogPopup):
    def __init__(self, id, parent_win):
        self.old_branch_name = ''
        self.header_item = TextListItem('')
        super().__init__(id, parent_win, ' Rename Branch', self.header_item)

    def set_old_branch_name(self, name):
        self.old_branch_name = name
        self.header_item.txt = f"Rename branch '{self.old_branch_name}' to:"

    def execute(self):
        if not self.input.txt:
            log_error("New branch name cannot be empty")
            return
            
        args = ['git', 'branch', '-m', self.old_branch_name, self.input.txt]
        result = Gitkcli.run_job(args)
        if result.returncode == 0:
            Gitkcli.refresh_refs()
            log_success(f'Branch renamed from {self.old_branch_name} to {self.input.txt}')
        else:
            log_error(f"Error renaming branch: {result.stderr}")

class NewRefDialogPopup(UserInputDialogPopup):
    def __init__(self, id, parent_win):
        self.force = ToggleSegment("<Force>")
        self.commit_id = ''
        self.ref_type = '' # branch or tag
        self.title_segment = TextSegment('')
        super().__init__(id, parent_win, ' New Branch',
            SegmentedListItem([TextSegment(f"Specify the new branch name:"), FillerSegment(), TextSegment("Flags:"), self.force, FillerSegment()]),
            "Enter: Execute | Esc: Cancel | F1: Force") 

    def set_ref_type(self, ref_type):
        self.title = f' New {ref_type}'
        self.ref_type = ref_type
        self.title_segment.txt = f"Specify the new {ref_type} name:"

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
        result = Gitkcli.run_job(args)
        if result.returncode == 0:
            Gitkcli.refresh_refs()
            log_success(f'{self.ref_type} {self.input.txt} created successfully')
        else:
            log_error(f"Error creating {self.ref_type}: " + result.stderr)

class SearchDialogPopup(UserInputDialogPopup):
    def __init__(self, id, parent_win, help_item = None):
        self.case_sensitive = ToggleSegment("<Case>", True)
        self.use_regexp = ToggleSegment("<Regexp>")
        self.header = SegmentedListItem([FillerSegment(), TextSegment("Flags:"), self.case_sensitive, self.use_regexp, FillerSegment()])
        super().__init__(id, parent_win, ' Search', self.header, "Enter: Search | Esc: Cancel | F1: Case | F2: Regexp")

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
            Gitkcli.get_parent_view(self.id).dirty = True

        if key == curses.KEY_F1:
            self.case_sensitive.toggle()
        elif key == curses.KEY_F2:
            self.use_regexp.toggle()
        else:
            return super().handle_input(key)
        return True

    def execute(self):
        Gitkcli.get_parent_view(self.id).search_requested(self)

class GitSearchDialogPopup(SearchDialogPopup):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win, "Enter: Search | Esc: Cancel | Tab: Change type | F1: Case | F2: Regexp") 

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
        else:
            return item.id in Gitkcli.found_ids

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:  # Enter key
            if self.search_type == "txt":
                return super().handle_input(key)

            Gitkcli.hide_view()

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

            Gitkcli.get_job('git-search').start_job(args)

        elif key == 9:  # Tab key - cycle through search types
            Gitkcli.get_parent_view(self.id).dirty = True
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


class Gitkcli:

    head_branch = ''
    head_id = ''
    refs = {} # map: git_id --> [ { 'type':<ref-type>, 'name':<ref-name> } ]
    commits = {} # map: git_id --> { parents, date, author, title }
    found_ids = set()
    context_size = 3
    ignore_whitespace = False

    unlinked_parent_ids = {}

    showed_views = []
    jobs = {}
    views = {}
    running = True

    status_bar_message = ''
    status_bar_color = None
    status_bar_time = time.time()

    mouse_x = 0
    mouse_y = 0
    mouse_state = 0
    mouse_click_x = 0
    mouse_click_y = 0
    mouse_click_time = time.time()

    @classmethod
    def create_views_and_jobs(cls, stdscr, cmd_args):
        ListView('log', stdscr, title_item = TextListItem('Logs', 19, expand = True))
        SearchDialogPopup('log-search', stdscr)

        GitLogView('git-log', stdscr)
        GitSearchDialogPopup('git-log-search', stdscr)
        NewRefDialogPopup('git-log-ref', stdscr)

        GitDiffView('git-diff', stdscr)
        SearchDialogPopup('git-diff-search', stdscr)

        ListView('git-refs', stdscr, title_item = TextListItem('Git references', 19, expand = True))
        SearchDialogPopup('git-refs-search', stdscr)
        BranchRenameDialogPopup('git-branch-rename', stdscr)

        ContextMenu('context-menu', stdscr)

        GitLogJob('git-log', cmd_args)
        GitRefreshHeadJob('git-refresh-head') # NOTE: This job will be no longer needed when we will have implemented graph with topology order
        GitSearchJob('git-search', cmd_args)
        GitDiffJob('git-diff')
        GitRefsJob('git-refs')

    @classmethod
    def log(cls, color, txt, status_color = None):
        now = datetime.datetime.now()
        view = Gitkcli.get_view('log')
        first_line = ''
        if view:
            for line in txt.splitlines():
                view.append(TextListItem(f'{now} {line}', color))
                if not first_line:
                    first_line = line
        if status_color:
            cls.status_bar_message = first_line
            cls.status_bar_time = time.time()
            cls.status_bar_color = status_color

    @classmethod
    def refresh_refs(cls):
        cls.get_job('git-refs').start_job()

    @classmethod
    def refresh_head(cls):
        commit_id = cls.head_id
        if commit_id:
            cls.get_job('git-refresh-head').start_job(['--reverse', f'{commit_id}..HEAD'], clear_view = False)

    @classmethod
    def add_commit(cls, id, commit):
        if id in cls.commits:
            return False
        commit['children'] = cls.unlinked_parent_ids.pop(id, [])
        cls.commits[id] = commit
        for parent_id in commit['parents']:
            if parent_id in cls.commits:
                cls.commits[parent_id]['children'].append(id)
            else:
                cls.unlinked_parent_ids.setdefault(parent_id,[]).append(id)
        return True

    @classmethod
    def add_job(cls, id, job):
        if id in cls.jobs:
            cls.jobs[id].stop_job()
        cls.jobs[id] = job

    @classmethod
    def run_job(cls, args):
        log_info(' '.join(args))
        return subprocess.run(args, capture_output=True, text=True)

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
    def resize_all_views(cls, lines, cols):
        for view in cls.views.values():
            view.parent_resize(lines, cols)

    @classmethod
    def get_job(cls, id = None) -> typing.Any:
        if len(cls.showed_views) > 0:
            id = cls.showed_views[-1] if not id else id
            if id in cls.jobs:
                return cls.jobs[id]
        return None

    @classmethod
    def get_view(cls, id = None) -> typing.Any:
        if id is None and len(cls.showed_views) > 0:
            id = cls.showed_views[-1]
        if id in cls.views:
            return cls.views[id]
        return None

    @classmethod
    def get_parent_view(cls, id) -> ListView:
        return cls.get_view(id[:id.rfind('-')])

    @classmethod
    def get_search_dialog(cls, parent_id) -> SearchDialogPopup:
        return cls.get_view(parent_id + '-search')

    @classmethod
    def show_view(cls, id):
        if len(cls.showed_views) > 0 and cls.showed_views[-1] == id:
            return
        prev_view = cls.get_view()
        if id in cls.showed_views:
            cls.showed_views.remove(id)
        cls.showed_views.append(id)
        cls.get_view().dirty = True
        if prev_view and cls.get_view().view_position == 'fullscreen':
            prev_view.on_hide_view()
        cls.get_view().on_show_view()

    @classmethod
    def clear_and_show_view(cls, id):
        cls.get_view(id).clear()
        cls.show_view(id)

    @classmethod
    def hide_view(cls):
        if len(cls.showed_views) > 0:
            cls.get_view().on_hide_view()
            view_id = cls.showed_views.pop(-1)
            cls.get_view(view_id).win.erase()
            cls.get_view(view_id).win.refresh()
            if cls.get_view():
                cls.get_view().dirty = True

    @classmethod
    def hide_current_and_show_view(cls, id):
        cls.hide_view()
        cls.show_view(id)

    @classmethod
    def draw_visible_views(cls):
        positions = {}
        for view_id in cls.showed_views:
            view = cls.get_view(view_id)
            positions.pop('window', None)
            if view.view_position == 'fullscreen':
                positions.clear()
            positions[view.view_position] = view
            if 'top' in positions and 'bottom' in positions:
                positions.pop('fullscreen', None)

        force_redraw = False
        if 'fullscreen' in positions:
            force_redraw = positions['fullscreen'].redraw(force_redraw)
        if 'top' in positions:
            force_redraw = positions['top'].redraw(force_redraw)
        if 'bottom' in positions:
            force_redraw = positions['bottom'].redraw(force_redraw)
        if 'window' in positions:
            force_redraw = positions['window'].redraw(force_redraw)

    @classmethod
    def draw_status_bar(cls, stdscr):
        lines, cols = stdscr.getmaxyx()

        if cls.status_bar_message:
            # show status bar message for 2 seconds
            if time.time() - cls.status_bar_time < 2:
                stdscr.addstr(lines-1, 0, cls.status_bar_message.ljust(cols - 1), curses_color(cls.status_bar_color))
                return
            else:
                cls.status_bar_message = ''

        job = cls.get_job()
        view = cls.get_view()
        if not job or not view:
            return

        job_status = ''
        if job.job_running():
            job_status = 'Running'
        elif job.get_exit_code() == None:
            job_status = f"Not started"
        else:
            job_status = f"Exited with code {job.get_exit_code()}"

        stdscr.addstr(lines-1, 0, f"Line {view.selected+1}/{len(view.items)} - Offset {view.offset_x} - Process '{cls.showed_views[-1]}' {job_status}".ljust(cols - 1), curses_color(200))

def init_color(pair_number: int, fg: int, bg: int) -> None:
    curses.init_pair(pair_number, fg, bg)
    # highlighted offset
    curses.init_pair(50 + pair_number, fg, 20)
    # selected offset
    curses.init_pair(100 + pair_number, fg, 235)
    # selected+highlighted offset
    curses.init_pair(150 + pair_number, fg, 21)

def launch_curses(stdscr, cmd_args):
    # Run with curses
    curses.use_default_colors()

    curses.start_color()

    init_color(1, curses.COLOR_WHITE, -1)    # Normal text
    init_color(2, curses.COLOR_RED, -1)      # Error text
    init_color(3, curses.COLOR_GREEN, -1)    # Status text
    init_color(4, curses.COLOR_YELLOW, -1)   # Git ID
    init_color(5, curses.COLOR_BLUE, -1)     # Data
    init_color(6, curses.COLOR_GREEN, -1)    # Author
    init_color(8, curses.COLOR_RED, -1)      # diff -
    init_color(9, curses.COLOR_GREEN, -1)    # diff +
    init_color(10, curses.COLOR_CYAN, -1)    # diff ranges
    init_color(11, curses.COLOR_GREEN, -1)   # local ref
    init_color(12, curses.COLOR_YELLOW, -1)  # tag
    init_color(13, curses.COLOR_BLUE, -1)    # head
    init_color(14, curses.COLOR_CYAN, -1)    # stash
    init_color(15, curses.COLOR_RED, -1)     # remote ref
    init_color(16, curses.COLOR_MAGENTA, -1) # search match
    init_color(17, curses.COLOR_BLUE, -1)    # diff info lines
    init_color(18, 245, -1)                  # debug text

    curses.init_pair(19, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Active window title
    curses.init_pair(100 + 19, curses.COLOR_BLACK, 245)          # Inactive window title
    curses.init_pair(50 + 19, curses.COLOR_WHITE, 20)            # Active segment in window title

    curses.init_pair(200, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Status bar normal
    curses.init_pair(201, curses.COLOR_BLACK, curses.COLOR_GREEN) # Status bar success
    curses.init_pair(202, curses.COLOR_WHITE, curses.COLOR_RED)   # Status bar error

    curses.curs_set(0)  # Hide cursor
    stdscr.timeout(5)
    curses.set_escdelay(200)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    curses.mouseinterval(0)

    Gitkcli.create_views_and_jobs(stdscr, cmd_args)
    Gitkcli.show_view('git-log')

    log_info('Application started')

    while Gitkcli.running:
        Gitkcli.process_all_jobs()

        stdscr.refresh()

        try:
            Gitkcli.draw_status_bar(stdscr)
            Gitkcli.draw_visible_views()
        except curses.error as e:
            log_error(f"Curses exception: {str(e)}\n{traceback.format_exc()}")

        active_view = Gitkcli.get_view()
        if not active_view:
            break;
        
        key = stdscr.getch()
        if key < 0:
            # no key pressed
            continue

        if key == curses.KEY_MOUSE:
            _, Gitkcli.mouse_x, Gitkcli.mouse_y, _, Gitkcli.mouse_state = curses.getmouse()

            event_type = None
            if Gitkcli.mouse_state == curses.BUTTON1_PRESSED:
                now = time.time()
                if now - Gitkcli.mouse_click_time < 0.3 and Gitkcli.mouse_x == Gitkcli.mouse_click_x and Gitkcli.mouse_y == Gitkcli.mouse_click_y:
                    event_type = 'double-click'
                else:
                    Gitkcli.mouse_click_x = Gitkcli.mouse_x
                    Gitkcli.mouse_click_y = Gitkcli.mouse_y
                    Gitkcli.mouse_click_time = now
                    event_type = 'left-click'
            elif Gitkcli.mouse_state == curses.BUTTON3_RELEASED:
                event_type = "right-release"
            elif Gitkcli.mouse_state == curses.BUTTON3_PRESSED:
                event_type = 'right-click'
            elif Gitkcli.mouse_state == curses.REPORT_MOUSE_POSITION:
                event_type = 'hover'
            elif Gitkcli.mouse_state == curses.BUTTON4_PRESSED:
                event_type = 'wheel-up'
            elif Gitkcli.mouse_state == curses.BUTTON5_PRESSED:
                event_type = 'wheel-down'

            if event_type:
                begin_y, begin_x = active_view.win.getbegyx()
                win_height, win_width = active_view.win.getmaxyx()
                max_y = begin_y + win_height
                max_x = begin_x + win_width
                # check if we are inside window
                if begin_y <= Gitkcli.mouse_y < max_y and begin_x <= Gitkcli.mouse_x < max_x:
                    win_x = Gitkcli.mouse_x - begin_x
                    win_y = Gitkcli.mouse_y - begin_y
                    if active_view.handle_mouse_input(event_type, win_x, win_y):
                        active_view.dirty = True
                elif 'click' in event_type:
                    Gitkcli.hide_view()

        elif key == curses.KEY_RESIZE:
            lines, cols = stdscr.getmaxyx()
            Gitkcli.resize_all_views(lines, cols)

        elif active_view.handle_input(key):
            active_view.dirty = True

        else:
            if key == ord('q') or key == curses.KEY_EXIT or key == 27:
                Gitkcli.hide_view()
            elif key == curses.KEY_F1 or key == 9:
                Gitkcli.show_view('git-log')
            elif key == curses.KEY_F2:
                Gitkcli.show_view('git-refs')
            elif key == curses.KEY_F3:
                Gitkcli.show_view('git-diff')
            elif key == curses.KEY_F4:
                Gitkcli.show_view('log')
            elif key == curses.KEY_F5:
                Gitkcli.refresh_head()
                Gitkcli.refresh_refs()
            elif key == ord('~'): # Shift + F5
                Gitkcli.refresh_refs()
                Gitkcli.get_job('git-log').start_job()

    Gitkcli.exit_program()

    log_info('Application ended')

def main():
    parser = argparse.ArgumentParser(description='')
    args, cmd_args = parser.parse_known_args()

    curses.wrapper(lambda stdscr: launch_curses(stdscr, cmd_args))

if __name__ == "__main__":
    main()
