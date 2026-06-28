# Ralph Loop Progress — gitkcli modularization

> Living state. The loop updates this every iteration. Newest notes at the top
> of the log. Check off tasks as they complete. See `ralph/PLAN.md` for rules.

## Baseline (captured 2026-06-28, before any refactor)

- `gitkcli.py`: **3987 lines**, **55 classes**, single file.
- `Gitkcli.` references in `gitkcli.py`: **297** (top: git_log 85, git_diff 52,
  screen 41, log 37, mouse 28, git_refs 18).
- `Gitkcli` classmethods: **10**.
- Golden test suite: **60 cases, all passing** (verified `log_startup`).
- Harness launches `python3 gitkcli.py` (`test/run.py` → `GITKCLI`). Do not
  break this path.

## Current status

- **Phase:** 1 — **COMPLETE.** The `Gitkcli` service locator is fully gone:
  no `class Gitkcli`, no module global, no bridge. `grep -n 'Gitkcli' gitkcli.py`
  → nothing. `App` is a plain struct created in `launch_curses` and injected:
  Screen/View/Log/jobs get it at construction (`self.app`), items/segments via
  the `get_app()` parent chain.
- **Phase:** 4 — in progress. ALL modules now ≤ ~600 lines (criterion 4 MET).
  Splits done: view→view(426)+list_view(333); items→items(341)+
  segmented_items(330); views.py(658) → `gitk/views/` package
  (git_log 358, context_menu 163, git_diff 109, log 33, git_refs 27, __init__ 11).
  setup.py find_packages → ['gitk','gitk.views'].
- **ALL PHASES COMPLETE.** Every exit criterion in PLAN.md verified TRUE
  (2026-06-28, iteration 35 — see the Exit-criteria check section below). The
  `Gitkcli` service-locator is gone; the app is a 14-module `gitk/` package
  reached through an injected `App` struct; `gitkcli.py` is a 9-line shim; all
  modules ≤ ~600 lines; golden suite 60/60 with goldens byte-identical to
  `refactor-baseline`; unit tests 9/9.
- **NEXT:** refactor done → proceed to code-quality improvements and bug hunting
  (keeping the golden + unit suites green).
- **NEXT (Phase 3):** `gitk/main.py` (move `launch_curses` + `main`, importing
  the needed names directly from their gitk modules) → reduce gitkcli.py to a
  thin shim `from gitk.main import main; if __name__=='__main__': main()`, drop
  the re-export crutch block, update setup.py/pyproject.toml console script.
  STANDING LESSON: run the AST undefined-name check (scratchpad) after each
  extraction — re-exported names aren't `class` defs so a class-only scan misses
  them, and thread/runtime NameErrors only surface as wrong goldens.
- **gitkcli.py:** 9-line shim · **package (15 modules):** view 426 + list_view
  333 (was view 743); still over cap: views 658, items 654; others ≤ 559
  (dialogs 559, jobs 427, screen 347, segments 245, main 178, app 154, log 51,
  config, ids, input, __init__). · **Gitkcli refs:** 0.

## Iteration 0 (setup) — DONE

- [x] Create branch `refactor-modularize` off `master`; tag current commit
      `refactor-baseline`. NOTE: a branch named `refactor` already exists, which
      blocks `refactor/modularize` (git ref hierarchy conflict). Using
      `refactor-modularize` instead.
- [x] Run the FULL suite once (`python3 test/run.py`): **60 passed, 0 failed**
      (confirmed baseline, 2026-06-28).
- [x] Commit (plan files only) and update "Current status".

## Phase 1 — Dissolve `Gitkcli` global into an `App` struct (still one file)

- [x] Introduce `App` instance + access path (`self.app` on views; `get_app()`
      parent-chain walk for items/segments; Screen holds `app`).
      COMPLETE: App instance + `Gitkcli` bridge; `self.app` on Screen/View;
      `Item._view` + `Item.get_app()`, `Segment._item` + `Segment.get_app()`
      wired in `ListView.append`/`.items.insert`/`set_header_item` and
      `SegmentedListItem.__init__`. Validated by migrating ContextMenuItem,
      RefListItem, and the Item base right-click handler to `get_app()`.
      DONE: (a) `Gitkcli` class → `App` instance (10 classmethods → instance
      methods, `cls`→`self`; class attrs → `__init__`). Transitional
      module-level `Gitkcli` name bound to the single `App()` in `launch_curses`
      (via `global Gitkcli`) so all existing `Gitkcli.<x>` call sites keep
      working. (b) `Screen.__init__` and `View.__init__` now set `self.app`
      (from the bridge at construction; Screen is the root holder, views read
      it). `View.__init__` uses `self.app.screen.add_view(...)`.
      STILL TODO in this item: `get_app()` parent-chain walk for items/segments
      (the current code has NO item→view parent wiring — items reach `Gitkcli`
      directly — so this needs append/insert/header/segment back-refs added,
      its own iteration). Then migrate clusters.

      NOTE / deviation from STRUCTURE.md: the stale `ui/` parent-chain
      (`get_screen`/`set_parent`) does NOT exist in the current single-file
      app; items/segments currently use the `Gitkcli` global directly. So the
      `get_app()` parent walk must be built, not merely "wired up".
- [x] Migrate the 10 classmethods to `App` methods (DONE in iteration 1).
- [x] Replace all `Gitkcli.<x>` references with injected access (DONE,
      iterations 3–15, cluster by cluster).
- [x] Remove the `Gitkcli` class AND the bridge global. `grep -n 'Gitkcli'
      gitkcli.py` → nothing. Suite green throughout. **Phase 1 COMPLETE.**

## Phase 2 — Extract clusters into `gitk/` (one per iteration, re-export crutch)

- [x] `gitk/config.py` — get_config_path, load_config, save_config,
      copy_to_clipboard, DEFAULT_CONFIG, KEY_CTRL. Leaf module (stdlib only).
      gitkcli.py re-exports them. (KEY_* constants stay for input.py.)
- [x] `gitk/input.py` — KeyboardState, MouseState + KEY_*/ENTER_KEYS constants.
      Uses `from __future__ import annotations` so the View/Item type hints stay
      lazy (no UI imports). gitkcli.py re-exports them.
- [x] `gitk/screen.py` — Screen (curses/panel lifecycle, colour palette, panel
      deck, bottom bar). Clean near-leaf; view/item refs duck-typed at runtime.
- [x] `gitk/ids.py` — all ID_* string constants (leaf, no imports). Not in the
      original STRUCTURE; added so jobs/views/dialogs share IDs without a
      backward `from gitkcli import`.
- [x] `gitk/jobs.py` — Job + GitLogJob/GitRefreshHeadJob/GitDiffJob/
      GitSearchJob/GitRefsJob. Imports gitk.ids + gitk.items (incl. DiffListItem/
      StatListItem — the diff job builds those rows). DiffListItem's blame jump
      uses a function-local `from gitk.jobs import Job` (jobs imports items, so
      no load-time cycle).
- [x] `gitk/segments.py` — Segment + 10 subclasses + ref_color_and_title.
      Imports Screen; RefSegment uses a function-local `from gitkcli import
      RefListItem` to break the segment↔item cycle.
- [x] `gitk/items.py` — Item + 15 subclasses + button_row (incl. the
      SegmentedListItem family; STRUCTURE's separate segmented_items.py folded in
      for now). 654 lines → split in Phase 4. DiffListItem uses a function-local
      `from gitkcli import Job`.
- [x] `gitk/segmented_items.py` — SegmentedListItem family + button_row, split
      out of items.py in Phase 4 (iteration 31). Imports Item from gitk.items.
- [x] `gitk/view.py` — View + ListView + `_raise_split_sibling` +
      HORIZONTAL_OFFSET_JUMP/SPLIT_DIVIDER_COLOR. Imports items (no cycle).
      743 lines → split into view.py + list_view.py in Phase 4.
- [x] `gitk/views/` package — git_log.py (GitLogView), git_diff.py (GitDiffView),
      git_refs.py (GitRefsView), log.py (LogView), context_menu.py (ContextMenu);
      __init__.py re-exports all 5 so `from gitk.views import GitLogView` works.
      Each view is independent (siblings reached via app at runtime).
