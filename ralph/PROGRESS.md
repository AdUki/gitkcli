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

- **Phase:** 1 — in progress. Migrating view-method `Gitkcli.<x>` → `self.app.<x>`
  cluster by cluster. View/Screen instance-scope migration COMPLETE: done
  `GitDiffView`, `GitLogView`, `View` base, `ContextMenu`,
  `PreferencesDialogPopup`, `Screen`, `ListView` (+ module fn
  `_raise_split_sibling` via `view.app`), `LogView`, `ResetDialogPopup`,
  `RefPushDialogPopup`, `NewRefDialogPopup`, `GitSearchDialogPopup`. Remaining
  `Gitkcli.` refs are now in: items/segments (migrating via the new
  `get_app()`), jobs (no view; need a different access path), one `GitRefsView`
  classmethod, `MouseState`/`KeyboardState` helpers, and the
  App/launch_curses/main-loop entry point (~61). `get_app()` chain is BUILT and
  validated. Item clusters DONE: CommitListItem, UncommittedChangesListItem,
  WindowTopBarItem, DiffListItem, StatListItem, ResetModeItem (+ earlier
  ContextMenuItem, RefListItem, Item base). Segment clusters DONE: RefSegment,
  SplitButtonSegment (callback deferred via lambda). Next: the GitRefsView
  classmethod; jobs (GitDiffJob, GitRefsJob, GitLogJob, GitRefreshHeadJob,
  GitSearchJob, Job); MouseState/KeyboardState; the copy_to_clipboard module fn;
  and the App/launch_curses/main-loop entry point. After items/segments/jobs,
  ALL remaining refs should be in the entry point (become `app.`) + a few
  helpers that need an app handle passed in.
- **gitkcli.py:** 4119 lines · **Gitkcli. refs:** 115 (code; +2 doc-prose) ·
  **`class Gitkcli`:** 0 · **package:** not created.

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
- [ ] Migrate the 10 classmethods to `App` methods (consider `SplitLayout`).
- [ ] Replace all `Gitkcli.<x>` references with injected access, cluster by
      cluster (e.g. one iteration: all `Gitkcli.git_diff` in the diff view).
- [ ] Remove the `Gitkcli` class. Confirm `grep -c 'Gitkcli\.' gitkcli.py` = 0.
      Suite green throughout.

## Phase 2 — Extract clusters into `gitk/` (one per iteration, re-export crutch)

- [ ] `gitk/config.py`
- [ ] `gitk/input.py`
- [ ] `gitk/log.py`
- [ ] `gitk/screen.py`
- [ ] `gitk/jobs.py`
- [ ] `gitk/segments.py`
- [ ] `gitk/items.py`
- [ ] `gitk/segmented_items.py`
- [ ] `gitk/view.py`
- [ ] `gitk/views/` (git_log, git_diff, git_refs, log)
- [ ] `gitk/dialogs/` (base, context_menu, confirm, error, preferences, reset,
      ref_push, new_ref, search)
- [ ] `gitk/app.py` (App + SplitLayout)
- [ ] `gitk/main.py` (launch_curses, main)

## Phase 3 — Thin entry point & packaging

- [ ] `gitkcli.py` reduced to the thin shim (≤ ~15 lines).
- [ ] Remove `from gitk... import *` re-export crutches.
- [ ] Update `setup.py` / `pyproject.toml`; console script + `python3
      gitkcli.py` both work.

## Phase 4 — Loose-coupling, readability, new tests

- [ ] Cross-module import audit (base classes + App only; no sibling internals).
- [ ] Every module ≤ ~600 lines (or documented exception below).
- [ ] Introduce passed structs/dataclasses where it improves clarity (no
      behavior change).
- [ ] Add unit tests for pure pieces (config parsing, KEY_CTRL, job line
      parsers, segment geometry).

## Exit-criteria check (fill in when claiming completion)

- [ ] `grep -rn 'class Gitkcli' .` → none
- [ ] `grep -rn 'Gitkcli\.' gitkcli.py gitk/` → 0
- [ ] `gitkcli.py` is a thin shim; code in `gitk/` modules
- [ ] no module > ~600 lines (exceptions: …)
- [ ] full golden suite passes; `git status test/cases` clean vs baseline
- [ ] `import gitkcli` works; console script works
- [ ] all tasks above checked; STRUCTURE.md matches reality
- [ ] added unit tests pass

---

## Log (newest first)

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
