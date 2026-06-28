# Target module structure (reference for Phase 2/3)

Guidance, not gospel — if a cleaner cut emerges while moving code, take it and
note the deviation in PROGRESS.md. Keep each file cohesive and ≤ ~600 lines.

**As-built (2026-06-28):** the tree below reflects the real layout. Deviations
from the original idealized plan: added `ids.py` (shared identifiers, leaf) and
`list_view.py` (split from view.py); `dialogs.py` is a single module (not a
`dialogs/` package); `ContextMenu` lives in `views/context_menu.py` (grouped
with the views, not under dialogs); `OnOffToggleSegment`/`ChoiceSegment` are
in `segments.py`; `SplitLayout` was later extracted from `App` into
`split_layout.py` (the split state + tiling logic, reached as `app.split`); the
red confirm/error message boxes were split from `dialogs.py` into
`message_box.py` to keep both ≤ ~600 lines. All modules ≤ ~600 lines.

```
gitkcli.py                 # THIN entry shim: `from gitk.main import main`
gitk/
  __init__.py              # package docstring
  ids.py                   # ID_* string identifiers (views/dialogs/jobs)  [leaf]
  config.py                # get_config_path, load_config, save_config,
                           #   copy_to_clipboard, DEFAULT_CONFIG, KEY_CTRL  [leaf]
  input.py                 # KeyboardState, MouseState + KEY_*/ENTER_KEYS   [leaf]
  screen.py                # Screen                                         [leaf]
  segments.py              # Segment, FillerSegment, TextSegment, RefSegment,
                           #   ButtonSegment, ToggleSegment, SplitButtonSegment,
                           #   DynamicTextSegment, HighlightToggleSegment,
                           #   OnOffToggleSegment, ChoiceSegment, ref_color_and_title
  items.py                 # Item, SeparatorItem, RefListItem, TextListItem,
                           #   SpacerListItem, StatListItem, DiffListItem,
                           #   ContextMenuItem, UserInputListItem, ResetModeItem
  segmented_items.py       # SegmentedListItem, ButtonRowItem (+ button_row),
                           #   WindowTopBarItem, UncommittedChangesListItem,
                           #   CommitListItem, PreferenceRow
  view.py                  # View (base) + HORIZONTAL_OFFSET_JUMP/SPLIT_DIVIDER_COLOR
  list_view.py             # ListView (+ _raise_split_sibling helper)
  jobs.py                  # Job (base + registry) + Git*Job subclasses
  message_box.py           # _RedMessageBoxPopup, ConfirmDialogPopup, ErrorDialogPopup
                           #   (split from dialogs.py to keep both ≤ ~600 lines)
  dialogs.py               # UserInputDialogPopup, PreferencesDialogPopup,
                           #   NewRefDialogPopup, SearchDialogPopup, GitSearchDialogPopup,
                           #   ResetDialogPopup, RefPushDialogPopup
  views/
    __init__.py            # re-exports the 5 view classes
    git_log.py             # GitLogView
    git_diff.py            # GitDiffView
    git_refs.py            # GitRefsView
    log.py                 # LogView
    context_menu.py        # ContextMenu
  log.py                   # Log
  split_layout.py          # SplitLayout: split state + tiling (reached as app.split)
  app.py                   # App struct (was Gitkcli)
  main.py                  # launch_curses, main, curses bootstrap
```

## The App struct (replaces Gitkcli)

`App` is a plain instance created once in `launch_curses` and handed to the
components. It holds (no behavior changes, just relocation from the old
classmethods/attrs):

- infra: `screen`, `keyboard`, `mouse`, `log`
- jobs: a `JobManager` (or the `Job` registry) reference
- views: `git_log`, `git_diff`, `git_refs`
- dialogs: `context_menu`, `preferences`, `confirm_dialog`, `error_dialog`
- split state (or a `SplitLayout` sub-object): `split_mode`, `split_ratio`,
  `default_view_mode`, and the `apply_split_layout` / `set_split_mode` /
  `cycle_split_view` / `split_active` logic
- service methods: `run_git`, `refresh_all`, `open_search`,
  `open_context_menu`, `reload_refs_commits`, `exit_program`

### How components reach `App` (loose coupling, no global)
- **Views**: injected at construction (`GitLogView(app, ...)` → `self.app`).
- **Items / Segments**: via the existing parent back-reference. Add
  `get_app()` that walks the parent chain to the node holding `app`
  (Screen, or the owning View). Auto-parenting already wires the chain
  (`ListView.append/insert`, `set_header_item/set_footer_item`,
  `SegmentedListItem.__init__`). This mirrors the (now-removed) `get_screen()`
  idea but is actually wired up and used this time.

Keep the access path ONE way (don't mix injection and globals). When in doubt,
inject explicitly — "passing structs" beats hidden lookups.
