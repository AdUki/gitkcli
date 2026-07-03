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
import typing

from gitk.ids import ID_GIT_DIFF, ID_GIT_REFRESH_HEAD, ID_GIT_REFS, ID_GIT_SEARCH
from gitk.items import DiffListItem, RefListItem, StatListItem, TextListItem
from gitk.screen import Screen
from gitk.segmented_items import CommitListItem

# C0/C1 control characters minus tab (0x09, expanded to spaces) and newline
# (0x0a, line-split): stripped from streamed git text for clean display. A
# commit subject / ref name / diff line can contain ESC or other control bytes;
# curses addstr does NOT pass these to the terminal raw — it renders them as
# caret notation (e.g. ESC -> "^[") — so this is display hygiene (drop the
# caret-notation clutter), not a terminal-injection guard.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _git_env():
    """Pin LC_ALL=C so git speaks English: callers parse stdout/stderr to
    detect conditions like "already exists" / "non-fast-forward"."""
    return {**os.environ, "LC_ALL": "C"}


class Job:
    jobs = {}

    @classmethod
    def add_job(cls, id, job):
        if id in cls.jobs:
            cls.jobs[id].stop_job()
        cls.jobs[id] = job

    @classmethod
    def run_job(cls, app, args):
        app.log.info("Run job: " + " ".join(args))
        return subprocess.run(args, capture_output=True, text=True, env=_git_env())

    @classmethod
    def process_all_jobs(cls) -> bool:
        update = False
        for job in cls.jobs.values():
            processed = job.process_items()
            if processed or job.running:
                update = True
        return update

    def __init__(self, app, id: str):
        # The App struct, injected by the owning view at construction. Jobs have
        # no parent chain (they are not items), so they hold app directly.
        self.app = app
        self.id = id
        self.cmd = ""
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
        if message["type"] == "error":
            self.app.log.error(message["message"])
        elif message["type"] == "started":
            self.running = True
        elif message["type"] == "finished":
            self.running = False
            self.app.log.debug(f"Job finished {self.id}")
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
            self.app.log.debug(f"Job stopped {self.id}")

    def start_job(self, args=[], on_finished=None):
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

        self.app.log.info(
            " ".join(["Job started", self.id + ":", self.cmd] + args + self.args)
        )

        self.job = subprocess.Popen(
            self.cmd.split(" ") + args + self.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(),
        )

        stdout_thread = threading.Thread(
            target=self._reader_thread, args=(self.job.stdout, False)
        )
        stderr_thread = threading.Thread(
            target=self._reader_thread, args=(self.job.stderr, True)
        )
        self._reader_threads = [stdout_thread, stderr_thread]
        stdout_thread.start()
        stderr_thread.start()

    def get_exit_code(self):
        return self.job.poll() if self.job else None

    def _reader_thread(self, stream, is_stderr=False):
        if not is_stderr:
            self.messages.put({"type": "started"})
        for bytearr in iter(stream.readline, b""):
            if self.stop:
                break
            try:
                # curses automatically converts tab to spaces, so we will replace it here and cut off newline
                tabsize = curses.get_tabsize() if hasattr(curses, "get_tabsize") else 8
                line = (
                    bytearr.decode("utf-8", errors="replace")
                    .replace("\t", " " * tabsize)
                    .rstrip("\r\n")
                )
                # Strip C0/C1 control chars from streamed git text for a clean
                # display: a commit subject, ref name, or diff line may contain
                # ESC/other control bytes, which curses would render as caret
                # notation ("^[…") rather than pass through raw. Tab is expanded
                # and CR/LF stripped above; printable Unicode (CJK, emoji,
                # box-drawing — all > U+009F) is preserved.
                line = _CONTROL_CHARS.sub("", line)
                if is_stderr:
                    self.messages.put({"type": "error", "message": line})
                else:
                    item = self.process_line(line)
                    if item:
                        self.items.put(item)

            except Exception as e:
                self.messages.put(
                    {
                        "type": "error",
                        "message": f"Error processing line: {bytearr}\n{str(e)}",
                    }
                )
        stream.close()
        if not is_stderr and not self.stop:
            self.messages.put({"type": "finished"})


