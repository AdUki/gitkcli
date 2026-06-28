#!/usr/bin/env python3
"""Golden-screen integration test runner for gitkcli.

Each case lives in cases/<name>/ with a spec.txt (see dsl.py) and golden/*.txt
snapshots. For every case the runner:

  1. copies the pristine test/repo into a throwaway work dir (clean tree, no
     cross-test leakage) and copies the chosen config template into an isolated
     XDG_CONFIG_HOME (so the developer's real preferences never bleed in),
  2. launches the real app on a fixed-size pty (test/harness.py),
  3. replays the spec, capturing the rendered screen at each `capture` point,
  4. compares each capture to its golden (or rewrites goldens with --update).

Usage:
  python3 test/run.py                 # run all cases
  python3 test/run.py -u              # (re)generate goldens
  python3 test/run.py --filter refs   # only matching cases
  python3 test/run.py --live diff_view  # replay then hand over the
                                                    # real terminal for debugging
"""

import argparse
import difflib
import os
import shutil
import subprocess
import sys
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dsl  # noqa: E402

try:
    from harness import Harness, ESC_GAP  # noqa: E402
except ImportError as e:
    sys.exit(f"cannot import harness ({e}); is pyte installed? "
             f"pip install -r {os.path.join(HERE, 'requirements.txt')}")

ROOT = os.path.dirname(HERE)
GITKCLI = os.path.join(ROOT, "gitkcli.py")
PRISTINE_REPO = os.path.join(ROOT, "test", "repo")
CONFIG_DIR = os.path.join(HERE, "config")
CASES_DIR = os.path.join(HERE, "cases")


