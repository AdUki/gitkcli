"""gitkcli's application package.

The whole app lives here; `gitkcli.py` at the repo root is just a launch shim
(`from gitk.main import main`). The generic curses UI - views, lists, rows and
segments, dialogs, split layout, input events, the event loop - is the
`tuikit` library next to this package; gitk subclasses it with git-specific
pieces. Modules are layered bottom-up (the import graph is a DAG):

    tuikit                       the UI library (knows nothing about git)
    config, ids, diff_target     leaves
    keymap                       -> tuikit.keys (rebindable shortcuts)
    screen                       -> tuikit (git palette, function-key bar)
    segments                     -> screen (refs, split button)
    items                        -> segments, screen (refs, diff/stat rows)
    segmented_items              -> items, segments (commit rows)
    jobs                         -> ids, items, segmented_items
    dialogs                      -> jobs, items, split_layout, ...
    views/ (git_log, git_diff, git_refs, log, context_menu)
                                 -> dialogs, jobs, items, segments, ...
    log                          -> views (LogView)
    split_layout, jump_list      the log/diff pane pairing and jump history
    app                          -> jobs, split_layout, jump_list (the App)
    main                         -> app + everything for launch_curses

State is reached through an injected `App` struct (`self.app`, a tuikit.App),
not a global: Screen/views/jobs/log receive it at construction; items reach it
via `Item.get_app()` (a `_view` back-ref), segments via `Segment.get_app()`
(an `_item` back-ref). Two genuine import cycles are broken with
function-local imports: `segments.RefSegment` -> `gitk.items.RefListItem`, and
`items.DiffListItem` -> `gitk.jobs.Job`.
"""
