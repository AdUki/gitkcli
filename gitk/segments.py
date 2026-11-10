"""gitk's segments: git refs on a commit row, and the split-view button. The
generic segments (text, buttons, toggles, ...) come from tuikit.segments.
"""

from __future__ import annotations

from tuikit import mouse as M
from tuikit.segments import TextSegment

from gitk.ids import ID_GIT_REFS
from gitk.screen import Screen


def ref_color_and_title(ref, head_branch=""):
    """Colour pair and display title for a git ref record. Pure: depends only on
    the ref dict and (for the current HEAD) the branch name. Used by RefSegment
    and RefListItem so neither has to reach into GitRefsView."""
    title = f"({ref['name']})"
    color = Screen.C_REF_LOCAL
    if ref["type"] == "head":
        color = Screen.C_HEAD
        if head_branch:
            title += " ->"
    elif ref["type"] == "heads":
        title = f"[{ref['name']}]"
    elif ref["type"] == "remotes":
        color = Screen.C_REF_REMOTE
        title = f"{{{ref['name']}}}"
    elif ref["type"] == "tags":
        color = Screen.C_TAG
        title = f"<{ref['name']}>"
    elif ref["type"] == "stash":
        color = Screen.C_STASH
    return color, title


class RefSegment(TextSegment):
    def __init__(self, ref, head_branch=""):
        self.ref = ref
        color, txt = ref_color_and_title(ref, head_branch)
        super().__init__(txt, color)

    def get_context_menu(self):
        from gitk.items import RefListItem  # late import: avoids segments<->items cycle

        return (RefListItem(self.ref), ID_GIT_REFS)

    def handle_mouse(self, event) -> bool:
        if event.type == M.RIGHT_CLICK:
            return self.get_app().context_menu.show_context_menu(
                *self.get_context_menu()
            )
        if event.type == M.DOUBLE_CLICK and "tag_id" in self.ref:
            self.get_app().git_diff.show_tag_annotation(self.ref["tag_id"])
            return True
        return super().handle_mouse(event)