class GitLogJob(Job):
    def __init__(self, app, id: str, args=[]):
        super().__init__(app, id)
        self.cmd = "git log --format=#%H#%P#%aI#%an#%s"
        self.args = args
        # One-shot: clear the "loading commits" bar once the first row streams
        # in. False here so GitRefreshHeadJob (which shows no bar and skips
        # this class's start_job) never consumes it.
        self._first_item_pending = False

    def start_job(self, args=[], on_finished=None):
        self.app.git_log.commits.clear()
        self._first_item_pending = True
        super().start_job(args, on_finished)

    def process_message(self, message):
        # A repo with no commits yet (fresh `git init`, unborn branch) makes
        # `git log` exit non-zero with this stderr. That's an empty log, not an
        # error — swallow it instead of popping a scary red error dialog.
        if (
            message["type"] == "error"
            and "does not have any commits yet" in message["message"]
        ):
            return
        super().process_message(message)

    def process_line(self, line) -> typing.Any:
        try:
            prefix, id, parents_str, date_str, author, title = line.split("#", 5)
            return (
                id,
                {
                    "prefix": prefix,
                    "parents": parents_str.split(" "),
                    "date": datetime.datetime.fromisoformat(date_str),
                    "author": author,
                    "title": title,
                },
            )
        except ValueError:
            return str(line)

    def process_item(self, item):
        if self._first_item_pending:
            # Rows are flowing: the silent up-front phase (--graph implies
            # --topo-order, computed over the whole set before the first line)
            # is over, so drop the "loading commits" bar.
            self._first_item_pending = False
            self.app.screen.clear_working()
        if isinstance(item, str):
            self.app.git_log.append(TextListItem(item, is_selectable=False))
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

    def start_job(self, args=[], on_finished=None):
        # check if HEAD commit is actually in view
        head_found = False
        for item in self.app.git_log.items:
            if hasattr(item, "id") and item.id == self.app.git_log.head_id:
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
    """Streams one 'git show'/'git diff'/'git cat-file' run (args supplied by
    GitDiffView) and parses each line into Text/Stat/DiffListItem rows. Knows
    nothing about diff modes, titles, jump lists, or scroll positions - that
    is GitDiffView's job (see gitk.diff_target)."""

    def __init__(self, app):
        super().__init__(app, ID_GIT_DIFF)
        self.cmd = "git"

        self.line_pattern = re.compile(
            r"^(?:( )|(?:\+\+\+ b/(.*))|(?:--- a/(.*))|(\+\+\+|---|diff|index)|(\+)|(-)|(@@ -(\d+),\d+ \+(\d+),\d+ @@))"
        )
        # Detects a diffstat line (" path | 5 +-"); the post-rename path used for
        # jump-to-file is reconstructed separately by _stat_file_path.
        self.stat_pattern = re.compile(r" (?:\.\.\.)?(?:.* => )?(.*?)}? +\| +\d+ \+*-*")

        self._reset_parser()

    def _reset_parser(self):
        """Per-run hunk-tracking state, written only by process_line (reader
        thread)."""
        self.old_file_path: typing.Optional[str] = None
        self.old_file_line: int = -1
        self.new_file_path: typing.Optional[str] = None
        self.new_file_line: int = -1
        self.line_count = -1

    def start_job(self, args=[], on_finished=None):
        self._reset_parser()
        super().start_job(args, on_finished)

    @staticmethod
    def _stat_file_path(stat_line):
        """The post-rename file path from a diffstat line, for jump-to-file.

        git renders a rename with a brace group that shares the common
        prefix/suffix ("dir/{old => new}/f") or, with no shared part, as a bare
        "old => new"; reconstruct the *new* path in both cases so the jump target
        matches the diff header (group(1) of stat_pattern cannot — it has already
        swallowed the "{old =>" prefix)."""
        # Drop the " | <count> <+-/Bin>" stat tail (variable spacing around '|').
        m = re.match(r" (.*?) +\| +", stat_line)
        path = m.group(1) if m else stat_line.strip()
        path = re.sub(r"\{.*? => (.*?)\}", r"\1", path)  # dir/{a => b}/f -> dir/b/f
        if " => " in path:
            path = path.rsplit(" => ", 1)[-1]  # a => b -> b
        return path

    def process_line(self, line) -> typing.Any:
        color = Screen.C_NORMAL
        self.line_count += 1

        # 9 capture groups
        match = self.line_pattern.search(line)
        if match:
            if match.group(1):  # code lines, stats and commit message
                if (
                    self.old_file_line < 0 and self.new_file_line < 0
                ):  # commit message or stats line
                    # Diffstat lines are indented with a single space
                    # (" file | 5 ++"); commit-message body lines with four. Only
                    # parse a stat on the former, so a message line that happens to
                    # contain "| N +-" (e.g. a markdown table) is not misread as a
                    # clickable stat row pointing at a bogus file.
                    if line.startswith(" ") and not line.startswith(
                        "    "
                    ):  # stats line
                        color = Screen.C_DIFF_RANGE
                        if self.stat_pattern.match(line):
                            return StatListItem(line, color, self._stat_file_path(line))
                    return TextListItem(line, color)
                self.old_file_line += 1
                self.new_file_line += 1
                old_path, old_line = self.old_file_path, self.old_file_line
                new_path, new_line = self.new_file_path, self.new_file_line
            elif match.group(2):  # '+++' new file
                color = Screen.C_DIFF_INFO
                self.new_file_path = str(match.group(2))
                return TextListItem(line, color)
            elif match.group(3):  # '---' old file
                color = Screen.C_DIFF_INFO
                self.old_file_path = str(match.group(3))
                return TextListItem(line, color)
            elif match.group(4):  # infos
                color = Screen.C_DIFF_INFO
                return TextListItem(line, color)
            elif match.group(5):  # '+' added code lines
                color = Screen.C_DIFF_ADD
                self.new_file_line += 1
                old_path, old_line = None, None
                new_path, new_line = self.new_file_path, self.new_file_line
            elif match.group(6):  # '-' remove code lines
                color = Screen.C_DIFF_DEL
                self.old_file_line += 1
                old_path, old_line = self.old_file_path, self.old_file_line
                new_path, new_line = None, None
            elif match.group(7):  # diff numbers
                color = Screen.C_DIFF_RANGE
                self.old_file_line = int(match.group(8)) - 1
                self.new_file_line = int(match.group(9)) - 1
                old_path, old_line = self.old_file_path, self.old_file_line
                new_path, new_line = self.new_file_path, self.new_file_line
            else:
                return TextListItem(line, color)

            return DiffListItem(
                self.line_count, line, color, old_path, old_line, new_path, new_line
            )

        return TextListItem(line, color)

    def process_item(self, item):
        self.app.git_diff.append(item)


