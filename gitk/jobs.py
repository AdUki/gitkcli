"""Background git jobs.

`Job` is the base (a worker thread streaming output lines back to the UI through
a queue) plus a small registry; the Git*Job subclasses drive the log, diff,
refs, and search views. Jobs hold the App struct (injected by the owning view)
and append result rows as the item types defined in gitk.items.
"""

from __future__ import annotations

import curses
import datetime
import os
import queue
import re
import subprocess
import threading

from gitk.ids import (ID_GIT_DIFF, ID_GIT_REFS, ID_GIT_SEARCH,
                      ID_GIT_REFRESH_HEAD)
from gitk.items import TextListItem, RefListItem, DiffListItem, StatListItem
from gitk.segmented_items import CommitListItem

# C0/C1 control characters minus tab (0x09, expanded to spaces) and newline
# (0x0a, line-split): stripped from all streamed git text so a crafted commit
# subject / ref name / diff line can't inject terminal escape sequences.
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')

class Job:

    jobs = {}

    @classmethod
    def add_job(cls, id, job):
        if id in cls.jobs:
            cls.jobs[id].stop_job()
        cls.jobs[id] = job

    @classmethod
    def run_job(cls, app, args):
        app.log.info('Run job: ' + ' '.join(args))
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

    def __init__(self, app, id:str):
        # The App struct, injected by the owning view at construction. Jobs have
        # no parent chain (they are not items), so they hold app directly.
        self.app = app
        self.id = id
        self.cmd = ''
        self.args = []
        self.job = None
        self.running = False
        self.stop = False
        self.items = queue.Queue()
        self.messages = queue.Queue()
        self.on_finished = None
        self._reader_threads = []
        Job.add_job(id, self)

    def process_line(self, line) -> typing.Any:
        return line

    def process_item(self, item):
        # This should be implemented by derived classes
        pass

    def process_message(self, message):
        if message['type'] == 'error':
            self.app.log.error(message['message'])
        elif message['type'] == 'started':
            self.running = True
        elif message['type'] == 'finished':
            self.running = False
            self.app.log.debug(f'Job finished {self.id}')
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

    @staticmethod
    def _empty_queue(q):
        try:
            while True:
                q.get_nowait()
                q.task_done()
        except queue.Empty:
            pass

    def stop_job(self):
        self.stop = True
        # A stopped job is no longer running. The reader thread breaks on
        # self.stop before emitting 'finished', so process_message would never
        # clear `running` — leaving process_all_jobs reporting perpetual updates
        # (a busy 5ms redraw loop) for a stopped-but-not-restarted job.
        self.running = False
        self.on_finished = None
        if self.job and self.get_exit_code() is None:
            self.job.terminate()
            try:
                self.job.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.job.kill()
            self.app.log.debug(f'Job stopped {self.id}')

    def start_job(self, args = [], on_finished = None):
        self.stop_job()

        # `self.stop` is still True here (set by stop_job). The previous run's
        # reader threads exit once its terminated streams hit EOF; join them
        # while stop is still set so they neither emit a stale 'finished' nor
        # race the queue clear below. Then discard anything the old run queued
        # so it can't bleed into the freshly-(re)loaded view.
        for thread in self._reader_threads:
            thread.join(timeout=1)
        self._reader_threads = []
        self._empty_queue(self.items)
        self._empty_queue(self.messages)

        self.stop = False
        self.on_finished = on_finished

        self.app.log.info(' '.join(['Job started', self.id + ':', self.cmd] + args + self.args))

        # Pin LC_ALL=C (same convention as run_job) so git speaks English: the
        # commit --format output is locale-independent, but stderr conditions
        # (e.g. an unborn branch) are parsed by callers.
        self.job = subprocess.Popen(
                self.cmd.split(' ') + args + self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, 'LC_ALL': 'C'})

        stdout_thread = threading.Thread(target=self._reader_thread, args=(self.job.stdout, False))
        stderr_thread = threading.Thread(target=self._reader_thread, args=(self.job.stderr, True))
        self._reader_threads = [stdout_thread, stderr_thread]
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
                # Strip C0/C1 control chars (terminal-escape injection guard): a
                # commit subject, ref name, or diff line could embed ANSI escapes
                # / cursor moves that would otherwise be written straight to the
                # terminal. Tab is expanded and CR/LF stripped above; printable
                # Unicode (CJK, emoji, box-drawing — all > U+009F) is preserved.
                line = _CONTROL_CHARS.sub('', line)
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
    def __init__(self, app, id:str, args = []):
        super().__init__(app, id)
        self.cmd = 'git log --format=#%H#%P#%aI#%an#%s'
        self.args = args

    def start_job(self, args = [], on_finished = None):
        self.app.git_log.commits.clear()
        super().start_job(args, on_finished)

    def process_message(self, message):
        # A repo with no commits yet (fresh `git init`, unborn branch) makes
        # `git log` exit non-zero with this stderr. That's an empty log, not an
        # error — swallow it instead of popping a scary red error dialog.
        if message['type'] == 'error' and 'does not have any commits yet' in message['message']:
            return
        super().process_message(message)

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
            self.app.git_log.append(TextListItem(item, selectable = False))
        else:
            id, commit = item
            if self.app.git_log.add_commit(id, commit):
                self.app.git_log.append(CommitListItem(id))
                self.app.git_log.select_if_pending(id)
                if id == self.app.git_log.head_id:
                    self.app.git_log.focus_head_if_pending()
                    self.app.git_log._place_uncommitted_rows()

