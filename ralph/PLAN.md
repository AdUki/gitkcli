# Ralph Loop Plan — Modularize gitkcli

> **This file is the stable prompt.** It does not change between iterations.
> Per-iteration state lives in `ralph/PROGRESS.md`. The target layout lives in
> `ralph/STRUCTURE.md`. Read all three at the start of every iteration.

## Mission

Refactor the single-file `gitkcli.py` (~3987 lines, 55 classes) into a clean,
module-based Python package. End state:

- Code split into multiple cohesive modules under a `gitk/` package.
- Everything in classes with a single clear responsibility.
- **No `Gitkcli` service-locator class.** It is replaced by an `App` *struct*
  (a plain instance holding the app's components) that is **passed / injected**,
  never reached through a module global.
- Loose coupling: a module depends on base classes + the `App` struct it is
  handed, not on its siblings' internals or on globals.
- Readable: small files, clear names, docstrings preserved/improved.
- Behavior is **identical** — proven by the golden-screen test suite, which
  must stay 100% green after every committed iteration.

## Why a Ralph loop fits

This is a large, mechanical-but-careful refactor with an automatic oracle (60
golden tests). Each iteration makes one small, verifiable move and commits it.
Progress persists in files + git history, so each iteration builds on the last.

---

## Hard invariants (true at the end of EVERY committed iteration)

1. **Tests green.** `python3 test/run.py` reports all cases passing.
2. **Goldens untouched.** You must NOT edit or regenerate any file under
   `test/cases/*/golden/`. The refactor changes structure, not behavior, so the
   rendered screens cannot change. If a golden would change, your refactor
   introduced a behavior regression — fix the code, never the golden.
   (`git status test/cases` must show no golden modifications.)
3. **App launches.** `python3 gitkcli.py --help` exits cleanly and the suite,
   which spawns the real curses app via `python3 gitkcli.py`, runs.
4. **Entry path intact.** The test harness launches `python3 gitkcli.py`
   (`test/run.py` → `GITKCLI`). Keep that command working the whole time. Also
   keep the `gitkcli` console-script (`setup.py` entry_points) importable.
5. **No circular imports.** `python3 -c "import gitkcli"` (and importing the
   `gitk` package) must succeed.
6. **Monotonic progress.** Each iteration either reduces `gitkcli.py` size,
   reduces the count of `Gitkcli.` references, or completes a checklist item —
   and never regresses a previously-met exit criterion.
7. **One small step.** Touch one cohesive concern per iteration. Keep diffs
   reviewable. Big-bang rewrites are forbidden — they make a red suite
   un-diagnosable.

---

## Strategy (chosen to keep the suite green at every step)

The harness runs `python3 gitkcli.py`, and ~all 55 classes currently reach the
`Gitkcli` global. So splitting files and removing the global are coupled. Do it
in this order; do NOT skip ahead.

**Phase 1 — Dissolve the global into an `App` struct (still one file).**
Convert the `Gitkcli` class (classmethods + class attributes used as a global)
into an `App` *instance* created in `launch_curses`. Establish a single access
path so components read `self.app.<x>` instead of `Gitkcli.<x>`:
  - Views receive `app` (inject at construction in `launch_curses`; store
    `self.app`).
  - Items/Segments reach `app` through a parent back-reference (the existing
    auto-parenting convention: `ListView.append/insert`, `set_header_item`,
    `SegmentedListItem.__init__` already set parents). Add a small
    `get_app()`/`self.app` accessor that walks the parent chain to a node that
    holds `app` (Screen or the owning View). Default Screen to hold `app`.
  - Replace all 297 `Gitkcli.<x>` references with the injected access.
  - The 10 `Gitkcli` classmethods (`run_git`, `refresh_all`, `open_search`,
    `open_context_menu`, `exit_program`, `split_active`, `cycle_split_view`,
    `set_split_mode`, `apply_split_layout`, `reload_refs_commits`) become `App`
    methods. Split state (`split_mode`, `split_ratio`, layout) may move to a
    small `SplitLayout` helper held by `App` if it reads cleaner.
  After Phase 1: `grep -c 'Gitkcli\.' gitkcli.py` is 0 and the class is named
  `App` (an instance, not a global). Still one file. Suite green.

**Phase 2 — Extract cohesive clusters into the `gitk/` package.**
Create package `gitk/`. Move one cluster per iteration into its module, and in
`gitkcli.py` re-import the moved names (`from gitk.<mod> import *`) so any
not-yet-moved code still resolves them. Suite green after each move. Suggested
extraction order (leaves first → fewest inbound deps):
  1. `gitk/config.py` — `get_config_path`, `load_config`, `save_config`,
     `copy_to_clipboard`, `KEY_CTRL`.
  2. `gitk/input.py` — `KeyboardState`, `MouseState`.
  3. `gitk/log.py` — `Log`.
  4. `gitk/jobs.py` — `Job`, `GitLogJob`, `GitRefreshHeadJob`, `GitDiffJob`,
     `GitSearchJob`, `GitRefsJob` (split into `gitk/jobs/` if it exceeds the
     size cap).
  5. `gitk/segments.py` — `Segment` + all segment subclasses.
  6. `gitk/items.py` — `Item` + list-item subclasses + the `SegmentedListItem`
     family (`ButtonRowItem`, `WindowTopBarItem`, `CommitListItem`, ...).
  7. `gitk/view.py` — `View`, `ListView`.
  8. `gitk/views/` — `git_log.py`, `git_diff.py`, `git_refs.py`, `log.py`
     (the `LogView`).
  9. `gitk/dialogs/` — context menu, confirm/error, user-input, preferences,
     reset, ref-push, new-ref, search.
  10. `gitk/app.py` — the `App` struct + `SplitLayout`.
  11. `gitk/main.py` — `launch_curses`, `main`, the curses wiring.
  See `ralph/STRUCTURE.md` for the full target tree (adapt if a better cut
  emerges — record the deviation in PROGRESS.md).

**Phase 3 — Thin the entry point & tidy.**
Once every cluster is moved, `gitkcli.py` becomes a thin shim:
```python
from gitk.main import main
if __name__ == "__main__":
    main()
```
Remove the `from gitk... import *` re-export crutches. Update `setup.py`
(`packages=find_packages()` / keep the `gitkcli=...:main` console script
pointing at the new location) and `pyproject.toml`. Confirm the console script
and `python3 gitkcli.py` both still work.

**Phase 4 — Loose-coupling & readability pass + new tests.**
  - Audit cross-module imports: a module should import base classes + `App`, not
    siblings' internals. Replace any lingering tight coupling with passed data
    (structs / dataclasses for things like commit/diff/ref records if it reads
    cleaner — but do not change behavior).
  - Ensure every module file ≤ ~600 lines (split further if not; document any
    justified exception in PROGRESS.md).
  - Add **unit tests** for the now-isolated, pure pieces (e.g. `config` parsing,
    `KEY_CTRL`, job line-parsers, segment geometry) under `test/` so logic has
    fast coverage independent of the pty goldens. These are additive; the golden
    suite remains the behavioral oracle.

---

## Per-iteration protocol

1. **Orient.** Read `ralph/PROGRESS.md`. Identify the current phase and the next
   unchecked task. Read `ralph/STRUCTURE.md` if doing Phase 2/3 work.
2. **Confirm baseline green** if unsure: run a quick smoke
   (`python3 test/run.py --filter log_startup`). If the tree is already red from
   a previous broken iteration, your only job this iteration is to get it green
   (or `git reset --hard` to the last green commit and re-plan smaller).
3. **Do ONE small step** toward the next task. Keep it cohesive and reviewable.
4. **Verify:** run the **full** suite `python3 test/run.py`. It must report all
   cases passing AND `git status test/cases` must show no golden changes.
   Also `python3 -c "import gitkcli"` must succeed (no import cycle).
5. **If green:** `git add -A && git commit` with a clear message
   (`refactor: extract config helpers into gitk/config.py`). Then update
   `ralph/PROGRESS.md`: check the task, append a dated note with what changed,
   any gotcha learned, and the new `gitkcli.py` line count + `Gitkcli.` ref
   count. Keep STRUCTURE.md in sync if the layout evolved.
6. **If red and you can't fix it quickly:** revert the step
   (`git checkout -- <files>` or `git reset --hard HEAD`), and in PROGRESS.md log
   the blocker and a smaller next slice. Never leave the tree red across a
   commit. Never make tests pass by editing goldens.
7. **Try to exit.** The loop re-feeds this plan. Only output the completion
   promise if EVERY exit criterion below is verifiably TRUE.

### Discipline rules
- Work on branch `refactor/modularize` (create it off `master` in iteration 0;
  tag the starting commit `refactor-baseline`). Never commit to `master`.
- Commit every green iteration — that is how progress persists for the next loop.
- Never edit/regenerate goldens. Never weaken or delete a test to make it pass.
- Preserve docstrings and comments when moving code; improve them only when it
  aids clarity. Do not "compact" code to hit a line target — see the
  refactor-line-floor memory: distributing across files is the goal, not
  minimizing total LOC. Total LOC may rise slightly (imports/headers); fine.
- Prefer passing structs over reaching through references. No new globals.

---

## Exit criteria (output `<promise>REFACTOR_COMPLETE</promise>` only when ALL true)

1. `grep -rn 'class Gitkcli' .` → **no matches** (the service-locator is gone).
2. `grep -rn 'Gitkcli\.' gitkcli.py gitk/` → **0 matches**.
3. Code lives in a `gitk/` package of multiple cohesive modules; `gitkcli.py` is
   a thin entry point (≤ ~15 lines).
4. No module file exceeds ~600 lines (or PROGRESS.md documents a justified
   exception).
5. Cross-module access is via an injected/passed `App` struct and passed data —
   no module-level app global. Each class has one clear responsibility.
6. `python3 test/run.py` → **all cases pass**, and `git status test/cases` shows
   **no golden modifications** vs the `refactor-baseline` tag.
7. `python3 gitkcli.py --help` works and `python3 -c "import gitkcli"` succeeds
   (no circular imports). `setup.py`/`pyproject.toml` updated; console script
   intact.
8. Every task in `ralph/PROGRESS.md` is checked; `ralph/STRUCTURE.md` matches
   the real tree.
9. (Phase 4) Added unit tests pass alongside the golden suite.

If after many iterations you are blocked on something requiring a human design
decision, do NOT output the promise. Instead write the blocker, what you tried,
and 2-3 options into `ralph/PROGRESS.md` under a `## BLOCKED` heading and keep
looping on the parts that are not blocked.

## Verification command reference
```
python3 test/run.py                 # full golden suite (the gate)
python3 test/run.py --filter NAME   # quick smoke during dev
python3 test/run.py --list          # list the 60 cases
git status test/cases               # MUST be clean (no golden edits)
python3 -c "import gitkcli"         # no circular imports
grep -rn 'Gitkcli\.' gitkcli.py gitk/ | wc -l   # → 0 at the end
wc -l gitkcli.py                    # trends down to the thin shim
```
