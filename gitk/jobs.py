"""Background git jobs.

`Job` is the base (a worker thread streaming output lines back to the UI through
a queue) plus a small registry; the Git*Job subclasses drive the log, diff,
refs, and search views. Jobs hold the App struct (injected by the owning view)
and append result rows as the item types defined in gitk.items.
"""

from __future__ import annotations

import curses
import datetime
import gc
import os
import queue
import re
import subprocess
import threading
import time
import typing

from gitk.ids import ID_GIT_DIFF, ID_GIT_REFRESH_HEAD, ID_GIT_REFS, ID_GIT_SEARCH
from tuikit.items import TextListItem

from gitk.items import DiffListItem, RefListItem, StatListItem
from gitk.screen import Screen
from gitk.segmented_items import CommitListItem

# C0/C1 control characters minus tab (0x09, expanded to spaces) and newline
# (0x0a, line-split): stripped from streamed git text for clean display. A
# commit subject / ref name / diff line can contain ESC or other control bytes;
# curses addstr does NOT pass these to the terminal raw — it renders them as
# caret notation (e.g. ESC -> "^[") — so this is display hygiene (drop the
# caret-notation clutter), not a terminal-injection guard.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


# How long one main-loop pass may spend on a job's queued rows before it goes
# back to the keyboard and screen: a big backlog (a fast `git log` on a large
# repository) is worked off over several passes instead of holding a key back.
DRAIN_BUDGET = 0.010


def display_line(bytearr: bytes) -> str:
    """A line of git output as shown: decoded, tabs expanded (curses would
    expand them anyway), the line break cut off.

    C0/C1 control characters are stripped for a clean display: a commit
    subject, ref name, or diff line may contain ESC/other control bytes, which
    curses would render as caret notation ("^[…") rather than pass through
    raw. Printable Unicode (CJK, emoji, box-drawing - all > U+009F) is
    preserved."""
    tabsize = curses.get_tabsize() if hasattr(curses, "get_tabsize") else 8
    line = (
        bytearr.decode("utf-8", errors="replace")
        .replace("\t", " " * tabsize)
        .rstrip("\r\n")
    )
    return _CONTROL_CHARS.sub("", line)


