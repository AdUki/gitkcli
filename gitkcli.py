#!/usr/bin/python

import argparse
import curses
import datetime
import time
import typing
import queue
import re
import subprocess
import threading
import traceback

def log_info(txt): Gitkcli.log(1, txt)
def log_success(txt): Gitkcli.log(1, txt, 201)
def log_error(txt): Gitkcli.log(2, txt, 202)

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

        log_info(' '.join([self.cmd] + args + self.args))

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
    def start_diff_job(self, old_commit_id, new_commit_id):
        self.cmd = 'git diff --no-color'
        self.start_job([old_commit_id, new_commit_id])

    def start_show_job(self, commit_id):
        self.cmd = 'git show -m --no-color'
        self.start_job([commit_id])

    def process_line(self, line):
        self.items.put(line)

    def process_item(self, item):
        view = Gitkcli.get_view(self.id)
        if view:
            view.append(DiffListItem(item))

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
        self.selectable = True
        self.separator = False

    def get_text(self) -> str:
        return ''

    def draw_line(self, win, offset, width, selected, matched, marked):
        pass

    def handle_input(self, key) -> bool:
        return False

    def handle_left_click(self, offset, mouse_x, mouse_y) -> bool:
        return True

    def handle_double_click(self, offset, mouse_x, mouse_y) -> bool:
        self.handle_input(curses.KEY_ENTER)
        return True

    def handle_right_click(self, offset, mouse_x, mouse_y, clicked_idx, click_x) -> bool:
        return Gitkcli.get_view('context-menu').show_context_menu(mouse_x, mouse_y, self, clicked_idx)

class SeparatorItem(Item):
    def __init__(self):
        super().__init__()
        self.selectable = False
        self.separator = True

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
        if selected:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]

        win.addstr(line, curses_color(color, selected, underline = marked))
        win.clrtoeol()

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:
            if Gitkcli.get_view('git-log').jump_to_id(self.data['id']):
                Gitkcli.hide_current_and_show_view('git-log')
        else:
            return False
        return True

class TextListItem(Item):
    def __init__(self, txt, color = 1):
        super().__init__()
        self.txt = txt
        self.color = color

    def get_text(self):
        return self.txt

    def draw_line(self, win, offset, width, selected, matched, marked):
        line = self.txt[offset:]
        if selected:
            line += ' ' * (width - len(line))
        if len(line) > width:
            line = line[:width]

        win.addstr(line, curses_color(16 if matched else self.color, selected, underline = marked))
        win.clrtoeol()

class DiffListItem(TextListItem):
    def __init__(self, txt):
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

    def handle_left_click(self, offset, mouse_x, mouse_y) -> bool:
        return False

    def handle_double_click(self, offset, mouse_x, mouse_y) -> bool:
        return False

    def handle_right_click(self, offset, mouse_x, mouse_y, clicked_idx, click_x) -> bool:
        return False

class TextSegment(Segment):
    def __init__(self, txt, color = 1):
        super().__init__()
        self.txt = txt
        self.color = color

    def get_text(self):
        return self.txt

    def draw(self, win, offset, width, selected, matched, marked) -> int:
        visible_txt = self.txt[offset:width]
        win.addstr(visible_txt, curses_color(16 if matched else self.color, selected, underline = marked))
        return len(visible_txt)

class RefSegment(TextSegment):
    def __init__(self, ref):
        self.ref = ref
        color, txt = get_ref_color_and_title(ref)
        super().__init__(txt, color)

    def handle_right_click(self, offset, mouse_x, mouse_y, clicked_idx, click_x) -> bool:
        if self.ref['type'] == 'heads':
            return Gitkcli.get_view('context-menu').show_context_menu(mouse_x, mouse_y, RefListItem(self.ref), clicked_idx, 'git-refs')
        return False