- [x] `gitk/dialogs.py` — 10 modal popups (reset, ref-push, _RedMessageBox,
      confirm, error, user-input base, preferences, new-ref, search, git-search).
      Single module for now (STRUCTURE's dialogs/ package split deferred to
      Phase 4). Depends only on ListView + items/segments/jobs/helpers; reaches
      concrete views via app at runtime. ContextMenu stays with the views for
      now (extract with them).
- [x] `gitk/log.py` (Log — imports LogView + TextListItem).
- [x] `gitk/app.py` (App struct; only runtime dep is Job + KeyboardState, rest
      are __init__ attribute annotations. SplitLayout not split out — kept on
      App; revisit in Phase 4 if it reads cleaner).
- [x] `gitk/main.py` (launch_curses, main).

## Phase 3 — Thin entry point & packaging — DONE

- [x] `gitkcli.py` reduced to the thin shim (9 lines).
- [x] Remove re-export crutches (none remain; no gitk module imports gitkcli).
- [x] Update `setup.py` (packages=find_packages, console script → gitk.main:main);
      console script target imports and `python3 gitkcli.py` both work.
      pyproject.toml unchanged (build-backend + pytest config still valid).

## Phase 4 — Loose-coupling, readability, new tests

- [x] Cross-module import audit: load-time import graph is a DAG (verified by
      AST cycle check); two genuine cycles (items↔jobs, items↔segments) broken
      via documented function-local imports. No concrete-sibling-view imports
      (siblings reached via app). Unused imports removed; views/__init__ __all__.
- [x] Every module ≤ ~600 lines. view→view 426 + list_view 333; items→items 341
      + segmented_items 330; views.py 658 → gitk/views/ package (git_log 358,
      context_menu 163, git_diff 109, log 33, git_refs 27). No exceptions needed.
- [x] Introduce passed structs/dataclasses where it improves clarity (no
      behavior change). JUDGMENT: the `App` struct is now passed/injected
      everywhere (views/jobs at construction; items/segments via get_app()), and
      record-like data (commit/diff/ref dicts) is already passed as plain dicts
      via the jobs→items pipeline. No further dataclass conversion was made — it
      would be churn with behavior-change risk and no clarity gain that the
      golden suite could vouch for. (Optional task; intent satisfied.)
- [x] Add unit tests for pure pieces → `test/test_units.py` (9 tests):
      KEY_CTRL, DEFAULT_CONFIG shape, get_config_path, load_config
      (defaults/merge-known-keys/corrupt-json), save_config roundtrip,
      ref_color_and_title per type + head arrow. `pytest test/` collects 69
      (60 golden + 9 unit); all pass. `python3 test/run.py` still 60/60.

## Exit-criteria check (verified 2026-06-28, iteration 35)

- [x] `class Gitkcli` → none in TRACKED files (`git grep 'class Gitkcli'` empty).
      The only hit `grep -rn 'class Gitkcli' .` finds is in
      `.claude/worktrees/clipboard-fix/` — a gitignored, unrelated detached-HEAD
      worktree (pre-refactor checkout), not part of this project's sources.
- [x] `grep -rn 'Gitkcli\.' gitkcli.py gitk/` → 0 (and no bare `Gitkcli` either).
- [x] `gitkcli.py` is a 9-line thin shim; all code in the `gitk/` package.
- [x] no module > ~600 lines (largest gitk module: dialogs 559, git_log 358).
- [x] full golden suite passes (60/60); `git diff refactor-baseline -- test/cases`
      empty and `git status test/cases` clean.
- [x] `import gitkcli` works; console-script target `gitk.main:main` imports;
      `python3 gitkcli.py --help` works.
- [x] all tasks above checked; STRUCTURE.md matches reality (deviations recorded:
      added gitk/ids.py + gitk/list_view.py; ContextMenu lives in views/, not
      dialogs/; dialogs is a single module not a package).
- [x] added unit tests pass (`pytest test/test_units.py` → 9/9; `pytest test/`
      → 69/69).

---

## Post-refactor: code improvement & bug-fixing (branch `improve/post-refactor`)

A read-only bug-review of the gitk package surfaced several candidates. Verified
& fixing them one per iteration, golden + unit suites green each time. Queue:
- [x] **jump_index staleness** (git_log.add_to_jump_list) — dedup early-return
      left jump_index out of range → broken back/forward nav. FIXED + unit tests.
