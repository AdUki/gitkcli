"""GitRefsView: the branches/tags/refs pane."""

from __future__ import annotations

from tuikit.dialogs import SearchDialog
from tuikit.list_view import ListView
from tuikit.segmented import WindowTopBarItem

from gitk.dialogs import NewRefDialogPopup, RefPushDialogPopup
from gitk.ids import ID_GIT_REFS, ID_GIT_REFS_SEARCH
from gitk.jobs import GitRefsJob
from gitk.screen import Screen


class GitRefsView(ListView):
    def __init__(self, app):
        super().__init__(app, ID_GIT_REFS)

        self.refs = {}  # map: git_id --> [ { 'type':<ref-type>, 'name':<ref-name> } ]

        self.set_header_item(
            WindowTopBarItem("Git references", title_color=Screen.C_DATA)
        )
        self.set_search_dialog(SearchDialog(app, ID_GIT_REFS_SEARCH))

        self.new_ref_dialog = NewRefDialogPopup(app)
        self.ref_push_dialog = RefPushDialogPopup(app)

        self.job = GitRefsJob(self.app)

    def reload_refs(self):
        self.clear()
        self.job.start_job()