class GitRefreshHeadJob(GitLogJob):
    def __init__(self, app):
        super().__init__(app, ID_GIT_REFRESH_HEAD, [])

    def start_job(self, args = [], on_finished = None):
        # check if HEAD commit is actually in view
        head_found = False
        for item in self.app.git_log.items:
            if hasattr(item, 'id') and item.id == self.app.git_log.head_id:
                head_found = True
                break
        if not head_found:
            # no HEAD commit found, don't do anything
            return

        # skip calling self.app.git_log.commits.clear()
        Job.start_job(self, args, on_finished) 

    def process_item(self, item):
        (id, commit) = item
        if self.app.git_log.add_commit(id, commit):
            self.app.git_log.prepend_commit(CommitListItem(id))
            if id == self.app.git_log.head_id:
                self.app.git_log._place_uncommitted_rows()

class GitDiffJob(Job):
    def __init__(self, app):
        super().__init__(app, ID_GIT_DIFF)
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

        args.extend([f'-U{self.app.git_diff.context_size}', f'--stat={self.app.git_diff.width}', '--no-color', f'-l{self.app.git_diff.rename_limit}'])

        if self.app.git_diff.ignore_whitespace:
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
        return (lambda: self.app.git_diff.restore_view_position(*entry)) if entry else None

    def _prepare(self, title, *, is_diff, view_commit_id, commit_id=None, tag_id=None,
                 old_commit_id=None, new_commit_id=None, cached=False):
        """Reset the job target and the diff view before starting a show_* job."""
        self.commit_id = commit_id
        self.tag_id = tag_id
        self.cached = cached
        self.old_commit_id = old_commit_id
        self.new_commit_id = new_commit_id
        self.app.git_diff.clear()
        self.app.git_diff.commit_id = view_commit_id
        self.app.git_diff.is_diff = is_diff
        self.app.git_diff.header_item.set_title(title)

    def show_diff(self, old_commit_id, new_commit_id = None, cached = False, title = None,
                  view_id = None, add_to_jump_list = False):
        if not title:
            title = f'Diff {old_commit_id[:7]} {new_commit_id[:7]}'
        self._prepare(title, is_diff=True, view_commit_id=view_id or old_commit_id,
                      old_commit_id=old_commit_id, new_commit_id=new_commit_id, cached=cached)
        self.start_job(self._get_args(), on_finished=self._restore_on_finished(view_id))
        if add_to_jump_list and view_id:
            self.app.git_log.add_to_jump_list(view_id)

    def show_commit(self, commit_id, on_finished = None, add_to_jump_list = True):
        self._prepare(f'Commit {commit_id[:7]}', is_diff=False, view_commit_id=commit_id,
                      commit_id=commit_id)
        if on_finished is None:
            on_finished = self._restore_on_finished(commit_id)
        self.start_job(self._get_args(), on_finished=on_finished)
        if add_to_jump_list:
            self.app.git_log.add_to_jump_list(commit_id)

    def show_tag_annotation(self, tag_id):
        self._prepare(f'Tag {tag_id}', is_diff=True, view_commit_id=tag_id, tag_id=tag_id)
        self.start_job(self._get_args())
        self.app.git_diff.show()

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
        self.app.git_diff.append(item)

class GitSearchJob(Job):
    def __init__(self, app, args = []):
        super().__init__(app, ID_GIT_SEARCH)
        self.cmd = 'git log --format=%H'
        # CLI revision args (e.g. a branch name). These must precede any
        # '--' pathspec separator added by the search, so keep them out of
        # self.args (which the base class appends *after* the per-search args)
        # and instead prepend them to args in start_job.
        self.revisions = args
        self.found_ids = set()

    def start_job(self, args = [], on_finished = None):
        self.found_ids.clear()
        self.app.git_log.dirty = True
        super().start_job(self.revisions + args, on_finished)

    def process_item(self, item):
        self.found_ids.add(item)
        self.app.git_log.dirty = True

class GitRefsJob(Job):
    def __init__(self, app):
        super().__init__(app, ID_GIT_REFS) 
        self.cmd = 'git show-ref --head --dereference'

    def start_job(self, args = [], on_finished = None):
        self.app.git_refs.refs.clear()

        self.app.git_log.head_branch = Job.run_job(self.app, ['git', 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.rstrip()
        if self.app.git_log.head_branch == 'HEAD': self.app.git_log.head_branch = ''

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
            last_item_data = self.app.git_refs.items[-1].data
            last_item_data['tag_id'] = last_item_data['id']
            last_item_data['id'] = id
            item = last_item_data
        else:
            self.app.git_refs.append(RefListItem(item))

        self.app.git_refs.refs.setdefault(id,[]).append(item)
        self.app.git_log.dirty = True
        if item['type'] == 'head':
            self.app.git_log.head_id = id
            self.app.git_log.focus_head_if_pending()
            self.app.git_log._place_uncommitted_rows()