class SegmentedListItem(Item):
    def __init__(self):
        super().__init__()

    def get_segments(self):
        return []

    def get_text(self):
        text = ''
        for segment in self.get_segments():
            text += segment.get_text()

        return text

    def get_segment_on_offset(self, offset) -> Segment:
        segment_pos = 0
        for segment in self.get_segments():
            length = len(segment.get_text())
            if segment_pos <= offset < segment_pos + length:
                return segment
            segment_pos += length + 1
        return Segment()

    def draw_line(self, win, offset, width, selected, matched, marked):
        first = True
        for segment in self.get_segments():
            if first:
                first = False
            else:
                width -= 1
                win.addstr(' ', curses_color(1, selected, underline = marked))
            length = segment.draw(win, offset, width, selected, matched, marked)
            width -= length
            if width <= 0:
                return
            offset -= len(segment.get_text()) - length

        if selected:
            win.addstr(' ' * width, curses_color(1, selected))
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
            TextSegment(commit['title'])
        ]

        head_position = len(segments) + 1 # +1, because we want to skip 'HEAD ->' segment
        for ref in Gitkcli.refs.get(self.id, []):
            segments.insert(head_position if ref['name'] == Gitkcli.head_branch else len(segments), RefSegment(ref))

        return segments

    def draw_line(self, win, offset, width, selected, matched, marked):
        super().draw_line(win, offset, width, selected, matched, Gitkcli.get_view('git-log').marked_commit_id == self.id)

    def handle_right_click(self, offset, mouse_x, mouse_y, clicked_idx, click_x) -> bool:
        segment = self.get_segment_on_offset(click_x + offset)
        if segment and segment.handle_right_click(offset, mouse_x, mouse_y, clicked_idx, click_x):
            return True
        return super().handle_right_click(offset, mouse_x, mouse_y, clicked_idx, click_x)

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13 or key == 9:
            if Gitkcli.get_view('git-diff').commit_id == self.id:
                Gitkcli.show_view('git-diff')
            else:
                Gitkcli.get_view('git-diff').commit_id = self.id
                Gitkcli.get_job('git-diff').start_show_job(self.id)
                Gitkcli.clear_and_show_view('git-diff')
        else:
            return False
        return True

class View:
    def __init__(self, id, parent_win, title = '', view_position='fullscreen', x=None, y=None, height=None, width=None):
        self.id = id
        self.parent_win = parent_win
        self.view_position = view_position
        self.title = title
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
        if self.title:
            _, cols = self.win.getmaxyx()
            self.win.addstr(0, 0, self.title.ljust(cols), curses_color(7, Gitkcli.get_view() == self))

        self.win.refresh()

class ListView(View):
    def __init__(self, id, parent_win, view_position='fullscreen', title = '', x=None, y=None, height=None, width=None):
        super().__init__(id, parent_win, title, view_position, x, y, height, width)
        self.items = []
        self.selected = 0
        self.offset_y = 0
        self.offset_x = 0

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

    def append(self, item):
        """Add item to end of list"""
        self.items.append(item)
        if len(self.items) - self.offset_y < self.height:
            self.dirty = True
        
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
            if self.items[new_selected].selectable:
                break
            new_selected += direction
            self.dirty = True
            if new_selected < 0 or new_selected >= len(self.items):
                return
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

    def handle_input(self, key):
        if not self.items:
            return False

        offset_jump = int(self.width / 4)

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
        elif key == curses.KEY_MOUSE:
            _, mouse_x, mouse_y, _, mouse_state = curses.getmouse()
            begin_y, begin_x = self.win.getbegyx()
            click_x = mouse_x - begin_x - self.x
            click_y = mouse_y - begin_y - self.y
            if 0 <= click_y < self.height and 0 <= click_x < self.width:
                if mouse_state == curses.BUTTON1_PRESSED or mouse_state == curses.BUTTON1_CLICKED or mouse_state == curses.BUTTON1_DOUBLE_CLICKED or mouse_state == curses.BUTTON3_CLICKED or mouse_state == curses.BUTTON3_PRESSED or mouse_state == curses.BUTTON3_RELEASED:
                    clicked_idx = self.offset_y + click_y
                    if 0 <= clicked_idx < len(self.items) and self.items[clicked_idx].selectable:
                        if mouse_state == curses.BUTTON1_CLICKED or mouse_state == curses.BUTTON1_PRESSED or mouse_state == curses.BUTTON3_RELEASED:
                            self.selected = clicked_idx
                            return self.items[clicked_idx].handle_left_click(self.offset_x, mouse_x, mouse_y)
                        if mouse_state == curses.BUTTON1_DOUBLE_CLICKED:
                            self.selected = clicked_idx
                            return self.items[clicked_idx].handle_double_click(self.offset_x, mouse_x, mouse_y)
                        if mouse_state == curses.BUTTON3_CLICKED or mouse_state == curses.BUTTON3_PRESSED:
                            return self.items[clicked_idx].handle_right_click(self.offset_x, mouse_x, mouse_y, clicked_idx, click_x)
                elif mouse_state == curses.BUTTON4_PRESSED: # wheel up
                    self.offset_y -= 5
                    if self.offset_y < 0:
                        self.offset_y = 0
                elif mouse_state == curses.BUTTON5_PRESSED: # wheel down
                    self.offset_y += 5
                    if self.offset_y >= len(self.items) - self.height:
                        self.offset_y = max(0, len(self.items) - self.height)
            else:
                return self.view_position == 'fullscreen'
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
            return self.items[self.selected].handle_input(key)

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

            if item.separator:
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
        super().__init__(id, parent_win, 'fullscreen', 'Git commit log') 
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
        Gitkcli.get_view('git-log-branch').commit_id = commit_id
        Gitkcli.clear_and_show_view('git-log-branch')
    
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

