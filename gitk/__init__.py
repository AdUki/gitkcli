"""gitkcli's application package.

The whole app lives here; `gitkcli.py` at the repo root is just a launch shim
(`from gitk.main import main`). Modules are layered bottom-up (the import graph
is a DAG):

    config, ids, input, screen   leaves (stdlib + curses only)
    segments                     -> screen
    items                        -> segments, screen, config, input
    segmented_items              -> items, segments, screen, input
    view                         -> screen, segmented_items
    list_view                    -> view, items, screen, config
    jobs                         -> ids, items, segmented_items
    dialogs                      -> list_view, items, segmented_items, segments, jobs, ...
    views/ (git_log, git_diff, git_refs, log, context_menu)
                                 -> list_view, dialogs, jobs, items, segments, ...
    log                          -> views (LogView), items
    app                          -> jobs, input           (the App struct)
    main                         -> app + everything for launch_curses

State is reached through an injected `App` struct (`self.app`), not a global:
Screen/views/jobs/log receive it at construction; items reach it via
`Item.get_app()` (a `_view` back-ref), segments via `Segment.get_app()` (an
`_item` back-ref). Two genuine import cycles are broken with function-local
imports: `segments.RefSegment` -> `gitk.items.RefListItem`, and
`items.DiffListItem` -> `gitk.jobs.Job`.
"""