def build_env(xdg_home, home):
    """Minimal, pinned environment so rendering is reproducible."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        # screen-256color decodes the standard keys but leaves Shift-F5 and
        # Ctrl-Left/Right/Del for the app's own ESC parser (xterm-256color has
        # terminfo for those, so ncurses would swallow them first and the
        # bindings would never fire). This matches the terminal the app targets.
        "TERM": "screen-256color",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": home,
        "XDG_CONFIG_HOME": xdg_home,
        # Keep git from reading the developer's / machine's config & prompting.
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        # Pin identity + dates so any commit the app or a `run` step creates
        # (cherry-pick, revert, commit) gets a reproducible SHA.
        "GIT_AUTHOR_NAME": "Test Runner",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Runner",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2025-02-01T12:00:00+00:00",
        "GIT_COMMITTER_DATE": "2025-02-01T12:00:00+00:00",
    }


def normalize_golden(text):
    lines = text.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    return lines


class CaseError(Exception):
    pass


class Runner:
    def __init__(self, update=False, verbose=False, keep=False):
        self.update = update
        self.verbose = verbose
        self.keep = keep

    def _make_workdir(self, name, config_name):
        import tempfile
        work = tempfile.mkdtemp(prefix=f"gkc_{name}_")
        repo = os.path.join(work, "repo")
        xdg = os.path.join(work, "xdg")
        home = os.path.join(work, "home")
        os.mkdir(home)
        # Full copy of the fixture: clean tree + private .git the test may mutate.
        subprocess.run(["cp", "-a", PRISTINE_REPO, repo], check=True)
        template = os.path.join(CONFIG_DIR, config_name)
        if not os.path.isdir(template):
            raise CaseError(f"config template not found: {template}")
        shutil.copytree(template, xdg)
        return work, repo, xdg, home

    def _launch(self, repo, env, rows, cols, app_args):
        h = Harness([sys.executable, GITKCLI] + list(app_args), cwd=repo,
                    env=env, rows=rows, cols=cols)
        h.settle(require_output=True)
        return h

    def _run_shell(self, cmd, repo, env):
        """Run a shell command in the work repo (external change / setup)."""
        r = subprocess.run(cmd, shell=True, cwd=repo, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise CaseError(f"run failed ({cmd!r}): {r.stderr.strip()}")

    def _send_keys(self, h, events):
        for ev in events:
            h.send(ev.data)
            if ev.is_esc:
                time.sleep(ESC_GAP)
            elif ev.post_delay:
                time.sleep(ev.post_delay)

    def run_case(self, name, live=False):
        """Returns (passed: bool, messages: list[str])."""
        case_dir = os.path.join(CASES_DIR, name)
        spec_path = os.path.join(case_dir, "spec.txt")
        golden_dir = os.path.join(case_dir, "golden")
        with open(spec_path) as f:
            directives = dsl.parse_spec(f.read())

        # In live mode, replay only up to the last capture, then hand over.
        if live:
            cut = max((i for i, d in enumerate(directives)
                       if d.op == "capture"), default=len(directives) - 1)
            directives = [d for d in directives[:cut + 1] if d.op != "expect-exit"]

        config_name = "default"
        size = None
        for d in directives:  # resolve config/size before any launch
            if d.op == "config":
                config_name = d.name
            if d.op == "size":
                size = (d.rows, d.cols)
            if d.op == "launch":
                break
        if size is None:
            raise CaseError(f"{name}: no `size` before `launch`")

        work, repo, xdg, home = self._make_workdir(name, config_name)
        env = build_env(xdg, home)
        messages = []
        passed = True
        h = None
        try:
            for d in directives:
                if d.op in ("size", "config"):
                    continue
                elif d.op == "launch":
                    h = self._launch(repo, env, size[0], size[1], d.args)
                elif d.op == "run":
                    self._run_shell(d.name, repo, env)
                elif d.op == "key" or d.op == "text":
                    self._send_keys(h, d.keys)
                elif d.op == "wait":
                    if d.seconds <= 0:
                        h.settle()
                    else:
                        end = time.monotonic() + d.seconds
                        while time.monotonic() < end:
                            h.settle(quiet=0.05, timeout=end - time.monotonic())
                elif d.op == "resize":
                    h.resize(d.rows, d.cols)
                    h.settle()
                elif d.op == "capture":
                    h.settle()
                    ok, msg = self._handle_capture(h, golden_dir, d.name)
                    passed = passed and ok
                    if msg:
                        messages.append(msg)
                elif d.op == "expect-exit":
                    if not h.wait_exit():
                        passed = False
                        messages.append(f"  expect-exit: app still running "
                                        f"(line {d.lineno})")

            if live:
                if h is not None and h.is_alive():
                    print(f"\n[live] replay done; handing over terminal for "
                          f"'{name}'. Quit the app (F10) to return.\n")
                    time.sleep(0.3)
                    h.bridge()
                else:
                    print(f"[live] app for '{name}' already exited; nothing to "
                          f"attach to.")
        finally:
            if h is not None:
                h.close()
            if self.keep:
                messages.append(f"  workdir kept: {work}")
            else:
                shutil.rmtree(work, ignore_errors=True)
        return passed, messages

    def _handle_capture(self, h, golden_dir, cap_name):
        # Goldens store the frame in raw terminal format (text + inline ANSI
        # colour); `less -R` renders them. Colour-only state (selection
        # background, ref/diff colours, ...) is therefore asserted too.
        actual = h.capture_ansi()
        golden_path = os.path.join(golden_dir, cap_name + ".txt")
        if self.update:
            os.makedirs(golden_dir, exist_ok=True)
            with open(golden_path, "w") as f:
                f.write("\n".join(actual) + "\n")
            return True, f"  updated {cap_name}"
        if not os.path.exists(golden_path):
            return False, (f"  MISSING golden for '{cap_name}' "
                           f"(run with --update to create)")
        with open(golden_path) as f:
            expected = normalize_golden(f.read())
        if expected == actual:
            if self.verbose:
                return True, f"  ok {cap_name}\n" + "\n".join(actual)
            return True, None
        diff = "\n".join(difflib.unified_diff(
            expected, actual,
            fromfile=f"golden/{cap_name}.txt", tofile=f"actual/{cap_name}",
            lineterm=""))
        return False, f"  DIFF {cap_name}:\n{_indent(diff)}"


def _indent(text):
    return "\n".join("    " + l for l in text.splitlines())


def discover_cases(flt=None):
    if not os.path.isdir(CASES_DIR):
        return []
    names = sorted(d for d in os.listdir(CASES_DIR)
                   if os.path.isfile(os.path.join(CASES_DIR, d, "spec.txt")))
    if flt:
        names = [n for n in names if flt in n]
    return names


def main():
    ap = argparse.ArgumentParser(
        prog="run.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Golden-screen tests: feed keys to the real curses app on a "
                    "fixed-size pty, capture the screen, and diff it against "
                    "cases/<name>/golden/*.txt.",
        epilog=textwrap.dedent("""\
            examples:
              python3 test/run.py                  run all cases
              python3 test/run.py --list           list case names
              python3 test/run.py --filter refs    only cases matching 'refs'
              python3 test/run.py -u               (re)generate goldens
              python3 test/run.py --live diff_view replay a case, then drop you into it
              pytest test/                         same suite as per-case nodes

            spec.txt directives (cases/<name>/spec.txt):
              size WxH | config NAME | launch [args] | run <shell> | key <tokens> |
              text "literal" | mouse click|dblclick|rclick COL ROW | wait stable|<secs> |
              resize WxH | capture NAME | expect-exit
            keys: bare chars, <Up>/<Down>/<Enter>/<Esc>/<F1>..<F12>/<S-F5>, C-w/C-Left;
                  repeat a token with *N (e.g. <Down>*5)
            mouse: COL ROW are 1-based screen coords (no drag/wheel -- the pty only
                   reports button press/release, not motion)

            first run needs the fixture:  bash test/create_test_repo.sh
            """))
    ap.add_argument("-u", "--update", action="store_true",
                    help="(re)generate golden snapshots instead of comparing")
    ap.add_argument("--filter", metavar="SUBSTR",
                    help="only run cases whose name contains SUBSTR")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print captured frames for passing captures too")
    ap.add_argument("--keep", action="store_true",
                    help="keep per-case work dirs (print their paths)")
    ap.add_argument("--live", metavar="CASE",
                    help="replay CASE in this terminal, then hand over control")
    ap.add_argument("--list", action="store_true",
                    help="list available case names (respects --filter) and exit")
    args = ap.parse_args()

    if args.list:
        for name in discover_cases(args.filter):
            print(name)
        return

    if not os.path.exists(PRISTINE_REPO):
        sys.exit(f"fixture missing: {PRISTINE_REPO}\n"
                 f"run: bash {os.path.join(ROOT, 'test', 'create_test_repo.sh')}")

    runner = Runner(update=args.update, verbose=args.verbose, keep=args.keep)

    if args.live:
        passed, messages = runner.run_case(args.live, live=True)
        for m in messages:
            print(m)
        return

    names = discover_cases(args.filter)
    if not names:
        sys.exit("no cases found"
                 + (f" matching {args.filter!r}" if args.filter else ""))

    n_pass = n_fail = 0
    for name in names:
        try:
            ok, messages = runner.run_case(name)
        except (dsl.SpecError, CaseError) as e:
            ok, messages = False, [f"  ERROR: {e}"]
        except Exception as e:  # surface harness/runtime errors per-case
            ok, messages = False, [f"  ERROR: {type(e).__name__}: {e}"]
        status = ("UPDATED" if args.update else "PASS") if ok else "FAIL"
        print(f"[{status}] {name}")
        for m in messages:
            print(m)
        if ok:
            n_pass += 1
        else:
            n_fail += 1

    print(f"\n{n_pass} passed, {n_fail} failed")
    sys.exit(1 if n_fail else 0)


# --- pytest integration ---------------------------------------------------
# run.py doubles as the pytest module: `pytest test/` collects one test_case
# node per case (pyproject.toml's [tool.pytest.ini_options] adds run.py to
# python_files). Guarded so the standalone CLI never depends on pytest.
try:
    import pytest as _pytest
except ImportError:
    _pytest = None

if _pytest is not None:
    @_pytest.fixture(scope="session", autouse=True)
    def _fixture_repo():
        """Build the deterministic fixture repo once if it isn't there yet."""
        if not os.path.exists(PRISTINE_REPO):
            subprocess.run(
                ["bash", os.path.join(ROOT, "test", "create_test_repo.sh")],
                check=True)

    @_pytest.mark.parametrize("name", discover_cases())
    def test_case(name):
        passed, messages = Runner().run_case(name)
        assert passed, "\n".join(messages) or f"case {name} failed"


if __name__ == "__main__":
    main()