class GitDiffView(ListView):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win, 'fullscreen', 'Git commit diff') 
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
                    f'{line_number},{line_number}', self.commit_id,
                    '--', file_path]

            result = Gitkcli.run_job(args)
            if result.returncode == 0:
                id = result.stdout.split(' ')[0]
                Gitkcli.get_view('git-log').jump_to_id(id)
                Gitkcli.hide_view()
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
    def __init__(self, text, action, args=None):
        super().__init__(text)
        self.action = action
        self.args = args if args else []

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:
            Gitkcli.hide_view()
            self.action(*self.args)
        else:
            return False
        return True

    def handle_left_click(self, offset, mouse_x, mouse_y):
        self.handle_input(curses.KEY_ENTER)
        return True

class ContextMenu(ListView):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win, 'window')
        
    def show_context_menu(self, mouse_x, mouse_y, item, index, view_id = None):
        self.clear()
        if not view_id:
            view_id = Gitkcli.showed_views[-1]
        view = Gitkcli.get_view()
        if view_id == 'git-log':
            self.append(ContextMenuItem("Create new branch", view.create_branch, [item.id]))
            self.append(ContextMenuItem("Cherry-pick this commit", view.cherry_pick, [item.id]))
            self.append(ContextMenuItem("Revert this commit", view.revert, [item.id]))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Reset here", view.reset, [False, item.id]))
            self.append(ContextMenuItem("Hard reset here", view.reset, [True, item.id]))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Diff this --> selected", view.diff_commits, [item.id, view.get_selected_commit_id()]))
            self.append(ContextMenuItem("Diff selected --> this", view.diff_commits, [view.get_selected_commit_id(), item.id]))
            self.append(ContextMenuItem("Diff this --> marked commit", view.diff_commits, [item.id, view.marked_commit_id]))
            self.append(ContextMenuItem("Diff marked commit --> this", view.diff_commits, [view.marked_commit_id, item.id]))
            self.append(SeparatorItem())
            self.append(ContextMenuItem("Mark this commit", view.mark_commit, [item.id]))
            self.append(ContextMenuItem("Return to mark", view.jump_to_id, [view.marked_commit_id]))
        elif view_id == 'git-diff':
            self.append(ContextMenuItem("Show origin of this line", view.show_origin_of_line, [index]))
            self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard))
        elif view_id == 'git-refs':
            self.append(ContextMenuItem("Check out this branch", self.checkout_branch, [item.data['name']]))
            self.append(ContextMenuItem("Rename this branch", self.rename_branch, [item.data['name']]))
            self.append(ContextMenuItem("Remove this branch", self.remove_branch, [item.data['name']]))
            self.append(ContextMenuItem("Copy branch name", self.copy_branch_name, [item.data['name']]))
        elif view_id == 'log':
            self.append(ContextMenuItem("Copy all to clipboard", view.copy_text_to_clipboard))
        else:
            return False
        self.resize(len(self.items) + 2, 30)
        self.move(mouse_x, mouse_y)
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
        Gitkcli.get_view('git-branch-rename').old_branch_name = branch_name
        Gitkcli.clear_and_show_view('git-branch-rename')
    
    def remove_branch(self, branch_name):
        result = Gitkcli.run_job(['git', 'branch', '-d', branch_name])
        if result.returncode == 0:
            Gitkcli.refresh_refs()
            log_success(f'Deleted branch {branch_name}')
        else:
            log_error(f"Error deleting branch: {result.stderr}")
    
    def copy_branch_name(self, branch_name):
        try:
            import pyperclip
            pyperclip.copy(branch_name)
            log_success(f'Branch name "{branch_name}" copied to clipboard')
        except ImportError:
            log_error("pyperclip module not found. Install with: pip install pyperclip")
        except Exception as e:
            log_error(f"Error copying to clipboard: {str(e)}")

