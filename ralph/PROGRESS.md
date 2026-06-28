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
- **Phase:** 2 — in progress. Extracted: `gitk/config.py`, `gitk/input.py`,
  `gitk/screen.py`, `gitk/segments.py`, `gitk/items.py` (16 item classes +
  `button_row`; imports segments/Screen/config; `DiffListItem` uses a
  function-local `from gitkcli import Job`). `RefSegment`'s late import now
  points at `gitk.items`. gitkcli.py re-exports each.
  NOTE: items.py is 654 lines (> ~600 cap) — split into items.py +
  segmented_items.py in Phase 4 (per STRUCTURE.md).
- **NEXT (Phase 2):** `gitk/view.py` — `View` + `ListView` (+ the module fn
  `_raise_split_sibling`). These import Screen + curses; they reference items
  (WindowTopBarItem isinstance, TextListItem, ButtonRowItem, SpacerListItem,
  search dialogs) in method bodies (late-bound → local imports / re-export).
  Then `gitk/views/` (git_log, git_diff, git_refs, log), `gitk/dialogs/`,
  `gitk/jobs.py`, `gitk/log.py`, `gitk/app.py`, `gitk/main.py`.
- **gitkcli.py:** 2684 lines · **package:**
  `gitk/{__init__,config,input,screen,segments,items}.py` (screen 347,
  segments 245, items 654) · **Gitkcli refs:** 0.

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
- [ ] `gitk/log.py`
- [x] `gitk/screen.py` — Screen (curses/panel lifecycle, colour palette, panel
      deck, bottom bar). Clean near-leaf; view/item refs duck-typed at runtime.
- [ ] `gitk/jobs.py`
- [x] `gitk/segments.py` — Segment + 10 subclasses + ref_color_and_title.
      Imports Screen; RefSegment uses a function-local `from gitkcli import
      RefListItem` to break the segment↔item cycle.
- [x] `gitk/items.py` — Item + 15 subclasses + button_row (incl. the
      SegmentedListItem family; STRUCTURE's separate segmented_items.py folded in
      for now). 654 lines → split in Phase 4. DiffListItem uses a function-local
      `from gitkcli import Job`.
- [ ] `gitk/segmented_items.py` — DEFERRED: folded into items.py; split out in
      Phase 4 to respect the ~600-line cap.
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
