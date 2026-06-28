# Target module structure (reference for Phase 2/3)

Guidance, not gospel — if a cleaner cut emerges while moving code, take it and
note the deviation in PROGRESS.md. Keep each file cohesive and ≤ ~600 lines.

```
gitkcli.py                 # THIN entry shim (final): `from gitk.main import main`
gitk/
  __init__.py              # package marker; may re-export `main`
  config.py                # get_config_path, load_config, save_config,
                           #   copy_to_clipboard, KEY_CTRL
  input.py                 # KeyboardState, MouseState
  log.py                   # Log
  screen.py                # Screen
  jobs.py                  # Job (base + manager) + Git*Job subclasses
                           #   (promote to gitk/jobs/ if > ~600 lines)
  segments.py              # Segment, FillerSegment, TextSegment, RefSegment,
                           #   ButtonSegment, ToggleSegment, SplitButtonSegment,
                           #   DynamicTextSegment, HighlightToggleSegment,
                           #   OnOffToggleSegment, ChoiceSegment
  items.py                 # Item, SeparatorItem, RefListItem, TextListItem,
                           #   SpacerListItem, StatListItem, DiffListItem,
                           #   ContextMenuItem, UserInputListItem, ResetModeItem
  segmented_items.py       # SegmentedListItem, ButtonRowItem (+ button_row),
                           #   WindowTopBarItem, UncommittedChangesListItem,
                           #   CommitListItem, PreferenceRow
  view.py                  # View (base) + view constants
  list_view.py             # ListView (+ _raise_split_sibling helper)
  views/
    __init__.py
    git_log.py             # GitLogView
    git_diff.py            # GitDiffView
    git_refs.py            # GitRefsView
    log.py                 # LogView
  dialogs/
    __init__.py
    base.py                # _RedMessageBoxPopup, UserInputDialogPopup
    context_menu.py        # ContextMenu, ContextMenuItem (if not in items.py)
    confirm.py             # ConfirmDialogPopup
    error.py               # ErrorDialogPopup
    preferences.py         # PreferencesDialogPopup, PreferenceRow
    reset.py               # ResetDialogPopup, ResetModeItem
    ref_push.py            # RefPushDialogPopup
    new_ref.py             # NewRefDialogPopup
    search.py              # SearchDialogPopup, GitSearchDialogPopup
  app.py                   # App struct (was Gitkcli) + SplitLayout helper
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