class UserInputDialogPopup(View):
    def __init__(self, id, parent_win, title):
        super().__init__(id, parent_win, title, 'window', height=7, width=80) 
        self.query = ""
        self.cursor_pos = 0
        self.bottom_text = "Enter: Execute | Esc: Cancel"

    def draw_top_panel(self):
        pass

    def execute(self):
        pass

    def draw(self):
        self.draw_top_panel()
        
        # Draw query with cursor
        max_display = self.width - 5
        display_text = self.query
        
        # Adjust scroll position if needed
        start_pos = max(0, self.cursor_pos - max_display)
        if start_pos > 0:
            display_text = "..." + self.query[start_pos:]
            adjusted_cursor_pos = self.cursor_pos - start_pos
        else:
            display_text = self.query
            adjusted_cursor_pos = self.cursor_pos
        
        # Display query
        self.win.addstr(3, 2, display_text[:max_display])
        self.win.clrtoeol()
        
        # Draw help text
        self.win.addstr(5, (self.width - len(self.bottom_text)) // 2, self.bottom_text, curses.A_DIM)
        
        # Draw cursor
        self.win.insch(3, 2 + adjusted_cursor_pos, ' ', curses.A_REVERSE | curses.A_BLINK)

        super().draw()

    def clear(self):
        self.query = ""
        self.cursor_pos = 0

    def handle_input(self, key):
        if key == curses.KEY_ENTER or key == 10 or key == 13:  # Enter key
            Gitkcli.hide_view()
            self.execute()

        elif key == curses.KEY_EXIT or key == 27:  # Escape key
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

class BranchRenameDialogPopup(UserInputDialogPopup):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win, ' Rename Branch') 
        self.old_branch_name = ''

    def draw_top_panel(self):
        self.win.move(1, 2)
        self.win.addstr(f"Rename branch '{self.old_branch_name}' to:")

    def execute(self):
        if not self.query:
            log_error("New branch name cannot be empty")
            return
            
        args = ['git', 'branch', '-m', self.old_branch_name, self.query]
        result = Gitkcli.run_job(args)
        if result.returncode == 0:
            Gitkcli.refresh_refs()
            log_success(f'Branch renamed from {self.old_branch_name} to {self.query}')
        else:
            log_error(f"Error renaming branch: {result.stderr}")

class NewBranchDialogPopup(UserInputDialogPopup):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win, ' New Branch') 
        self.force = False
        self.bottom_text = "Enter: Execute | Esc: Cancel | F1: Force"
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
        result = Gitkcli.run_job(args)
        if result.returncode == 0:
            Gitkcli.refresh_refs()
            log_success(f'Branch {self.query} created successfully')
        else:
            log_error(f"Error creating branch: " + result.stderr)

class SearchDialogPopup(UserInputDialogPopup):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win, ' Search') 
        self.case_sensitive = True
        self.use_regexp = False
        self.bottom_text = "Enter: Search | Esc: Cancel | F1: Case | F2: Regexp"

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
        if key == curses.KEY_DC or key == curses.KEY_BACKSPACE or key == 127 or 32 <= key <= 126:
            Gitkcli.get_parent_view(self.id).dirty = True

        if key == curses.KEY_F1:
            self.case_sensitive = not self.case_sensitive
        elif key == curses.KEY_F2:
            self.use_regexp = not self.use_regexp
        else:
            return super().handle_input(key)
        return True

    def execute(self):
        Gitkcli.get_parent_view(self.id).search_requested(self)

class GitSearchDialogPopup(SearchDialogPopup):
    def __init__(self, id, parent_win):
        super().__init__(id, parent_win) 
        self.search_type = "txt"
        self.bottom_text = "Enter: Search | Esc: Cancel | Tab: Change type | F1: Case | F2: Regexp"

    def matches(self, item):
        if self.search_type == "txt":
            return super().matches(item)
        else:
            return item.id in Gitkcli.found_ids

    def draw_top_panel(self):
        self.win.move(1, 2)
        self.win.addstr("Type: ")
        self.win.addstr("[Txt]", curses_color(1, self.search_type == "txt"))
        self.win.addstr(" ")
        self.win.addstr("[ID]", curses_color(1, self.search_type == "id"))
        self.win.addstr(" ")
        self.win.addstr("[Message]", curses_color(1, self.search_type == "message"))
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
            if self.search_type == "txt":
                return super().handle_input(key)

            Gitkcli.hide_view()

            args = []
            if not self.case_sensitive:
                args.append('-i')
            if self.search_type == "message":
                if not self.use_regexp:
                    args.append('-F')
                args.append("--grep")
                args.append(self.query)
            elif self.search_type == "id":
                args.append(f"{self.query}^!")
            elif self.search_type == "diff":
                if self.use_regexp:
                    args.append("-G")
                else:
                    args.append("-S")
                args.append(self.query)
            elif self.search_type == "path":
                args.append('--')
                args.append(f"*{self.query}*")

            Gitkcli.get_job('git-search').start_job(args)

        elif key == 9:  # Tab key - cycle through search types
            Gitkcli.get_parent_view(self.id).dirty = True
            if self.search_type == "txt":
                self.search_type = "id"
            elif self.search_type == "id":
                self.search_type = "message"
            elif self.search_type == "message":
                self.search_type = "path"
            elif self.search_type == "path":
                self.search_type = "diff"
            else:
                self.search_type = "txt"

        else:
            return super().handle_input(key)
            
        return True