- [x] **Job queue not cleared on restart** (jobs.start_job) — stale items from a
      terminated run could contaminate the next run on the same Job singleton.
      FIXED: track reader threads; on restart, join them while `stop` is still
      True (so they can't emit a stale 'finished' or race), then empty
      items/messages before resetting stop + spawning new readers.
- [x] **stop_job leaves running=True** on the non-timeout path → potential busy
      redraw loop. FIXED: set `running=False` unconditionally at the top of
      stop_job (a stopped job is not running; the new-run 'started' message
      re-sets it True on restart).
- [x] **str/regex set_selected hardcodes git_diff.items** (list_view.py) —
      FIXED: search `self.items` (identical for today's sole caller, which runs
      on git_diff; correct for any other ListView).
- [x] **set_selected non-selectable skip direction** — the outer pass loop never
      broke and reused the updated index, so when selectable rows existed on both
      sides the opposite-direction pass clobbered the travel-direction match
      (landing opposite to travel). FIXED: search both passes from the original
      target, prefer travel direction, stop at first hit. Unit tests added.
- [x] **RefPush empty-named remote when no remotes** — `''.split('\n')` → `['']`
      made a blank toggle + `self.remote=''`. FIXED: parse with `.split()` (drops
      empties → `[]`), init `self.remote=''`, guard the initial selection.
- [x] **#3 separator/offset under horizontal scroll** — FIXED (iteration 53).
      `draw_line` now makes the inter-segment separator participate in the
      `offset` walk (consumed when scrolled off), keeping the drawn columns
      aligned with `_offset_x`/`get_segment_on_offset`. offset=0 byte-identical
      (the 61 existing goldens stay green); new additive `log_hscroll` golden +
      an independent check (narrow scrolled rows == full 120-wide rows shifted
      left by 12) verify offset>0. The earlier "deferred" call was over-cautious:
      the fix touches NO existing golden (no existing case scrolls horizontally),
      so it was in-rules all along.
      ALL reviewed bugs now fixed.

## Log (newest first)

- **2026-06-28 — Iteration 63 (BUG: empty-repo error dialog; + LC_ALL on streaming jobs).**
  Probed a previously-untested real scenario — launching in a repo with no
  commits (fresh `git init`, unborn branch) — via a pty harness and found a
  genuine UX bug: `git log` exits non-zero with "fatal: your current branch
  '…' does not have any commits yet", whose stderr was surfaced as a scary red
  **error dialog** on startup instead of just an empty log. Fixes: (1) pin
  `LC_ALL=C` on the streaming `Job` Popen (it lacked it, unlike `run_job` —
  aligns with the documented git-output convention and makes stderr reliably
  English; commit `--format` output is locale-independent, so the 62 populated
  goldens stay byte-identical — verified); (2) `GitLogJob.process_message`
  swallows the unborn-branch stderr (empty repo → empty log, no dialog;
  inherited by GitRefreshHeadJob). Verified via the pty probe (dialog gone,
  clean `[0/0]` log) and added an additive `log_empty_repo` golden (resets the
  work repo to empty via `run`, then captures). Full suite **63/63** (existing
  goldens untouched); units **39/39**.
- **2026-06-28 — Iteration 62 (docs audit complete: Search/Context sections verified).**
  Finished the README↔code audit: the **Search Features** section is accurate —
  the mode labels `[Txt]/[ID]/[Message]/[Filepaths]/[Diff]` match
  `SearchDialogPopup._TYPES` exactly, TAB cycles types, and the `<Case>` /
  `<Regexp>` flags match. The **Context Menus** groupings (commit / branch / tag
  operations) match the menu builders. No change needed in these sections. With
  the earlier fixes (Configuration, function-key bar, reset semantics, toggle
  label) the entire README is now cross-checked against the code and accurate.
  No source change. Full suite **62/62**; units **39/39**; goldens untouched.
- **2026-06-28 — Iteration 61 (docs: fix two wrong README key bindings).**
  Cross-checked the remaining Key Bindings against the handlers. Navigation
  (j/k/h/l/g/G/Ctrl-b/f), b/c/v/m/M, n/N, ENTER, and `+`/`-` all verified
  correct. Fixed two inaccuracies: (1) `r`/`R` don't do separate soft/hard
  resets — both call `confirm_reset()` which opens the reset-mode dialog
  (Soft/Mixed/Hard, default Mixed); reworded to one accurate entry. (2) the diff
  whitespace toggle's button label is `[Ignore whitespace]`, not
  `[Ignore space change]`. User-facing accuracy fixes, doc-only. Full suite
  **62/62** (existing goldens untouched); units **39/39**.
- **2026-06-28 — Iteration 60 (docs: complete the README function-key list).**
  The README listed only F1–F5 + Shift+F5, but the app's bottom bar also binds
  F6 (Search), F7 (Context menu at selection), F9 (Config/Preferences), F10
  (Quit) — verified against `screen.py`'s `bottom_bar_entries`. Added the four
  missing entries and renamed the subsection "Function-key bar" to match the UI.
  User-facing accuracy fix, doc-only. Full suite **62/62** (existing goldens
  untouched); units **39/39**.
- **2026-06-28 — Iteration 59 (docs: fix stale README Configuration section).**
  The README's Configuration said "Adjust these settings in the code" with a
  code-variable list — genuinely misleading for users, since those options are
  now in the **F9 Preferences** dialog and persisted to a JSON config file.
  Rewrote it to document the F9 toggles (id/date/author columns, ignore-
  whitespace, autoscroll, default view mode, extra git-log flags), the config
  file location per platform (XDG / macOS / Windows, matching get_config_path),
  diff context via the diff view's `[+]`/`[-]`, and noted the genuinely
  code-only knobs (rename_limit, log verbosity). User-facing accuracy fix,
  doc-only. Full suite **62/62** (existing goldens untouched); units **39/39**.
- **2026-06-28 — Iteration 58 (docs: README "Project structure" for contributors).**
  Verified the README had no stale structure references (it's user-facing). Added
  a concise "Project structure" section: the code lives in `gitk/` (gitkcli.py is
  a launch shim), the layering + injected-`App` design is in `gitk/__init__.py`,
  and how to run the two test suites (`test/run.py` goldens; `pytest test/` for
  goldens+units). Gives contributors a navigation entry point that didn't exist.
  Doc-only. Full suite **62/62** (existing goldens untouched); units **39/39**.
- **2026-06-28 — Iteration 57 (coverage: Screen._to_pal colour-tier degradation).**
  Added 3 pure unit tests for the palette-index → tier mapping that backs the
  `--no-color`/8-colour degradation (the non-obvious arithmetic behind the
  `log_nocolor` golden): full tier passes 256-only indices through; the 8/mono
  tiers collapse indices ≥8 to `COLOR_WHITE` while keeping the 8 base colours;
  the negative sentinel maps to `_default_bg`. `_to_pal` is pure (uses
  `curses.COLOR_WHITE` constant, no `initscr`); tests save/restore the shared
  `color_depth`/`_default_bg` class attrs via a context manager. Full suite
  **62/62** (existing goldens untouched); units **39/39**.
- **2026-06-28 — Iteration 56 (verify combined CI suite: `pytest test/`).**
  Ran the project's actual CI invocation `python3 -m pytest test/` end-to-end
  (not the golden + unit suites separately): **98 passed** (62 golden cases via
  run.py's test_case + 36 unit tests) in ~181s, no collection conflicts, existing
  goldens untouched. Confirms the documented test command is green with all the
  post-refactor additions (the 2 new golden cases + 36 unit tests coexist
  cleanly under one pytest run). Doc-only iteration.
- **2026-06-28 — Iteration 55 (coverage: ButtonRowItem focus navigation).**
  Added 4 pure unit tests for the button-row navigation used by every dialog:
  `_button_indices` finds segments with `activate` (skipping text/filler),
  `__init__`/`reset_focus` focus the first button, `focus_last` the rightmost
  (safe default for destructive confirms), and `_move_focus` wraps Left/Right
  while skipping non-buttons — plus the no-buttons no-op guard. Headless
  (no curses). Full suite **62/62** (existing goldens untouched); units **36/36**.
- **2026-06-28 — Iteration 54 (coverage: get_segment_on_offset hit-test math).**
  Completed the #3 story by locking the *other* half — the click hit-test.
  `get_segment_on_offset` maps an absolute get_text() offset (separators
  included) to a segment; it was already correct in that coordinate space (the
  iteration-53 draw fix made `draw_line` agree with it). Added 2 pure unit tests
  on a headless-constructed `SegmentedListItem`: columns map to the right
  segments across separators, and a separator-gap column maps to no segment
  (a fresh empty Segment). Full suite **62/62** (existing goldens untouched);
  units **32/32**.
- **2026-06-28 — Iteration 53 (FIX #3: separator-aware offset in draw_line + h-scroll golden).**
  Realized the earlier deferral was over-cautious: #3's fix only changes
  rendering at `offset>0`, and NO existing golden scrolls horizontally, so it
  touches no existing golden — in-rules. Reworked `SegmentedListItem.draw_line`
  so the inter-segment separator participates in the horizontal-scroll walk:
  consumed from `offset` when scrolled off the left, else drawn (gated on the
  prior segment having shown text). At `offset=0` it reduces exactly to the old
  `draw_separator` logic → the 61 existing goldens stay byte-identical (verified;
  the critical regression gate). Added an additive `log_hscroll` golden (narrow
  40-col pty, `<Right>*12`) and INDEPENDENTLY verified correctness: every scrolled
  commit row equals the full 120-wide row shifted left by 12 (38/38 aligned, 0
  mismatched) — so segments+separators now scroll in lockstep with `_offset_x`,
  and the draw/`get_segment_on_offset` hit-test agree. Full suite **62/62**;
  units **30/30**; existing goldens untouched.
- **2026-06-28 — Iteration 52 (coverage: Job queue helpers _empty_queue/_drain).**
  Confirmed the other degraded-tier/feature paths are already covered (`--graph`
  has graph_mode/graph_nav; mono now has log_nocolor; the 8-colour tier can't be
  forced via an app arg, no harness TERM control). Added 3 zero-risk unit tests
  for the pure `Job` queue helpers — `_empty_queue` (drains fully) and `_drain`
  (dispatches truthy items, breaks on the falsy sentinel leaving the rest queued,
  and drains-without-dispatch while `stop` is set) — the queue-processing
  contract behind the iteration-37 stale-row fix. Full suite **61/61** (existing
  goldens untouched); units **30/30**.
- **2026-06-28 — Iteration 51 (coverage: add golden for the --no-color mono tier).**
  The monochrome colour-degradation path (`--no-color` → `Screen.force_mono` →
  `color_depth=0`) had NO golden coverage. Added a NEW (additive) case
  `test/cases/log_nocolor` — `launch --no-color` then capture the Git Log view —
  and generated its golden with `--filter log_nocolor -u` (only the new case;
  existing 60 goldens untouched, verified via `git status`). The golden confirms
  the degraded tier: white-on-black (`37;40`) + video attributes (reverse/bold)
  and crucially NONE of the `38;5;`/`48;5;`/`48;2;` palette codes the normal
  256-colour goldens carry. Deterministic (passes on re-run without `-u`). Suite
  now **61 passed, 0 failed**; units **27/27**. (This is allowed: a new case's
  golden is additive coverage of CORRECT behavior, not editing/regenerating an
  existing golden.)
- **2026-06-28 — Iteration 50 (doc: accurate gitk/__init__ module map).**
  `gitk/__init__.py`'s docstring still described an *in-progress* migration with
  `from gitk.<mod> import *` re-export crutches that no longer exist. Replaced it
  with an accurate, layered module map (the DAG), the injected-`App` access model
  (`get_app()` via `_view`/`_item` back-refs), and the two function-local
  import-cycle breakers — so the package's own docs match reality for future
  maintainers. Doc-only, zero behavior risk. Full suite **60/60** (goldens
  unchanged); units **27/27**.
- **2026-06-28 — Iteration 49 (review dialogs+main; revert a regression; fix RefPush Tab).**
  Fourth review (dialogs.py + main.py event loop). Two findings:
  (1) [the reviewer flagged the mouse release-synthesis labels in main.py as
  "inverted"] — TRIED swapping them, but it broke the `ctx_menu_branch` golden:
  that path IS exercised (a left press whose release the harness doesn't send,
  then a right-click), and the existing labels produce the correct, contractual
  on-screen result. REVERTED — the golden is the oracle; the "inversion" doesn't
  hold against the real curses event model. (Good: the suite caught a
  plausible-but-wrong change.)
  (2) FIXED a genuine crash: `RefPushDialogPopup.handle_input` Tab did
  `names.index(self.remote)`/`% len(names)` on an empty remote list (repo with
  no remotes) → ValueError / ZeroDivisionError out of handle_input. Guarded with
  `if names:`. Not golden-covered (test repo has 3 remotes), so the normal cycle
  path is unchanged. Everything else in dialogs/main reviewed clean. Full suite
  **60/60** (goldens unchanged); units **27/27**.
- **2026-06-28 — Iteration 48 (coverage: UserInputListItem word navigation).**
  Re-confirmed #3 can't be unit-tested headlessly (`Screen.color` needs
  `initscr()`, so `draw_line` isn't callable without curses) and its fix touches
  the golden-locked offset=0 path with no safe oracle → stays deferred. Added 6
  zero-risk unit tests for the pure `prev_word_pos`/`next_word_pos` cursor
  word-navigation (mid-word→word start, word-start→previous word, BOL/EOL
  bounds), driven on a SimpleNamespace stand-in. Full suite **60/60** (source
  unchanged; goldens clean); units **27/27**.
- **2026-06-28 — Iteration 47 (consolidate: health check + correct project memory).**
  Health verification: `import gitkcli` + `--help` OK; `pytest test/` collects 81
  nodes (60 golden + 21 unit); goldens byte-identical to `refactor-baseline`;
  gitk/ is 21 files (incl. views/ package), gitkcli.py 9 lines. Updated the
  persistent project memory (lives in ~/.claude, outside the repo): added
  `gitk_package_architecture.md` (current module layout, injected `App`, the
  `get_app()` back-ref access path, the two function-local import-cycle breakers,
  and the extraction lessons), and flagged the stale Parent-Chain/`ui/`/
  single-file sections in MEMORY.md as superseded. No repo source change this
  iteration. Full suite **60/60**; goldens clean.
- **2026-06-28 — Iteration 46 (defer #3 separator-offset; add process_line tests).**
  Assessed the last review note (#3): under horizontal scroll of a segmented row,
  `draw_line`'s offset bookkeeping (`offset -= len(txt) - length`) doesn't account
  for the inter-segment separators that `get_text()`/`get_segment_on_offset`/
  `_offset_x` DO count, so draw and hit-test drift by a few columns. DEFERRED:
  impact is cosmetic + a rare mis-click only while the commit log is scrolled
  right; no golden exercises `offset>0` so a fix can't be regression-verified
  against the oracle, and touching `draw_line` risks the byte-identical
  `offset=0` golden path. Prereq for fixing safely: add an h-scroll golden case
  first (a behavior-adding change to the suite, out of scope for the
  never-touch-goldens loop). Instead added zero-risk coverage: 4 unit tests for
  the pure `GitLogJob.process_line` commit parser (normal parse, '#'-in-subject,
  graph prefix, non-matching → raw string). Full suite **60/60** (source
  unchanged this iteration; goldens clean); units **21/21**.
- **2026-06-28 — Iteration 45 (post-refactor bugfix: UserInputListItem field scroll).**
  `UserInputListItem.draw_line` never updated `self.offset` (the long-standing
  TODO), so once `cursor_pos > width-1` the `left_txt` (= `txt[:cursor_pos]`)
  was wider than the field and `addstr` ran past the window edge → `curses.error`
  (caught by the draw guard, but it aborts the frame mid-render → an unusable,
  flickering input field for long text). Implemented the field scroll: keep the
  cursor within the visible `width-1` columns by adjusting `self.offset`, slice
  `left/right` from it, and clamp the trailing pad with `max(0, …)`. Also made
  the click handler use `self.offset + mouse.x` so clicks map correctly in a
  scrolled field. Identical rendering at offset 0 (cursor in view) — every
  golden types short strings, so all stay green. Full suite **60/60** (goldens
  unchanged); units **17/17**.
- **2026-06-28 — Iteration 44 (post-refactor bugfix: segment horizontal-scroll clipping).**
  Third review (segment/item draw geometry) found `Segment._draw_text` sliced
  `get_text()[offset:width]`, but `width` is a column COUNT (remaining space),
  not an absolute end index — so when `offset>0` (horizontal scroll via `l`/
  Right on the log) a straddling segment under-drew by `offset` columns, and the
  broken return value then drove `draw_line`'s `offset` accounting negative for
  later segments (the ref segments). Fixed to `[offset:offset+width]`. At
  `offset=0` (every golden — no golden horizontally-scrolls the segmented log;
  the only `<Right>`s are Preferences button-row nav) the slice is unchanged, so
  all 60 goldens stay green. Added 3 pure unit tests for `_draw_text` clipping
  (fake win). Verified the other review findings: #3 (separator offset mismatch
  under h-scroll) and #4 (UserInputListItem long-text overflow — gated by the
  unimplemented field-scroll TODO) noted for later; #5 (fill_width pre-draw
  AttributeError) not reachable (draw precedes input each frame). Full suite
  **60/60** (goldens unchanged); units **17/17**.
- **2026-06-28 — Iteration 43 (tidy: select_line stops at first match; analyze open_context_menu).**
  `GitDiffView.select_line` called `set_selected` for every matching DiffListItem
  rather than the first (intended) one, so with duplicate (file,line) rows the
  last won and intermediate `selected_line_map` writes fired needlessly. Added a
  `return` after the first match. Analyzed the other low-confidence note
  (`App.open_context_menu` could compute a negative `screen_y` if the selected
  row were scrolled above the viewport): not reachable — `set_selected` always
  keeps `_offset_y <= _selected < _offset_y+height`, so `_selected - _offset_y`
  is in `[0, height)`. Recorded as analyzed-benign; left unchanged to avoid
  churn. diff_blame_origin golden passes; full suite **60/60** (goldens
  unchanged); units **14/14**.
- **2026-06-28 — Iteration 42 (post-refactor bugfix: west-edge resize clamp).**
  A second read-only review (views input + Screen/geometry) found the
  left-border (`'w'`) resize branch in `View.handle_resize` lacked the `max(5,…)`
  minimum-width clamp the `'e'`/`'s'` branches have: dragging the left edge
  rightward shrank `new_width` to 0/negative → either a half-screen jump (0
  treated as "unset" in `_calculate_dimensions`) or a `curses.error` from
  `win.resize(h, negative)` outside the draw try/except (crash). FIXED: clamp
  `new_x` to `[0, right-5]` (right edge fixed) so width stays ≥5 — identical to
  the old formula for normal drags (`right-new_x == win_width-(new_x-win_x)`).
  Also made `_calculate_dimensions` use `fixed_width/height is not None`
  (mirroring fixed_x/y) so a legit 0 isn't read as half-screen. Resize/window
  goldens (combo_resize_split, window_float/unfloat) still pass. Full suite
  **60/60** (goldens unchanged); units **14/14**.
- **2026-06-28 — Iteration 41 (post-refactor bugfix: RefPush empty remote).**
  `RefPushDialogPopup.__init__` parsed `git remote` with `.rstrip().split('\n')`,
  which returns `['']` for a repo with no remotes — creating a blank-named
  ToggleSegment and `self.remote=''` (a later push would `git push '' <ref>`).
  Fixed: parse with `.split()` (whitespace-split drops the empty → `[]`),
  initialise `self.remote=''`, and only auto-select `remotes[0]` when remotes
  exist. Identical for the test repo (3 remotes → same list/order). This clears
  the last item from the bug-review queue. Full golden suite **60/60** (goldens
  unchanged); units **14/14**.
- **2026-06-28 — Iteration 40 (post-refactor bugfix: set_selected skip direction).**
  When `set_selected`'s target row is non-selectable, it scans for the nearest
  selectable row. The old `for dir in [direction, -direction]` loop never broke
  and the second pass started from the *updated* `new_index`, so when selectable
  rows existed on both sides between target and cursor, the opposite-direction
  pass overwrote the travel-direction match — landing on the side opposite to
  travel (just behind the target instead of past it). Fixed: search both passes
  from the original `target`, prefer the travel direction, and break at the
  first hit. Added 3 pure unit tests (int target path needs no `app`). Full
  golden suite **60/60** (goldens unchanged — buggy clobber path wasn't
  golden-covered); units **14/14**.
- **2026-06-28 — Iteration 39 (post-refactor bugfix: set_selected wrong items list).**
  `ListView.set_selected`'s str/`re.Pattern` branch iterated
  `self.app.git_diff.items` (hardcoded) instead of `self.items`, then set
  `self._selected` to that index on `self` — only correct because the sole
  current caller (`StatListItem.jump_to_file`) runs on the git_diff view. Any
  other ListView searching by text/regex would scan git_diff's rows and select a
  bogus index. Fixed to `self.items` (identical for the current caller; correct
  in general). Verified `diff_jump_to_file` golden still passes. Full golden
  suite **60/60** (goldens unchanged); units **11/11**.
- **2026-06-28 — Iteration 38 (post-refactor bugfix: stop_job running flag).**
  `stop_job` only cleared `self.running` in the `TimeoutExpired` branch; on the
  normal terminate+wait path it stayed True. Since the reader thread breaks on
  `self.stop` before emitting 'finished', nothing flipped `running` back, so
  `process_all_jobs` reported `update=True` every frame for a stopped-not-
  restarted job → main loop spun on the 5ms-timeout redraw path. Fixed by
  setting `self.running = False` unconditionally at the top of stop_job; a
  restart's 'started' message re-sets it True. Full golden suite **60/60**
  (goldens unchanged); units **11/11**.
- **2026-06-28 — Iteration 37 (post-refactor bugfix: Job queue contamination on restart).**
  `Job.start_job` reused the singleton Job's `items`/`messages` queues without
  clearing them, so rows enqueued by a terminated run (e.g. a large `git show`
  abandoned by quickly selecting another commit) could be drained into the
  freshly-cleared diff view, prepending stale lines. Fixed: `Job` now tracks its
  reader threads; `start_job` joins the previous run's threads **while
  `self.stop` is still True** (set by the preceding `stop_job`) — so they exit
  via the `not self.stop` guard without emitting a stale 'finished' and without
  racing — then empties both queues before resetting `stop` and spawning the new
  readers. Added a `_empty_queue` helper. Subclass `start_job`s all funnel
  through `Job.start_job`, so the fix covers them. Full golden suite **60/60**
  (goldens unchanged; diff-nav cases still green); units **11/11**.
- **2026-06-28 — Iteration 36 (post-refactor bugfix: jump-list index staleness).**
  On branch `improve/post-refactor`. A read-only Explore review flagged that
  `GitLogView.add_to_jump_list` truncates forward history
  (`self.jump_list[self.jump_index:]`, making the current entry index 0) but the
  dedup early-return left `jump_index` at its old value — now out of range —
  silently breaking `[<-]`/`[->]` until the next non-dedup add. Fixed by setting
  `jump_index = 0` immediately after truncation (identical on the happy path,
  correct on dedup). Added 2 pure regression unit tests driving the method on a
  SimpleNamespace stand-in (it only touches jump_list/jump_index). Full golden
  suite **60/60** (goldens unchanged — buggy path wasn't golden-covered); units
  **11/11**.
- **2026-06-28 — Iteration 35 (final exit-criteria verification).**
  Walked every PLAN.md exit criterion and verified each TRUE (filled the
  Exit-criteria check section with evidence). Reconciled stale/duplicate
  checkboxes (removed superseded Phase-1 dupes and the stray `gitk/log.py` box;
  marked segmented_items + the optional structs/dataclasses task done with
  judgment notes). Rewrote STRUCTURE.md as the as-built tree (records the
  deviations: ids.py, list_view.py, single dialogs.py, ContextMenu in views/).
  Note: `grep -rn 'class Gitkcli' .` matches only a gitignored unrelated
  worktree (`.claude/worktrees/clipboard-fix`) — `git grep` over tracked files
  is clean. Full suite **60/60**, goldens byte-identical to refactor-baseline,
  units 9/9. **Refactor complete.**
- **2026-06-28 — Iteration 34 (Phase 4: loose-coupling / import audit + tidy).**
  Mapped the gitk load-time import graph via AST and confirmed it is a DAG (no
  cycles). The only genuine back-edges — items↔jobs and items↔segments — are
  broken with function-local imports (DiffListItem→jobs, RefSegment→items),
  documented inline. Confirmed no module imports a concrete sibling view (each
  view reaches its siblings through `app`); modules depend only on lower-layer
  base classes/helpers + the injected App. Removed genuinely-unused imports
  (view.py KEY_CTRL/copy_to_clipboard — moved to list_view; jobs.py ID_GIT_LOG —
  passed in by the view) and added `__all__` to views/__init__. Full suite:
  **60 passed, 0 failed**; goldens clean; units 9/9.
- **2026-06-28 — Iteration 33 (Phase 4: add unit tests for pure pieces).**
  Added `test/test_units.py` (9 pytest tests, no terminal): KEY_CTRL control
  codes, DEFAULT_CONFIG shape, get_config_path suffix, load_config
  (defaults-are-a-copy / merge-known-keys-only / tolerates-corrupt-json),
  save_config roundtrip (incl. makedirs; success path never touches `app`), and
  ref_color_and_title per ref type + the HEAD `->` arrow. The file puts the repo
  root on sys.path so `import gitk.*` works under any invocation. `pytest test/`
  collects 69 (60 golden cases + 9 units) — all pass; `python3 test/run.py`
  still **60 passed, 0 failed**, goldens clean. Satisfies exit criterion 9.
- **2026-06-28 — Iteration 32 (Phase 4: split `views.py` → `gitk/views/` package).**
  Converted the 658-line `gitk/views.py` module into a `gitk/views/` package:
  one module per view (git_log 358, context_menu 163, git_diff 109, log 33,
  git_refs 27) + `__init__.py` re-exporting all 5 (so `from gitk.views import
  GitLogView` etc. is unchanged for importers — app.py annotations, main.py).
  Each view is independent (no cross-view class refs; siblings via app at
  runtime), so per-module imports were derived mechanically from usage and
  mapped to source modules. setup.py find_packages now yields
  ['gitk','gitk.views']. ALL modules are now ≤ ~600 lines — exit criterion 4
  MET. AST check clean; full suite: **60 passed, 0 failed**; goldens clean.
- **2026-06-28 — Iteration 31 (Phase 4: split `items.py` → items + segmented_items).**
  Moved the 6 SegmentedListItem-family classes + `button_row` into
  `gitk/segmented_items.py` (imports `Item` from gitk.items + segments + Screen +
  ENTER_KEYS — one-way, no cycle). items.py (plain items) dropped to 341 lines,
  segmented_items 330. Trimmed items.py's now-unused segment-class import to just
  `ref_color_and_title`. Repointed the segmented-class importers: view (→
  WindowTopBarItem), jobs (→ CommitListItem), dialogs (→ SegmentedListItem,
  PreferenceRow, button_row), views (→ WindowTopBarItem, CommitListItem,
  UncommittedChangesListItem). AST check clean. Full suite: **60 passed, 0
  failed**; goldens clean.
- **2026-06-28 — Iteration 30 (Phase 4: split `view.py` → view + list_view).**
  `gitk/view.py` was 743 lines (> ~600 cap). Split `ListView` and the
  `_raise_split_sibling` helper into `gitk/list_view.py` (imports `View` +
  constants from gitk.view — one-way, no cycle). view.py now 426, list_view.py
  333. Repointed the two importers (`dialogs.py`, `views.py`) to
  `from gitk.list_view import ...`. STRUCTURE.md updated to list both modules.
  Full suite: **60 passed, 0 failed**; goldens clean.
- **2026-06-28 — Iteration 29 (Phase 3: `gitk/main.py` + thin shim + packaging).**
  Moved `launch_curses` + `main` into `gitk/main.py` (imports its deps directly
  from the gitk modules). Reduced `gitkcli.py` to a 9-line shim
  (`from gitk.main import main`); DELETED the whole re-export crutch block —
  confirmed no `gitk` module imports from `gitkcli` (only `gitk.config`'s
  config-dir name string mentions it). Updated setup.py:
  `packages=find_packages(include=["gitk","gitk.*"])` (→ `['gitk']`) and console
  script `gitkcli=gitk.main:main`; kept `py_modules=["gitkcli"]` for the
  `python3 gitkcli.py` path. AST check clean (only annotation-only flags).
  `import gitkcli` works, `--help` works. Full suite: **60 passed, 0 failed**;
  goldens clean. Phase 3 complete — the package is now self-contained.
- **2026-06-28 — Iteration 28 (Phase 2: extract `gitk/log.py` + `gitk/app.py`).**
  `Log` → gitk/log.py (imports LogView + TextListItem + datetime). `App` →
  gitk/app.py. App's only runtime deps are `Job` (run_git) and `KeyboardState`
  (open_search synthesizes `KeyboardState(ord('/'))`); the AST check flagged
  KeyboardState and I added the import — all the other capitalised names
  (Screen/MouseState/Log/GitLogView/…/dialogs) are `self.x:Type=None` attribute
  annotations that are never evaluated. Used App-class-only range (crng
  over-ran to EOF because launch_curses/main are `def`s, not classes). gitkcli.py
  re-exports Log+App; launch_curses/main still resolve everything. Full suite:
  **60 passed, 0 failed**; goldens clean. gitkcli.py 392→215.
- **2026-06-28 — Iteration 27 (Phase 2: extract `gitk/views.py`).**
  Moved the 5 concrete views (GitLogView, GitDiffView, GitRefsView, LogView,
  ContextMenu) into `gitk/views.py`. They import the lower layers
  (view/items/segments/jobs/dialogs/ids/Screen/config/input) and reach each
  other via the app at runtime; nothing imports views.py back, so no cycle. AST
  check clean for views.py (remaining flags are annotation-only: Item/View/
  SearchDialogPopup). gitkcli.py re-exports the views. Full suite: **60 passed,
  0 failed**; goldens clean. gitkcli.py 1018→392 (now just Log, App,
  launch_curses, main + the re-export block).
- **2026-06-28 — Iteration 26 (Phase 2: extract `gitk/dialogs.py`).**
  Moved the 10 modal dialog popups (contiguous block) into `gitk/dialogs.py`.
  Audit confirmed NO references to the concrete view classes (dialogs subclass
  ListView and reach views via app at runtime), so no cycle — dialogs import
  only view/items/segments/jobs/screen/config/input/ids. The AST undefined-name
  check caught 3 runtime names the class-only audit missed (`KEY_ENTER`,
  `KEY_RETURN`, `KeyboardState` — used in button callbacks / key handling);
  added them. `Item` flag is annotation-only (lazy). gitkcli.py re-exports the
  dialogs. Full suite: **60 passed, 0 failed**; goldens clean. gitkcli.py
  1544→1018. (ContextMenu kept with the views for the next iteration.)
- **2026-06-28 — Iteration 25 (Phase 2: extract `gitk/ids.py` + `gitk/jobs.py`).**
  Moved all 17 `ID_*` string constants to a new leaf `gitk/ids.py` (so jobs/
  views/dialogs share them without importing the entry module), then the 6 job
  classes (contiguous block) to `gitk/jobs.py`, importing ids + items + stdlib
  (incl. curses/datetime, used in diff parsing/commit dates). Repointed
  DiffListItem's late `Job` import to gitk.jobs. CAUGHT a regression: first pass
  imported only TextListItem/CommitListItem/RefListItem — but GitDiffJob also
  builds `DiffListItem`/`StatListItem` rows; the missing import made the diff
  job *thread* die on NameError → 18 diff/split/jump goldens showed
  truncated/wrong output (goldens themselves untouched). Added them; wrote an
  AST undefined-name check (scratchpad) confirming no others. Full suite:
  **60 passed, 0 failed**; goldens clean. gitkcli.py 1961→1544.
- **2026-06-28 — Iteration 24 (Phase 2: extract `gitk/view.py`).**
  Moved `View`, `ListView`, the module fn `_raise_split_sibling`, and the
  view-only constants `HORIZONTAL_OFFSET_JUMP` / `SPLIT_DIVIDER_COLOR` into
  `gitk/view.py`. Imports Screen, KEY_CTRL/copy_to_clipboard, and the item types
  the base classes instantiate (WindowTopBarItem/SpacerListItem/TextListItem)
  from gitk.items — no cycle (items doesn't import view). `SearchDialogPopup`
  refs are non-evaluated annotations (attribute annotation + quoted param), so
  no import needed. Capped ListView's range at the `_raise_split_sibling` def
  (same overlap lesson as button_row). gitkcli.py re-exports the names +
  constants. Full suite: **60 passed, 0 failed**; goldens clean. gitkcli.py
  2684→1961; view.py 743 (> cap → split in Phase 4).
- **2026-06-28 — Iteration 23 (Phase 2: extract `gitk/items.py`).**
  Moved the 16 item classes (Item + plain items + the SegmentedListItem family)
  and the `button_row` helper into `gitk/items.py`, in dependency order. Imports
  segments, Screen, `copy_to_clipboard`, and input key constants. `DiffListItem`
  uses a function-local `from gitkcli import Job` (Job not yet extracted);
  `RefSegment`'s late import repointed to `gitk.items`. Audited deps: the only
  real runtime cross-ref was Job; GitLogView/View/ListView "refs" were all
  comments. FIRST attempt corrupted `View.__init__` — `ButtonRowItem`'s computed
  range (next *class*) overlapped the intervening `button_row` *def*, so
  highest-first deletion mis-shifted; reverted and redid with explicit
  non-overlapping ranges (ButtonRowItem capped at the def, button_row its own
  block) + an overlap assertion. Full suite: **60 passed, 0 failed**; goldens
  clean. gitkcli.py 3308→2684; items.py 654 (> ~600 cap → split in Phase 4).
- **2026-06-28 — Iteration 22 (Phase 2: extract `gitk/segments.py`).**
  Gathered the 11 segment classes (scattered across 5 source regions, dependency
  order Segment→…→ChoiceSegment) plus the `ref_color_and_title` helper into
  `gitk/segments.py`, via an extraction script (ralph scratchpad). Imports
  `Screen` from gitk.screen for the colour palette. The one upward dep —
  `RefSegment.handle_mouse_input` creating a `RefListItem` for the context menu —
  is handled with a function-local `from gitkcli import RefListItem` (late
  binding, no import cycle; updates to `gitk.items` when items move). gitkcli.py
  re-exports all segment names + the helper. Full suite: **60 passed, 0
  failed**; goldens clean. gitkcli.py 3531→3308 lines; segments.py 245.
- **2026-06-28 — Iteration 21 (Phase 2 prep: decouple ref formatter from view).**
  Relocated `GitRefsView.get_ref_color_and_title` (classmethod) to a pure
  module-level function `ref_color_and_title(ref, head_branch='')`. Updated its
  two callers (`RefListItem.draw_line`, `RefSegment.__init__`). This removes the
  segment→view dependency that would have blocked a clean `gitk/segments.py`
  extraction (a segment no longer reaches into `GitRefsView`). No behavior
  change — same colour/title logic. Full suite: **60 passed, 0 failed**; goldens
  clean. gitkcli.py 3531→3531 (net ~0; logic moved, not added).
- **2026-06-28 — Iteration 20 (Phase 2: extract `gitk/screen.py`).**
  Moved the `Screen` class (331 lines) into `gitk/screen.py`. Audited it as a
  clean near-leaf: the only seeming external refs were a `'Git Log'` string
  label and an `App` mention in a comment — no real class deps. Uses curses/
  curses.panel/os/re/time/typing only; view/item objects are duck-typed at
  runtime, so `from __future__ import annotations` + no UI imports. gitkcli.py
  re-exports `Screen` (used by `launch_curses`, `Screen.color(...)` in draw
  methods, and `Screen.force_mono` in main). Full suite: **60 passed, 0
  failed**; goldens clean. gitkcli.py 3860→3531 lines.
- **2026-06-28 — Iteration 19 (Phase 2: extract `gitk/input.py`).**
  Moved `KeyboardState`, `MouseState`, and the keyboard constants
  (`KEY_SHIFT_F5`, `KEY_CTRL_LEFT/RIGHT/BACKSPACE/DEL`, `KEY_ENTER`,
  `KEY_RETURN`, `KEY_TAB`, `ENTER_KEYS`) into `gitk/input.py`. Added
  `from __future__ import annotations` so the one unquoted hint
  (`process_mouse_event(active_view:View)`) and the quoted `'View|None'` /
  `'Item|None'` field hints don't require importing the (not-yet-extracted) UI
  classes — they're duck-typed via `self.app` at runtime. gitkcli.py re-exports
  the names. Full suite: **60 passed, 0 failed**; goldens clean. gitkcli.py
  4083→3860 lines. Gotcha noted: `Log` (next on the plan's list) is not a leaf
  (needs LogView/TextListItem), so Phase 2 order will follow real deps:
  segments → items → view → views, then log/screen/jobs.
- **2026-06-28 — Iteration 18 (Phase 2 start: extract `gitk/config.py`).**
  Created the `gitk/` package (`__init__.py`) and moved the config cluster into
  `gitk/config.py`: `get_config_path`, `load_config`, `save_config`,
  `copy_to_clipboard`, `DEFAULT_CONFIG`, and the `KEY_CTRL` helper. It's a leaf
  module (stdlib only; log-needing fns take `app`). gitkcli.py replaces the
  definitions with `from gitk.config import (...)` so all bare-name call sites
  keep resolving. Left the `KEY_*`/`ENTER_KEYS` constants in gitkcli.py for the
  upcoming `gitk/input.py`. Verified `import gitkcli`, `import gitk.config`,
  `--help`, and re-export visibility. Full suite: **60 passed, 0 failed**;
  goldens clean. gitkcli.py 4129→4083 lines.
- **2026-06-28 — Iteration 17 (Phase 1 DONE: constructor injection, bridge removed).**
  Injected `app` as the first constructor param across the whole View hierarchy
  (`View`/`ListView` + all 14 subclasses), `Screen`, and `Log`, threading it
  through each `super().__init__(app, ...)` and updating every construction site
  to pass `app`/`self.app`. Then DELETED the `Gitkcli` bridge global and the
  `self.app = Gitkcli` reads (now `= app`); `Log.success/error` use
  `getattr(self.app, ...)`. Reworded all stale comments/docstring. Applied via a
  reviewable transform script (ralph scratchpad). Two misses caught by the suite
  and fixed: `Log()` and a `GitSearchDialogPopup(app)` inside `set_pref_flags`
  (not `__init__`, so no `app` local → `self.app`). Result:
  `grep -n 'Gitkcli' gitkcli.py` → **nothing**. **Phase 1 complete** — the
  service locator is fully dissolved into an injected `App` struct. Full suite:
  **60 passed, 0 failed**; goldens clean. gitkcli.py 4135→4129 lines.
- **2026-06-28 — Iteration 16 (Phase 1: entry point → local `app`).**
  Converted every `Gitkcli.<x>` in `launch_curses` + the main loop to a local
  `app` (created `app = App()`; the bridge `Gitkcli = app` is retained for now
  because Screen/View/Item constructors still read it to set `self.app`). Fixed
  the two doc-comment prose lines that still wrote `Gitkcli.<x>`. Result:
  `grep -c 'Gitkcli\.' gitkcli.py` = **0** — exit criterion 2 satisfied.
  `python3 gitkcli.py --help` works, import clean. Full suite: **60 passed, 0
  failed**; goldens clean. Bridge global removal (exit criterion 5) deferred to
  the next iteration via constructor injection.
- **2026-06-28 — Iteration 15 (Phase 1: thread `app` through `Job.run_job`).**
  `Job.run_job` is a classmethod (no `self`); gave it an `app` param —
  `run_job(cls, app, args)` logs via `app.log.info`. Updated all 16 call sites
  to pass their app handle: `self.app` (GitRefsJob/GitLogView/ContextMenu/
  RefPushDialogPopup), the `app` local (DiffListItem.jump_to_origin), and `self`
  (App.run_git, where self is the App). Verified RefPushDialogPopup.__init__
  calls `super().__init__` (which sets self.app) before its run_job. This clears
  the LAST non-entry-point `Gitkcli.` reference. Full suite: **60 passed, 0
  failed**; goldens clean. `Gitkcli.` refs 64→63 (now only 61 entry-point + 2
  doc-prose).
- **2026-06-28 — Iteration 14 (Phase 1: migrate module-fn helpers + a classmethod).**
  `copy_to_clipboard` and `save_config` now take an explicit `app` arg (passed
  by callers that have `self.app`/`get_app()`).
  `GitRefsView.get_ref_color_and_title` is a classmethod (no `self`) called once
  at `RefSegment.__init__` time (before the segment is wired), so instead of an
  app handle it now takes `head_branch` as plain data — threaded from
  `RefListItem.draw_line` (`self.get_app().git_log.head_branch`) and
  `CommitListItem.get_segments` (passes `app.git_log.head_branch` into
  `RefSegment`). This fully decouples the pure color/title formatter from the
  global. Full suite: **60 passed, 0 failed**; goldens clean. `Gitkcli.` refs
  68→64. Only the `Job.run_job` classmethod ref (16 call sites) and the entry
  point now remain.
- **2026-06-28 — Iteration 13 (Phase 1: migrate MouseState/KeyboardState).**
  Both are `@dataclass`es and `KeyboardState` is constructed in ~6 places as
  synthetic single-positional key events, so adding `app` as a *field* would
  break those. Instead added an UNANNOTATED `app = None` class attribute to each
  (dataclass ignores unannotated names → no new field), set it on the single
  real `mouse`/`keyboard` in launch_curses, and migrated their instance-method
  refs (`read`, `read_curses_event`, `process_mouse_event`) to `self.app`. The
  synthetic KeyboardState instances keep `app=None` but never call the logging
  `read()`. Full suite: **60 passed, 0 failed**; goldens clean. `Gitkcli.` refs
  73→68 (7 helper refs migrated; +2 new `Gitkcli.mouse/keyboard.app =` lines in
  the entry point, which convert to `app.` in Phase 3).
- **2026-06-28 — Iteration 12 (Phase 1: migrate Git*Job classes to self.app).**
  Jobs have no parent chain (not items), so they hold `app` directly: added an
  `app` first param to `Job.__init__` and threaded it through every subclass
  constructor (`GitLogJob`, `GitRefreshHeadJob`, `GitDiffJob`, `GitSearchJob`,
  `GitRefsJob`); the owning views pass `self.app` at the 5 construction sites.
  Migrated all instance-method refs (start_job/process_item/process_message/
  stop_job/_get_args/_prepare/show_*/_restore_on_finished, incl. a lambda and a
  comment) to `self.app`. DEFERRED the single `Job.run_job` classmethod ref
  (`Gitkcli.log.info`) — a classmethod has no `self`; needs a dedicated step.
  Full suite: **60 passed, 0 failed**; goldens clean. `Gitkcli.` refs 115→73.
- **2026-06-28 — Iteration 11 (Phase 1: migrate segment clusters to `get_app()`).**
  `RefSegment.handle_mouse_input` → `self.get_app()` (right-click context menu +
  tag-annotation double-click). `SplitButtonSegment`: `get_text` →
  `self.get_app().split_mode`, and the constructor callback deferred from
  `Gitkcli.cycle_split_view` (resolved at construction, before `_item` wiring)
  to `lambda: self.get_app().cycle_split_view()` (resolved at click, after
  wiring). Verified both segment types are only instantiated where `_item` gets
  wired (RefSegment in CommitListItem.get_segments; SplitButtonSegment in the
  GitLogView header). Full suite: **60 passed, 0 failed**; goldens clean.
  `Gitkcli.` refs 119→115.
- **2026-06-28 — Iteration 10 (Phase 1: migrate item clusters to `get_app()`).**
  Migrated all six remaining item classes off the global, introducing an `app =
  self.get_app()` local where a method had several refs (readability):
  `CommitListItem` (get_segments/draw_line/load_to_view/activate),
  `UncommittedChangesListItem`, `WindowTopBarItem` (the `[X]` close lambda now
  `lambda: self.get_app()...`, closing over self — runs after set_header_item
  wires `_view`; + double-click handler), `DiffListItem.jump_to_origin` (incl.
  the nested `on_finished` closure), `StatListItem.jump_to_file`,
  `ResetModeItem.activate`. Also back-wired `_item` on the segments
  `CommitListItem.get_segments` rebuilds each call (they aren't the wired
  `self.segments`), so a future `RefSegment` migration can use `get_app()`.
  Full suite: **60 passed, 0 failed**; goldens clean. `Gitkcli.` refs 143→119.
- **2026-06-28 — Iteration 9 (Phase 1: build `get_app()` chain for items/segments).**
  Added the parent back-reference chain so items/segments reach `app` without
  the global: `Item._view` (+ `Item.get_app()` → `self._view.app`),
  `Segment._item` (+ `Segment.get_app()` → `self._item.get_app()`). Wired
  `_view` in `ListView.append`, the two `GitLogView` `self.items.insert` sites
  (uncommitted pseudo-rows + `prepend_commit`), and `set_header_item`; wired
  `_item` for all segments in `SegmentedListItem.__init__`. Validated the chain
  end-to-end by migrating three small Item classes to `get_app()`:
  `ContextMenuItem.activate`, `RefListItem.activate`, and the `Item` base
  right-click handler (`get_app().context_menu.show_context_menu`). Full suite:
  **60 passed, 0 failed** (incl. `tag_context_menu`); goldens clean. `Gitkcli.`
  refs 148→143. Completes the Phase-1 access-path checklist item.
- **2026-06-28 — Iteration 8 (Phase 1: finish View/Screen instance-scope refs).**
  Migrated the remaining view/screen-scope clusters in one cohesive pass:
  `Screen` (12, all in `__init__` F-key lambdas + instance methods — none in its
  `cls` colour helpers), `ListView` (instance methods), `LogView`,
  `ResetDialogPopup`, `RefPushDialogPopup`, `NewRefDialogPopup`,
  `GitSearchDialogPopup`. The module-level `_raise_split_sibling(view, sibling)`
  has no `self`, so its 4 refs became `view.app.<x>` (it already receives the
  focused view). Updated the now-stale Screen bottom-bar comment. Deliberately
  SKIPPED `GitRefsView`'s 1 ref — it sits in the `get_ref_color_and_title`
  *classmethod* (no `self`); deferred to the items/jobs phase. Verified each
  range first: no `@staticmethod`/`@classmethod` in the migrated dialog ranges,
  every `def` takes `self`, lambdas close over `self`. Full suite: **60 passed,
  0 failed**; goldens clean. `Gitkcli.` refs 176→148.
- **2026-06-28 — Iteration 7 (Phase 1: migrate `PreferencesDialogPopup`).**
  Replaced all 23 `Gitkcli.<x>` → `self.app.<x>` inside `PreferencesDialogPopup`
  (2950–3043) — the apply/reset paths that read & write `git_log`/`git_diff`/
  `log`/`default_view_mode` and call `set_split_mode`. All instance methods
  (segment-setup `__init__` had no `Gitkcli.` refs). Full suite: **60 passed, 0
  failed**; goldens clean. `Gitkcli.` refs 199→176.
- **2026-06-28 — Iteration 6 (Phase 1: migrate `ContextMenu` refs to `self.app`).**
  Replaced all 22 `Gitkcli.<x>` → `self.app.<x>` inside `ContextMenu`
  (2331–2482). Also converted the main-menu sentinel `item == Gitkcli` →
  `item is self.app` (equivalent: identity on the single App instance, and now
  bridge-free). Note: no call site currently passes the App instance as the
  menu `item`, so that branch is latent in the present code — the change is
  behavior-identical whether or not it is reached. Full suite: **60 passed, 0
  failed**; goldens clean. `Gitkcli.` refs 221→199.
- **2026-06-28 — Iteration 5 (Phase 1: migrate `View` base refs to `self.app`).**
  Replaced all 41 `Gitkcli.<x>` → `self.app.<x>` inside the `View` base class
  (1083–1484). All sites are instance methods (no static/class methods; every
  `def` takes `self`), covering split geometry, window drag/resize, z-order
  (`screen.showed_views`), and logging. Full suite: **60 passed, 0 failed**;
  goldens clean. `Gitkcli.` refs 262→221.
- **2026-06-28 — Iteration 4 (Phase 1: migrate `GitLogView` refs to `self.app`).**
  Replaced all 24 `Gitkcli.<x>` → `self.app.<x>` inside `GitLogView`
  (1801–2141). Pre-checked: no `@staticmethod`/`@classmethod` in range and every
  `def` takes `self`, so `self.app` is in scope at every site (incl. App-method
  calls like `run_git`, `exit_program`, `set_split_mode`, `split_active`).
  Full suite: **60 passed, 0 failed**; goldens clean. `Gitkcli.` refs 286→262.
- **2026-06-28 — Iteration 3 (Phase 1: migrate `GitDiffView` refs to `self.app`).**
  Replaced all 12 `Gitkcli.<x>` → `self.app.<x>` inside `GitDiffView` (scoped
  `sed` over its class line range, 2160–2249). All sites are instance methods or
  `__init__` lambdas that close over `self`, so `self.app` is valid everywhere;
  the `Gitkcli.git_diff` self-references resolve to the same view object as
  before (pure mechanical rename, behavior identical). Validates the cluster
  pattern for the larger view classes. Full suite: **60 passed, 0 failed**;
  goldens clean. `Gitkcli.` refs 298→286. Lines unchanged (4089).
- **2026-06-28 — Iteration 2 (Phase 1: `self.app` access path on Screen/View).**
  Added `self.app` to `Screen.__init__` and `View.__init__`, bound from the
  transitional `Gitkcli` bridge at construction (Screen is created first, before
  any view, so `self.app.screen` is always valid). `View.__init__` now calls
  `self.app.screen.add_view(...)` (−1 `Gitkcli.` ref). All 16 view/dialog classes
  subclass `ListView`→`View`, so they inherit `self.app` with no per-class
  change. Verified the access path is live without touching call sites yet.
  Discovered the planned `get_app()` "parent-chain walk" has no existing chain
  to walk — items/segments reference `Gitkcli` directly today — so that becomes
  its own iteration (add item→view + segment→item back-refs first). Full suite:
  **60 passed, 0 failed**; goldens clean. gitkcli.py 4078→4089 lines.
- **2026-06-28 — Iteration 1 (Phase 1 start: `Gitkcli` class → `App` instance).**
  Converted the `Gitkcli` service-locator class into an `App` *instance* class:
  the 10 `@classmethod`s became plain instance methods (`cls`→`self`), and the
  class attributes (`running`, split state, component slots) moved into
  `__init__`. `launch_curses` now does `global Gitkcli; Gitkcli = App()` before
  any view is built, so the bridge is live before `SplitButtonSegment.__init__`
  (the earliest `Gitkcli.<x>` access during view construction) runs. All 297
  code call sites resolve through the bridge unchanged — attribute/bound-method
  access on the instance is identical to the old classmethod access. The
  `item == Gitkcli` main-menu sentinel still works (identity preserved: the
  same single instance is the sentinel). Full suite: **60 passed, 0 failed**;
  `git status test/cases` clean. gitkcli.py 4066→4078 lines. Gotcha:
  `grep -c 'Gitkcli\.'` rose 297→299 because the new bridge docstring/comment
  mentions `Gitkcli.<x>` in prose — those 2 are comments, not code, and vanish
  with the bridge in Phase 3.
- **2026-06-28 — Iteration 0 (setup).** Created branch `refactor-modularize`
  (the planned `refactor/modularize` is blocked by an existing `refactor`
  branch — git can't nest a ref under an existing leaf ref). Tagged
  `refactor-baseline` at the current master HEAD. Ran the full golden suite:
  **60 passed, 0 failed**. `gitkcli.py` = 4066 lines (baseline doc said 3987;
  file grew on master since the doc was written), `Gitkcli.` refs = 297,
  `class Gitkcli` = 1. Committed plan files. Next: Phase 1 — introduce the `App`
  instance + access path.

## BLOCKED
- _(none)_