def raw_line(bytearr: bytes) -> str:
    """A line of git output exactly as git wrote it, minus the final newline
    (a CR stays): what a patch built from the shown diff must contain.
    Undecodable bytes survive as surrogates (encode with surrogateescape)."""
    if bytearr.endswith(b"\n"):
        bytearr = bytearr[:-1]
    return bytearr.decode("utf-8", errors="surrogateescape")


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
    def run_job(cls, app, args, input=None):
        """Run a command to completion (blocking). `input` (str) is fed to
        its stdin."""
        app.log.info("Run job: " + " ".join(args))
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            env=_git_env(),
            input=input,
            encoding="utf-8",
            errors="surrogateescape",
        )

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
        pass  # subclasses override

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

    def _drain(self, q, handler, deadline=None) -> typing.Tuple[bool, bool]:
        """Drain a queue, dispatching each truthy item to handler (skipped while
        stopped), until it is empty or time.monotonic() passes `deadline`.
        Returns (anything processed, stopped at the deadline)."""
        processed = False
        try:
            while True:
                if deadline is not None and time.monotonic() > deadline:
                    return processed, True
                item = q.get_nowait()
                q.task_done()
                if not item:
                    break
                if not self.stop:
                    handler(item)
                    processed = True
        except queue.Empty:
            pass
        return processed, False

    def process_items(self) -> bool:
        drained_items, more = self._drain(
            self.items, self.process_item, time.monotonic() + DRAIN_BUDGET
        )
        if more:
            # Messages wait for the rows: "finished" is queued after the last
            # one, and must not be handled while rows are still pending.
            return True
        drained_msgs, _ = self._drain(self.messages, self.process_message)
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

        tail = self.command_args()
        self.app.log.info(
            " ".join(["Job started", self.id + ":", self.cmd] + args + tail)
        )

        self.job = subprocess.Popen(
            self.cmd.split(" ") + args + tail,
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

    def command_args(self) -> typing.List[str]:
        """The arguments appended after the per-run ones (default: self.args)."""
        return self.args

    def _stdout_line(self, bytearr: bytes):
        """Parse one line of the command's output into a queued row."""
        try:
            item = self.process_line(display_line(bytearr))
            if item:
                self.items.put(item)
        except Exception as e:
            self.messages.put(
                {
                    "type": "error",
                    "message": f"Error processing line: {bytearr}\n{str(e)}",
                }
            )

    def get_exit_code(self):
        return self.job.poll() if self.job else None

    def _reader_thread(self, stream, is_stderr=False):
        if not is_stderr:
            self.messages.put({"type": "started"})
        for bytearr in iter(stream.readline, b""):
            if self.stop:
                break
            if is_stderr:
                self.messages.put({"type": "error", "message": display_line(bytearr)})
            else:
                self._stdout_line(bytearr)
        stream.close()
        if not is_stderr and not self.stop:
            self.messages.put({"type": "finished"})


def _parse_date(date_str: str) -> datetime.datetime:
    """An ISO date from git. A few real commits carry an impossible timezone
    (the kernel has "+80:00"); show those with the offset dropped."""
    try:
        return datetime.datetime.fromisoformat(date_str)
    except ValueError:
        return datetime.datetime.fromisoformat(date_str[:19])


# Commits loaded between two gc.freeze() calls (see GitLogJob._freeze_loaded).
FREEZE_EVERY = 20000


class GitLogJob(Job):
    def __init__(self, app, id: str, args=[]):
        super().__init__(app, id)
        self._unfrozen = 0
        self.cmd = "git log --format=%H#%P#%ct#%aI#%an#%s"
        self.args = args
        # One-shot: clear the "loading commits" bar once the first row streams
        # in. False here so GitRefreshHeadJob (which shows no bar and skips
        # this class's start_job) never consumes it.
        self._first_item_pending = False

    def start_job(self, args=[], on_finished=None):
        self.app.git_log.commits.clear()
        self._first_item_pending = True
        super().start_job(args, on_finished)

    def command_args(self) -> typing.List[str]:
        # In graph mode the lanes are ours (gitk.graph), so git must not draw
        # them: plain `git log` streams at once, where --graph (implying
        # --topo-order) first walks the whole history. --parents makes git
        # rewrite the parents of a path-limited log onto the shown commits.
        if self.app.git_log.graph is not None:
            return [a for a in self.args if a != "--graph"] + ["--parents"]
        return self.args

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
        if message["type"] == "finished" and self.app.git_log.graph is not None:
            self.app.git_log.graph_finished()

    def process_line(self, line) -> typing.Any:
        try:
            id, parents_str, ctime, date_str, author, title = line.split("#", 5)
            return (
                id,
                {
                    "parents": parents_str.split(),
                    "ctime": int(ctime),
                    "date": _parse_date(date_str),
                    "author": author,
                    "title": title,
                },
            )
        except ValueError:
            return str(line)

    def _freeze_loaded(self):
        """Keep the garbage collector off the loaded commits.

        A full (oldest-generation) collection walks every tracked object, and
        Python runs one each time that generation has grown by a quarter. A
        large history is millions of commit dicts, parent lists and graph
        entries, so those passes grew to 300+ ms stalls of the UI thread, with
        keys left unanswered, a dozen times per load. The commits hold no
        reference cycles (refcounting frees them on reload), so collecting
        the young generations and then freezing the survivors - moving them
        out of the collector's sight - loses nothing, and every collection
        after that only walks what was allocated since."""
        self._unfrozen += 1
        if self._unfrozen >= FREEZE_EVERY:
            self._unfrozen = 0
            gc.collect(1)
            gc.freeze()

    def process_item(self, item):
        self._freeze_loaded()
        if self._first_item_pending:
            # Rows are flowing: the silent up-front phase (--graph implies
            # --topo-order, computed over the whole set before the first line)
            # is over, so drop the "loading commits" bar.
            self._first_item_pending = False
            self.app.screen.clear_working()
        if isinstance(item, str):
            if self.app.git_log.graph is not None:
                # the rows are the graph's: a line we cannot parse has no place
                self.app.log.warning(f"Unparsed git log line: {item}")
            else:
                self.app.git_log.append(TextListItem(item, is_selectable=False))
        else:
            id, commit = item
            if self.app.git_log.add_commit(id, commit):
                if self.app.git_log.graph is None:
                    self.app.git_log.append(CommitListItem(id))
                self.app.git_log.select_if_pending(id)
                if id == self.app.git_log.head_id:
                    self.app.git_log.focus_head_if_pending()
                    self.app.git_log._place_uncommitted_rows()


class GitRefreshHeadJob(GitLogJob):
    def __init__(self, app):
        super().__init__(app, ID_GIT_REFRESH_HEAD, [])

    def start_job(self, args=[], on_finished=None):
        log = self.app.git_log
        if log.head_id not in log.commits:
            return  # nothing to refresh onto
        # Skip GitLogJob.start_job (which clears commits): this appends to the view.
        Job.start_job(self, args, on_finished)

    def process_message(self, message):
        Job.process_message(self, message)

    def process_item(self, item):
        (id, commit) = item
        if self.app.git_log.add_commit(id, commit):
            if self.app.git_log.graph is None:
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
        # While not None, rows are collected here instead of being appended
        # to the view: a reload in place swaps them in once complete (see
        # start_steps(buffered=True)).
        self.buffer = None

        # Lines outside a hunk: file headers, the hunk header itself, and (with
        # nothing matched) the commit message / diffstat. Lines INSIDE a hunk
        # never reach this pattern - see process_line.
        self.line_pattern = re.compile(
            r"^(?:( )|(?:\+\+\+ (?:b/(.*)|/dev/null))|(?:--- (?:a/(.*)|/dev/null))|(\+\+\+|---|diff|index)|(\+)|(-)|(@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@))"
        )
        # Detects a diffstat line (" path | 5 +-"); the post-rename path used for
        # jump-to-file is reconstructed separately by _stat_file_path.
        self.stat_pattern = re.compile(r" (?:\.\.\.)?(?:.* => )?(.*?)}? +\| +\d+ \+*-*")

        self._reset_parser()

    def _reset_parser(self):
        """Per-run parser state, written only by the reader thread."""
        self.line_count = -1
        # The section (WorktreeTarget.section) the rows belong to while a
        # sequence of steps runs (start_steps); "" for a plain run.
        self.section = ""
        self._section_rows = 0
        self._reset_hunk_state()

    def _reset_hunk_state(self):
        """Hunk-tracking state, reset per run and per step of a sequence."""
        self.old_file_path: typing.Optional[str] = None
        self.old_file_line: int = -1
        self.new_file_path: typing.Optional[str] = None
        self.new_file_line: int = -1
        # Lines still owed by the current hunk, per its "@@ -o,N +n,M @@"
        # header. While either is non-zero a line is hunk content whatever it
        # looks like, so a patch file's own "+++ b/x" / "--- a/x" / "-- " lines
        # are not mistaken for this diff's headers.
        self.old_remaining = 0
        self.new_remaining = 0
        # Whether the current hunk belongs to a patch file, whose content is
        # itself a diff and gets its own colours (see _nested_color).
        self.nested = False

    def start_job(self, args=[], on_finished=None):
        self._reset_parser()
        self.buffer = None
        super().start_job(args, on_finished)

    def _stdout_line(self, bytearr: bytes):
        # Rows keep the line exactly as git wrote it (`raw`: tabs, CR, any
        # bytes) - a patch built from the shown diff must match the file - and
        # the section of a sequence they belong to.
        try:
            item = self.process_line(display_line(bytearr))
            if item:
                item.raw = raw_line(bytearr)
                item.section = self.section
                self._section_rows += 1
                self.items.put(item)
        except Exception as e:
            self.messages.put(
                {
                    "type": "error",
                    "message": f"Error processing line: {bytearr}\n{str(e)}",
                }
            )

    # -- a sequence of steps (the uncommitted-changes view) --------------------

    def start_steps(self, steps, cwd=None, on_finished=None, buffered=False):
        """Run `steps` one after another in one background thread, streaming
        every output line into the view as one list. A step is
        ("section", id) - the rows that follow belong to section `id` -
        ("git", argv) - a command, its output parsed like a single run - or
        ("untracked", diff_flags) - every untracked file as a new-file diff. Commands run in `cwd`. `buffered` collects
        the rows in `self.buffer` for `on_finished` to swap in, instead of
        streaming them into the view."""
        self.stop_job()
        for thread in self._reader_threads:
            thread.join(timeout=1)
        self._empty_queue(self.items)
        self._empty_queue(self.messages)
        self._reset_parser()
        self.buffer = [] if buffered else None
        self.stop = False
        self.on_finished = on_finished
        self.app.log.info(
            f"Job started {self.id}: "
            + "; ".join(" ".join(step[1]) for step in steps if step[0] == "git")
        )
        thread = threading.Thread(target=self._steps_thread, args=(steps, cwd))
        self._reader_threads = [thread]
        thread.start()

    def _steps_thread(self, steps, cwd):
        self.messages.put({"type": "started"})
        for step in steps:
            if self.stop:
                return
            kind = step[0]
            if kind == "section":
                self._end_section()
                self.section = step[1]
                self._section_rows = 0
            elif kind == "git":
                self._run_step(step[1], cwd)
            elif kind == "untracked":
                listing = subprocess.run(
                    ["git", "ls-files", "--others", "--exclude-standard", "-z"],
                    capture_output=True,
                    env=_git_env(),
                    cwd=cwd,
                )
                paths = [os.fsdecode(p) for p in listing.stdout.split(b"\0") if p]
                self._untracked_list(paths)
                for path in paths:
                    if self.stop:
                        return
                    args = ["git", "diff", "--no-index", *step[1], "--"]
                    args += ["/dev/null", path]
                    # --no-index exits 1 when the files differ
                    self._run_step(args, cwd, ok=(0, 1))
        self._end_section()
        if not self.stop:
            self.messages.put({"type": "finished"})

    def _put_row(self, row, count=True):
        """Queue a row of our own (not parsed from git output)."""
        self.line_count += 1
        row.section = self.section
        row.raw = ""
        self.items.put(row)
        if count:
            self._section_rows += 1

    def _untracked_list(self, paths):
        """The untracked files, listed like a diffstat (each row jumps to /
        stages its file) under a summary row, before their diffs."""
        if not paths:
            return
        if self._section_rows:
            self._put_row(TextListItem(""))
        for path in paths:
            self._put_row(
                StatListItem(f" {path} (untracked)", Screen.C_DIFF_RANGE, path)
            )
        n = len(paths)
        self._put_row(TextListItem(f" {n} untracked file{'s' if n > 1 else ''}"))
        self._put_row(TextListItem(""))

    def _end_section(self):
        """An empty section says so."""
        if self.section and not self._section_rows:
            row = TextListItem("  No changes", Screen.C_DIM, is_selectable=False)
            self._put_row(row)

    def _run_step(self, args, cwd, ok=(0,)):
        self._reset_hunk_state()
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_git_env(),
                cwd=cwd,
            )
        except OSError as e:
            self.messages.put({"type": "error", "message": str(e)})
            return
        self.job = proc
        for bytearr in iter(proc.stdout.readline, b""):
            if self.stop:
                break
            self._stdout_line(bytearr)
        proc.stdout.close()
        err = proc.stderr.read()
        proc.stderr.close()
        if proc.wait() not in ok and not self.stop:
            for line in err.splitlines():
                if line.strip():
                    self.messages.put({"type": "error", "message": display_line(line)})

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

    # A changed file whose content is a diff itself (a quilt / Yocto / kernel
    # patch). Its lines carry two markers: column 0 is this diff's, the rest
    # is a line of the patch.
    _PATCH_SUFFIXES = (".patch", ".diff")

    @staticmethod
    def _nested_color(text):
        """Colour for a patch file's line once this diff's marker is cut off:
        the same scheme as the outer diff, applied to the inner one."""
        if text.startswith("@@"):
            return Screen.C_DIFF_RANGE
        if text.startswith(
            ("diff ", "index ", "--- a/", "+++ b/", "--- /dev/null", "+++ /dev/null")
        ):
            return Screen.C_DIFF_INFO
        # format-patch's message/diffstat separator and mail signature line.
        if text.rstrip() in ("---", "--"):
            return Screen.C_NORMAL
        if text.startswith("+"):
            return Screen.C_DIFF_ADD
        if text.startswith("-"):
            return Screen.C_DIFF_DEL
        return Screen.C_NORMAL

    def process_line(self, line) -> typing.Any:
        self.line_count += 1
        if self.old_remaining > 0 or self.new_remaining > 0:
            item = self._hunk_line(line)
            if item:
                return item
            # Not a hunk line although the header promised more (a truncated
            # or hand-edited diff): stop trusting the counts.
            self.old_remaining = self.new_remaining = 0
        return self._header_line(line)

    def _hunk_line(self, line):
        """A line inside a hunk, classified by its first column alone."""
        marker = line[:1]
        old_path, old_line = self.old_file_path, None
        new_path, new_line = self.new_file_path, None
        if marker in (" ", ""):  # context ("" when trailing space was lost)
            color = Screen.C_NORMAL
            self.old_remaining -= 1
            self.new_remaining -= 1
            self.old_file_line += 1
            self.new_file_line += 1
            old_line, new_line = self.old_file_line, self.new_file_line
        elif marker == "+":
            color = Screen.C_DIFF_ADD
            self.new_remaining -= 1
            self.new_file_line += 1
            old_path, new_line = None, self.new_file_line
        elif marker == "-":
            color = Screen.C_DIFF_DEL
            self.old_remaining -= 1
            self.old_file_line += 1
            old_line, new_path = self.old_file_line, None
        elif marker == "\\":  # "\ No newline at end of file"
            return TextListItem(line, Screen.C_NORMAL)
        else:
            return None
        inner, dim = None, False
        if self.nested:
            inner = self._nested_color(line[1:])
            if inner == Screen.C_NORMAL and marker in ("+", "-"):
                # A plain line of the patch (its context, message text) added
                # or removed here: draw the whole line in dimmed red/green, so
                # it reads as changed without competing with the patch's own
                # +/- lines.
                inner, dim = None, True
        return DiffListItem(
            self.line_count,
            line,
            color,
            old_path,
            old_line,
            new_path,
            new_line,
            inner_color=inner,
            dim=dim,
        )

    def _header_line(self, line):
        """A line outside any hunk: commit message, diffstat, file headers or
        a hunk header."""
        color = Screen.C_NORMAL
        match = self.line_pattern.search(line)
        if not match:
            return TextListItem(line, color)
        if match.group(1):  # commit message and stats lines
            # Diffstat lines are indented with a single space (" file | 5 ++");
            # commit-message body lines with four. Only parse a stat on the
            # former, so a message line that happens to contain "| N +-" (e.g.
            # a markdown table) is not misread as a clickable stat row pointing
            # at a bogus file.
            if line.startswith(" ") and not line.startswith("    "):  # stats line
                color = Screen.C_DIFF_RANGE
                if self.stat_pattern.match(line):
                    return StatListItem(line, color, self._stat_file_path(line))
            return TextListItem(line, color)
        if line.startswith("+++ "):  # new file ("/dev/null" when deleted)
            self.new_file_path = match.group(2)
            return TextListItem(line, Screen.C_DIFF_INFO)
        if line.startswith("--- "):  # old file ("/dev/null" when added)
            self.old_file_path = match.group(3)
            return TextListItem(line, Screen.C_DIFF_INFO)
        if match.group(4):  # other headers
            if line.startswith("diff "):
                # A new file starts; its ---/+++ lines (absent for a binary
                # or mode-only change) name it.
                self.old_file_path = self.new_file_path = None
            return TextListItem(line, Screen.C_DIFF_INFO)
        if match.group(7):  # hunk header
            self.old_file_line = int(match.group(8)) - 1
            self.new_file_line = int(match.group(10)) - 1
            # A count is omitted when it is 1.
            self.old_remaining = int(match.group(9) or 1)
            self.new_remaining = int(match.group(11) or 1)
            self.nested = any(
                p and p.endswith(self._PATCH_SUFFIXES)
                for p in (self.new_file_path, self.old_file_path)
            )
            return DiffListItem(
                self.line_count,
                line,
                Screen.C_DIFF_RANGE,
                self.old_file_path,
                self.old_file_line,
                self.new_file_path,
                self.new_file_line,
            )
        # A stray +/- line outside a hunk (not expected from git).
        color = Screen.C_DIFF_ADD if match.group(5) else Screen.C_DIFF_DEL
        return TextListItem(line, color)

    def process_item(self, item):
        if self.buffer is not None:
            self.buffer.append(item)
        else:
            self.app.git_diff.append(item)


class GitSearchJob(Job):
    def __init__(self, app):
        super().__init__(app, ID_GIT_SEARCH)
        self.cmd = "git log --format=%H"
        self.found_ids = set()

    def start_job(self, args=[], on_finished=None, scoped=True):
        """`args` are the per-search options (and an optional `-- <path>`).
        When `scoped`, GitLogView.search_args wraps them with the revisions the
        log view shows, so the search covers the commits on screen; an [ID]
        search (`<id>^!`) names its one commit itself and must not be scoped -
        its `^<parent>` exclusion would apply to the extra revisions too."""
        self.found_ids.clear()
        self.app.git_log.dirty = True
        if scoped:
            args = self.app.git_log.search_args(args)
        super().start_job(args, on_finished)

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
        id, value = line.split(" ")
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