class Gitkcli:

    head_branch = ''
    head_id = ''
    refs = {} # map: git_id --> [ { 'type':<ref-type>, 'name':<ref-name> } ]
    commits = {} # map: git_id --> { parents, date, author, title }
    found_ids = set()

    unlinked_parent_ids = {}

    showed_views = []
    jobs = {}
    views = {}
    running = True

    status_bar_message = ''
    status_bar_color = None
    status_bar_time = time.time()

    @classmethod
    def create_views_and_jobs(cls, stdscr, cmd_args):
        ListView('log', stdscr, title = 'Logs')
        SearchDialogPopup('log-search', stdscr)

        GitLogView('git-log', stdscr)
        GitSearchDialogPopup('git-log-search', stdscr)
        NewBranchDialogPopup('git-log-branch', stdscr)

        GitDiffView('git-diff', stdscr)
        SearchDialogPopup('git-diff-search', stdscr)

        ListView('git-refs', stdscr, title = 'Git references')
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
        if id in cls.showed_views:
            cls.showed_views.remove(id)
        cls.showed_views.append(id)
        cls.get_view().dirty = True

    @classmethod
    def clear_and_show_view(cls, id):
        cls.get_view(id).clear()
        cls.show_view(id)

    @classmethod
    def hide_view(cls):
        if len(cls.showed_views) > 0:
            view_id = cls.showed_views.pop(-1)
            cls.get_view(view_id).win.erase()
            cls.get_view(view_id).win.refresh()
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

        stdscr.addstr(lines-1, 0, f"Line {view.selected+1}/{len(view.items)} - Offset {view.offset_x} - Process '{cls.showed_views[-1]}' {job_status}".ljust(cols - 1), curses_color(7, True))

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
    curses.init_pair(7, curses.COLOR_BLACK, 245)  # Header Footer
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

    curses.init_pair(201, curses.COLOR_BLACK, curses.COLOR_GREEN) # Status bar success
    curses.init_pair(202, curses.COLOR_WHITE, curses.COLOR_RED)   # Status bar error

    # selected colors have offset 100
    curses.init_pair(101, curses.COLOR_WHITE, 235)
    curses.init_pair(102, curses.COLOR_RED, 235)
    curses.init_pair(103, curses.COLOR_GREEN, 235)
    curses.init_pair(104, curses.COLOR_YELLOW, 235)
    curses.init_pair(105, curses.COLOR_BLUE, 235)
    curses.init_pair(106, curses.COLOR_GREEN, 235)
    curses.init_pair(107, curses.COLOR_WHITE, curses.COLOR_BLUE)
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
    stdscr.timeout(5)
    curses.set_escdelay(200)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

    Gitkcli.create_views_and_jobs(stdscr, cmd_args)
    Gitkcli.show_view('git-log')

    log_info('Application started')

    while Gitkcli.running:
        Gitkcli.process_all_jobs()

        try:
            Gitkcli.draw_status_bar(stdscr)
            Gitkcli.draw_visible_views()
        except curses.error as e:
            log_error(f"Curses exception: {str(e)}\n{traceback.format_exc()}")

        stdscr.refresh()

        active_view = Gitkcli.get_view()
        if not active_view:
            break;
        
        key = stdscr.getch()

        if key == curses.KEY_RESIZE:
            lines, cols = stdscr.getmaxyx()
            Gitkcli.resize_all_views(lines, cols)
        elif active_view.handle_input(key):
            active_view.dirty = True
        else:
            if key == ord('q') or key == curses.KEY_EXIT or key == 27 or key == curses.KEY_MOUSE:
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