class GitSearchJob(Job):
    def __init__(self, app, args=[]):
        super().__init__(app, ID_GIT_SEARCH)
        self.cmd = "git log --format=%H"
        # CLI revision args (e.g. a branch name). These must precede any
        # '--' pathspec separator added by the search, so keep them out of
        # self.args (which the base class appends *after* the per-search args)
        # and instead prepend them to args in start_job.
        self.revisions = args
        self.found_ids = set()

    def start_job(self, args=[], on_finished=None):
        self.found_ids.clear()
        self.app.git_log.dirty = True
        super().start_job(self.revisions + args, on_finished)

    def process_item(self, item):
        self.found_ids.add(item)
        self.app.git_log.dirty = True


class GitRefsJob(Job):
    def __init__(self, app):
        super().__init__(app, ID_GIT_REFS)
        self.cmd = "git show-ref --head --dereference"

    def start_job(self, args=[], on_finished=None):
        self.app.git_refs.refs.clear()

        self.app.git_log.head_branch = Job.run_job(
            self.app, ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        ).stdout.rstrip()
        if self.app.git_log.head_branch == "HEAD":
            self.app.git_log.head_branch = ""

        super().start_job(args, on_finished)

    def process_line(self, line) -> typing.Any:
        id, value = tuple(line.split(" "))
        if value == "HEAD":
            return {"id": id, "name": value, "type": "head"}
        parts = value.split("/", 2)
        return {
            "id": id,
            "type": parts[1],
            "name": parts[1] if len(parts) == 2 else parts[2],
        }

    def process_item(self, item):
        id = item["id"]

        if item["type"] == "tags" and item["name"].endswith("^{}"):
            # process link to annotated tag
            last_item_data = self.app.git_refs.items[-1].data
            last_item_data["tag_id"] = last_item_data["id"]
            last_item_data["id"] = id
            item = last_item_data
        else:
            self.app.git_refs.append(RefListItem(item))

        self.app.git_refs.refs.setdefault(id, []).append(item)
        self.app.git_log.dirty = True
        if item["type"] == "head":
            self.app.git_log.head_id = id
            self.app.git_log.focus_head_if_pending()
            self.app.git_log._place_uncommitted_rows()
