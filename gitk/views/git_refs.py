"""GitRefsView: the branches/tags/refs pane."""

from __future__ import annotations

from gitk.dialogs import NewRefDialogPopup, RefPushDialogPopup, SearchDialogPopup
from gitk.ids import ID_GIT_REFS, ID_GIT_REFS_SEARCH
from gitk.jobs import GitRefsJob
from gitk.list_view import ListView
from gitk.segmented_items import WindowTopBarItem


class GitRefsView(ListView):
    def __init__(self, app):
        super().__init__(app, ID_GIT_REFS)

        self.refs = {}  # map: git_id --> [ { 'type':<ref-type>, 'name':<ref-name> } ]

        self.set_header_item(WindowTopBarItem("Git references", title_color=5))
        self.set_search_dialog(SearchDialogPopup(app, ID_GIT_REFS_SEARCH))

        self.view_new_ref = NewRefDialogPopup(app)
        self.view_ref_push = RefPushDialogPopup(app)

        self.job = GitRefsJob(self.app)

    def reload_refs(self):
        self.clear()
        self.job.start_job()
