"""Commit graph engine: a port of gitk's "arc" model and lane layout.

gitk does not need `git log --topo-order` (which makes git walk the whole
history before printing the first commit). It reads the plain, streaming,
date-ordered `git log --parents` output and files every commit into an *arc*:
a run of commits where each one is the only parent of the one above it and
the only child of the one below it. Arcs form a tree (each arc hangs under the
arc of its last child) and every arc carries a *token*, a string whose sort
order is the display order. A new commit is appended to its child's arc or
starts a new one; when a commit turns out to be the parent of a commit we
have already shown (a "reversal": clock skew, or an incremental refresh that
adds new commits on top of old ones) the affected arcs are split and
re-tokened so the parent moves below all of its children. Row numbers are
then recomputed lazily, and only up to the rows somebody actually asks for.

The lane layout is lazy too: `rowidlist[row]` is the list of lines (named by
the commit each one leads to) crossing a row, ordered left to right by an
"order token". A line whose next use is more than ~110 rows away is cut: it
ends in a down arrow a few rows below its child and restarts with an up
arrow a few rows above its parent. That keeps the lane count small even for
`--all` on huge repositories, and it means the lanes of any row can be
rebuilt from a small window of rows around it, so jumping into the middle of
a million-commit history does not lay out everything above it.

The data structures and algorithms mirror gitk (see the proc named in each
docstring); the text rendering (`render_row`) is ours: one terminal line per
commit, box-drawing joins, `╱`/`╲` for a lane shifting by one column, and
a dashed `┆` where a long line was cut.
"""

from __future__ import annotations

import bisect
import typing
import unicodedata

# Layout tunables, as in gitk.
UPARROWLEN = 5  # rows of line drawn above a parent whose line was cut
DOWNARROWLEN = 5  # rows of line drawn below a child before cutting
MINGAPLEN = 100  # a line is only cut when the gap is at least this long

# The end of a line that was cut short, and the start of its continuation:
# dashed, so it still joins the line it belongs to.
CUT = "┆"
# A commit, beside its cell in the graph: ⬤┤ (the large circle, so it meets
# the line)
DOT = "⬤"

_U, _D, _L, _R = 1, 2, 4, 8
_BOX = {
    _U | _D: "│",
    _L | _R: "─",
    _U | _D | _L | _R: "┼",
    _D | _R: "┌",
    _D | _L: "┐",
    _U | _R: "└",
    _U | _L: "┘",
    _U | _D | _R: "├",
    _U | _D | _L: "┤",
    _D | _L | _R: "┬",
    _U | _L | _R: "┴",
    _U: "│",
    _D: "│",
    _L: "─",
    _R: "─",
}


def _weighted_boxes() -> typing.Dict[typing.Tuple[int, int], str]:
    """Every light/heavy box-drawing character, keyed by (light arms, heavy
    arms) as _U/_D/_L/_R masks - read from the Unicode names ("UP LIGHT AND
    LEFT HEAVY" is ┙), which cover every combination."""
    words = {"UP": _U, "DOWN": _D, "LEFT": _L, "RIGHT": _R}
    words["VERTICAL"] = _U | _D
    words["HORIZONTAL"] = _L | _R
    table = {}
    for cp in range(0x2500, 0x2580):
        name = unicodedata.name(chr(cp))[len("BOX DRAWINGS ") :].split()
        if not set(name) <= set(words) | {"LIGHT", "HEAVY", "AND"}:
            continue  # dashed, double, arc, diagonal
        arms = {"LIGHT": 0, "HEAVY": 0}
        weight = None
        for part in " ".join(name).split(" AND "):
            ws = part.split()
            weight = next((w for w in ws if w in arms), weight)
            for w in ws:
                if w in words:
                    arms[weight] |= words[w]
        table.setdefault((arms["LIGHT"], arms["HEAVY"]), chr(cp))
    return table


_WEIGHTED = _weighted_boxes()


def _marker(arms: int) -> int:
    """The heavy arm(s) marking a commit: left, or right when a line joins
    from the left, or both horizontal arms when both are taken."""
    if not arms & _L:
        return _L
    if not arms & _R:
        return _R
    return _L | _R


def node_glyph(arms: int) -> str:
    """A commit whose dot has no room beside it: its connections in light
    lines plus a heavy stub marking it (┥ ┽ ┑ ┙ ┭ ┾ ┿)."""
    heavy = _marker(arms)
    return _WEIGHTED[(arms & ~heavy, heavy)]


def strrep(n: int) -> str:
    """A string for a non-negative integer that sorts like the number."""
    if n < 16:
        return "%x" % n
    if n < 256:
        return "x%02x" % n
    if n < 65536:
        return "y%04x" % n
    return "z%08x" % n


class CommitGraph:
    def __init__(self, date_mode: bool = False):
        # --date-order: every arc gets a date token, so siblings sort by date.
        self.date_mode = date_mode

        # Per commit.
        self.parents: typing.Dict[str, typing.List[str]] = {}
        # Children, kept sorted by the token of the arc each child is on. Also
        # holds entries for parents not (yet) loaded.
        self.children: typing.Dict[str, typing.List[str]] = {}
        self.arc_of: typing.Dict[str, int] = {}  # gitk: varcid
        self.ctime: typing.Dict[str, int] = {}
        self.fake: typing.Set[str] = set()  # pseudo-rows (uncommitted changes)

        # Per arc; arc 0 is the virtual root of the arc tree.
        self.arc_start: typing.List[typing.Optional[str]] = [None]  # varcstart
        self.up = [0]  # vupptr: parent arc in the tree
        self.down = [0]  # vdownptr: first child arc
        self.left = [0]  # vleftptr: next sibling arc
        self.back = [0]  # vbackptr: previous sibling arc
        self.tok = [""]  # varctok
        self.arc_row: typing.List[typing.Optional[int]] = [None]  # varcrow
        self.arc_ix: typing.List[typing.Optional[int]] = [None]  # varcix
        self.last_ins = [0]  # vlastins
        self.arc_commits: typing.List[typing.List[str]] = [[]]  # varccommits
        self.seed_count: typing.Dict[int, int] = {}

        # Rows are valid for every arc whose token sorts before tokmod, i.e.
        # every row before rowmod; arcmod is where update_arcrows resumes.
        self.tokmod = ""
        self.arcmod = 0
        self.rowmod = 0
        # _update_arcrows stopped early (see there) and nothing changed since.
        self._resumable = False
        self.num_rows = 0  # gitk: commitidx
        self.complete = False  # the log finished streaming

        self.vrownum: typing.List[int] = []  # first row of each arc, in order
        self.varcorder: typing.List[int] = []  # arcs in display order
        self.displayorder: typing.List[typing.Optional[str]] = []
        self.parentlist: typing.List[typing.Optional[typing.List[str]]] = []
        self.cached_row: typing.Dict[str, int] = {}
        self.ordertok: typing.Dict[str, str] = {}

        # Lane layout.
        self.rowidlist: typing.List[typing.Optional[typing.List[str]]] = []
        self.rowisopt: typing.List[bool] = []
        self.rowfinal: typing.List[bool] = []
        # The glyph marking a commit (configurable: fonts differ in where they
        # put a round glyph vertically).
        self.dot = DOT
        # Bumped whenever rows (or their layout) change: callers redraw.
        self.generation = 0

    # ------------------------------------------------------------------
    # Reading commits (gitk: getcommitlines, newvarc, splitvarc, ...)

    def __contains__(self, id: str) -> bool:
        return id in self.arc_of

    def add_commit(self, id: str, parents: typing.List[str], ctime: int = 0) -> bool:
        """File one commit from `git log` output, in any order. Returns False
        for a commit already in the graph."""
        self._resumable = False  # the arc structure changes
        if id in self.arc_of:
            return False
        self.ctime[id] = ctime
        self.parents[id] = parents
        a = 0
        kids = self.children.get(id)
        if kids is None:
            self.children[id] = []
        elif len(kids) == 1:
            # Continue the only child's arc when that child has only us.
            k = kids[0]
            if len(self.parents[k]) == 1 and (
                not self.date_mode or self.arc_of[k] == len(self.tok) - 1
            ):
                a = self.arc_of[k]
        if a == 0:
            a = self._new_arc(id)
        if self.tok[a] < self.tokmod:
            self._modify_arc(a)
        self.arc_of[id] = a
        self.arc_commits[a].append(id)
        self.num_rows += 1

        for i, p in enumerate(parents):
            if i and parents.index(p) < i:
                continue  # duplicated parent
            pkids = self.children.setdefault(p, [])
            pkids.append(id)
            if len(pkids) > 1 and self._kid_tok(pkids[-2]) > self._kid_tok(id):
                pkids.sort(key=self._kid_tok)
                self.ordertok.clear()
            if p in self.arc_of:
                self._fix_reversal(p, a)
        self.generation += 1
        return True

    def _kid_tok(self, id: str) -> str:
        return self.tok[self.arc_of[id]]

    def _new_arc(self, id: str) -> int:
        """gitk: newvarc."""
        a = len(self.tok)
        kids = self.children[id]
        if not kids or self.date_mode:
            cdate = self.ctime.get(id, 0) or 0
            c = self.seed_count.get(cdate, -1) + 1
            self.seed_count[cdate] = c
            tok = "s" + strrep(cdate ^ 0xFFFFFFFF) + strrep(c)
        else:
            tok = ""
        ka = 0
        ki = None
        if kids:
            kid = kids[-1]
            k = self.arc_of[kid]
            if self.tok[k] > tok:
                ki, ka, tok = kid, k, self.tok[k]
        if ka != 0:
            kp = self.parents[ki]
            tok += strrep(len(kp) - 1 - kp.index(id))
        c = self.last_ins[ka]
        if c == 0 or tok < self.tok[c]:
            c = ka
            b = self.down[ka]
        else:
            b = self.left[c]
        while b != 0 and tok >= self.tok[b]:
            c = b
            b = self.left[c]
        if c == ka:
            self.down[ka] = a
            self.back.append(0)
        else:
            self.left[c] = a
            self.back.append(c)
        self.last_ins[ka] = a
        self.up.append(ka)
        self.left.append(b)
        if b != 0:
            self.back[b] = a
        self.tok.append(tok)
        self.arc_start.append(id)
        self.down.append(0)
        self.arc_row.append(None)
        self.arc_ix.append(None)
        self.arc_commits.append([])
        self.last_ins.append(0)
        return a

    def _split_arc(self, p: str):
        """gitk: splitvarc - make `p` the start of a new arc."""
        self._resumable = False  # the arc structure changes
        oa = self.arc_of[p]
        otok = self.tok[oa]
        ac = self.arc_commits[oa]
        i = ac.index(p) if p in ac else -1
        if i <= 0:
            return
        na = len(self.tok)
        self.tok.append(otok + "%" + strrep(i))  # "%" sorts before "0"
        self.arc_row.append(None)
        self.arc_ix.append(None)
        self.arc_commits[oa] = ac[:i]
        self.arc_commits.append(ac[i:])
        self.arc_start.append(p)
        for id in self.arc_commits[na]:
            self.arc_of[id] = na
        self.down.append(self.down[oa])
        self.last_ins.append(self.last_ins[oa])
        self.down[oa] = na
        self.last_ins[oa] = 0
        self.up.append(oa)
        self.left.append(0)
        self.back.append(0)
        b = self.down[na]
        while b != 0:
            self.up[b] = na
            b = self.left[b]
        if otok <= self.tokmod:
            self._modify_arc(oa)

    def _last_real_child(self, id: str) -> typing.Optional[str]:
        for kid in reversed(self.children.get(id, ())):
            if kid not in self.fake:
                return kid
        return None

    def _first_real_child(self, id: str) -> typing.Optional[str]:
        for kid in self.children.get(id, ()):
            if kid not in self.fake:
                return kid
        return None

    def _renumber_arc(self, a: int):
        """gitk: renumbervarc - recompute the tokens of arc `a` and of the
        arcs below it after its children changed, moving them in the tree."""
        self._resumable = False  # the arc structure changes
        tok_of, up, down, left, back = (
            self.tok,
            self.up,
            self.down,
            self.left,
            self.back,
        )
        todo = []
        isrelated = {a}
        kidchanged = {a}
        while a != 0:
            if a in isrelated:
                todo.append(a)
                for p in self.parents[self.arc_commits[a][-1]]:
                    if p in self.arc_of:
                        isrelated.add(self.arc_of[p])
            b = down[a]
            if b == 0:
                while a != 0:
                    b = left[a]
                    if b != 0:
                        break
                    a = up[a]
            a = b
        sortkids = set()
        for a in todo:
            if a not in kidchanged:
                continue
            id = self.arc_start[a]
            if len(self.children[id]) > 1:
                self.children[id].sort(key=self._kid_tok)
            oldtok = tok_of[a]
            tok = oldtok if self.date_mode else ""
            ka = 0
            kid = self._last_real_child(id)
            if kid is not None:
                k = self.arc_of[kid]
                if tok_of[k] > tok:
                    ki, ka, tok = kid, k, tok_of[k]
            if ka != 0:
                kp = self.parents[ki]
                tok += strrep(len(kp) - 1 - kp.index(id))
            if tok == oldtok:
                continue
            for p in self.parents[self.arc_commits[a][-1]]:
                if p in self.arc_of:
                    kidchanged.add(self.arc_of[p])
                else:
                    sortkids.add(p)
            tok_of[a] = tok
            b = up[a]
            if b != ka:
                if tok_of[ka] < self.tokmod:
                    self._modify_arc(ka)
                if tok_of[b] < self.tokmod:
                    self._modify_arc(b)
                c = back[a]
                d = left[a]
                if c == 0:
                    down[b] = d
                else:
                    left[c] = d
                if d != 0:
                    back[d] = c
                if self.last_ins[b] == a:
                    self.last_ins[b] = c
                up[a] = ka
                c = self.last_ins[ka]
                if c == 0 or tok < tok_of[c]:
                    c = ka
                    b = down[ka]
                else:
                    b = left[c]
                while b != 0 and tok >= tok_of[b]:
                    c = b
                    b = left[c]
                if c == ka:
                    down[ka] = a
                    back[a] = 0
                else:
                    left[c] = a
                    back[a] = c
                left[a] = b
                if b != 0:
                    back[b] = a
                self.last_ins[ka] = a
        for id in sortkids:
            if len(self.children[id]) > 1:
                self.children[id].sort(key=self._kid_tok)
        self.ordertok.clear()

    def _fix_reversal(self, p: str, a: int):
        """gitk: fix_reversal - `p`, already filed, turned out to be a parent
        of the last commit on arc `a`."""
        pa = self.arc_of[p]
        if p != self.arc_start[pa]:
            self._split_arc(p)
            pa = self.arc_of[p]
        # seeds always need to be renumbered
        if self.up[pa] == 0 or self.tok[a] > self.tok[pa]:
            self._renumber_arc(pa)

    def _modify_arc(self, a: int, lim: typing.Optional[int] = None):
        """gitk: modify_arc - rows from arc `a` (index `lim` in it) on are no
        longer valid."""
        if lim is not None:
            if self.tok[a] > self.tokmod:
                return
            if self.tok[a] == self.tokmod:
                r = self.arc_row[a]
                if r is not None and self.rowmod <= r + lim:
                    return
        self.tokmod = self.tok[a]
        self.arcmod = a
        self._resumable = False
        while a != 0 and self.arc_row[a] is None:
            a = self.up[a]
            lim = None
        r = 0
        if a != 0:
            if lim is None:
                lim = len(self.arc_commits[a])
            r = self.arc_row[a] + lim
        self.rowmod = r
        self._undo_layout(r)

    # ------------------------------------------------------------------
    # Pseudo-rows (gitk: insertfakerow, removefakerow)

    def insert_fake_row(self, id: str, p: str) -> bool:
        """Insert a pseudo-commit `id` (uncommitted changes) as a child of
        `p`, directly above it."""
        self._resumable = False  # the arc structure changes
        if p not in self.arc_of or id in self.arc_of:
            return False
        a = self.arc_of[p]
        i = self.arc_commits[a].index(p)
        self.children[id] = []
        self.parents[id] = [p]
        self.arc_of[id] = a
        self.children.setdefault(p, []).append(id)
        self.fake.add(id)
        self.num_rows += 1
        # note we deliberately don't update arc_start even if i == 0
        self.arc_commits[a].insert(i, id)
        self._modify_arc(a, i)
        self.cached_row.clear()
        self.generation += 1
        return True

    def remove_fake_row(self, id: str) -> bool:
        self._resumable = False  # the arc structure changes
        if id not in self.fake or id not in self.arc_of:
            return False
        p = self.parents[id][0]
        a = self.arc_of.pop(id)
        i = self.arc_commits[a].index(id)
        del self.arc_commits[a][i]
        del self.parents[id]
        del self.children[id]
        self.fake.discard(id)
        self.num_rows -= 1
        if id in self.children.get(p, ()):
            self.children[p].remove(id)
        self._modify_arc(a, i)
        self.cached_row.clear()
        self.ordertok.pop(id, None)
        self.generation += 1
        return True

    # ------------------------------------------------------------------
    # Rows (gitk: update_arcrows, rowofcommit, make_disporder, commitonrow)

    def _update_arcrows(self, until_arc: int = 0, until_row: int = -1):
        """Assign rows to arcs from the last valid one on. Stops early once
        `until_arc` got its row, or rows past `until_row` are covered."""
        if self.rowmod == self.num_rows:
            return
        if not self._resumable:
            # Rows from rowmod on were modified. (When merely resuming an
            # update that stopped early, the rows already handed out stay
            # valid - and a layout pass may be reading them right now.)
            if len(self.displayorder) > self.rowmod:
                del self.displayorder[self.rowmod :]
                del self.parentlist[self.rowmod :]
            self.cached_row.clear()
        up, down, left = self.up, self.down, self.left
        arc_ix, arc_row, arc_commits = self.arc_ix, self.arc_row, self.arc_commits
        a = self.arcmod
        while a != 0 and arc_ix[a] is None:
            # go up the tree until we find something that has a row number,
            # or we get to a seed
            a = up[a]
        if a == 0:
            a = down[0]
            if a == 0:
                return
            self.vrownum = [0]
            self.varcorder = [a]
            arc_ix[a] = 0
            arc_row[a] = 0
            arcn = 0
            row = 0
        else:
            arcn = arc_ix[a]
            if len(self.vrownum) > arcn + 1:
                del self.vrownum[arcn + 1 :]
                del self.varcorder[arcn + 1 :]
            row = arc_row[a]
        vrownum, varcorder = self.vrownum, self.varcorder
        while True:
            if a == until_arc or (until_row >= 0 and row > until_row):
                # Stop here: arc a has its row; rows before `row` are valid.
                self.tokmod = self.tok[a]
                self.arcmod = a
                self.rowmod = row
                self._resumable = True
                return
            p = a
            row += len(arc_commits[a])
            b = down[a]
            if b == 0:
                # if not, go left, or go up until we can go left
                while a != 0:
                    b = left[a]
                    if b != 0:
                        break
                    a = up[a]
                if a == 0:
                    break
            a = b
            arcn += 1
            vrownum.append(row)
            varcorder.append(a)
            arc_ix[a] = arcn
            arc_row[a] = row
        self.tokmod = self.tok[p]
        self.arcmod = p
        self.rowmod = row
        self._resumable = False

    def row_of(self, id: str) -> typing.Optional[int]:
        """gitk: rowofcommit - the row of `id`, or None if not loaded."""
        a = self.arc_of.get(id)
        if a is None:
            return None
        if self.tok[a] >= self.tokmod:
            self._update_arcrows(until_arc=a)
        r = self.cached_row.get(id)
        if r is not None:
            return r
        r = self.arc_commits[a].index(id) + self.arc_row[a]
        self.cached_row[id] = r
        return r

    def comes_before(self, a: str, b: str) -> bool:
        if a == b or a not in self.arc_of or b not in self.arc_of:
            return False
        if self.arc_of[a] != self.arc_of[b]:
            return self.tok[self.arc_of[a]] < self.tok[self.arc_of[b]]
        return self.row_of(a) < self.row_of(b)

    def _make_disporder(self, start: int, end: int):
        """Make sure rows start..end-1 are valid in displayorder/parentlist."""
        if end > self.rowmod:
            self._update_arcrows(until_row=end)
        vrownum = self.vrownum
        if not vrownum:
            return
        ai = max(0, bisect.bisect_right(vrownum, max(start, 0)) - 1)
        r = vrownum[ai]
        narc = len(vrownum)
        disp, plist = self.displayorder, self.parentlist
        while ai < narc and r < end:
            a = self.varcorder[ai]
            ac = self.arc_commits[a]
            al = len(ac)
            n = len(disp)
            if n < r + al:
                if n < r:
                    pad = [None] * (r - n)
                    disp.extend(pad)
                    plist.extend(pad)
                elif n > r:
                    del disp[r:]
                    del plist[r:]
                disp.extend(ac)
                plist.extend(self.parents[id] for id in ac)
            elif disp[r + al - 1] is None:
                disp[r : r + al] = ac
                plist[r : r + al] = [self.parents[id] for id in ac]
            r += al
            ai += 1

    def commit_on_row(self, row: int) -> typing.Optional[str]:
        if row < 0 or row >= self.num_rows:
            return None
        if row < len(self.displayorder):
            id = self.displayorder[row]
            if id is not None and row < self.rowmod:
                return id
        self._make_disporder(row, row + 1)
        return self.displayorder[row] if row < len(self.displayorder) else None

    # ------------------------------------------------------------------
    # Lane layout (gitk: ordertoken, idcol, make_idlist, layoutrows, ...)

    def _ordertoken(self, id: str) -> str:
        ordertok = self.ordertok
        t = ordertok.get(id)
        if t is not None:
            return t
        origid = id
        todo = []
        while True:
            a = self.arc_of.get(id)
            if a is not None:
                p = self.arc_start[a]
            else:
                p = self.children[id][0]
            t = ordertok.get(p)
            if t is not None:
                tok = t
                break
            id = self._first_real_child(p)
            if id is None:
                # it's a root
                tok = self.tok[self.arc_of[p]]
                break
            ps = self.parents[id]
            if len(ps) == 1:
                todo.append((p, ""))
            else:
                todo.append((p, strrep(ps.index(p))))
        for p, suffix in reversed(todo):
            tok += suffix
            ordertok[p] = tok
        ordertok[origid] = tok
        return tok

    def _idcol(self, idlist: typing.List[str], id: str, i: int = 0) -> int:
        """Where `id` goes in idlist so order tokens increase left to right."""
        ot = self._ordertoken
        t = ot(id)
        if i < 0:
            i = 0
        n = len(idlist)
        if i >= n or t < ot(idlist[i]):
            if i > n:
                i = n
            i -= 1
            while i >= 0 and t < ot(idlist[i]):
                i -= 1
            i += 1
        elif t > ot(idlist[i]):
            i += 1
            while i < n and t >= ot(idlist[i]):
                i += 1
        return i

    def _nextuse(self, id: str, row: int) -> int:
        for kid in self.children.get(id, ()):
            kr = self.row_of(kid)
            if kr is None:
                return -1
            if kr > row:
                return kr
        r = self.row_of(id)
        return -1 if r is None else r

    def _prevuse(self, id: str, row: int) -> int:
        ret = -1
        for kid in self.children.get(id, ()):
            kr = self.row_of(kid)
            if kr is None:
                break
            if kr < row:
                ret = kr
        return ret

    def _first_kid_row(self, id: str) -> typing.Optional[int]:
        kids = self.children.get(id)
        if not kids:
            return None
        return self.row_of(kids[0])

    def _make_idlist(self, row: int) -> typing.List[str]:
        """Rebuild the lanes of `row` from scratch, from the rows near it."""
        r = max(0, row - MINGAPLEN - DOWNARROWLEN - 1)
        ra = max(0, row - DOWNARROWLEN)
        rb = min(row + UPARROWLEN, self.num_rows)
        self._make_disporder(r, rb + 1)
        disp, plist = self.displayorder, self.parentlist
        ot = self._ordertoken
        ids = set()
        while r < ra:
            nextid = disp[r + 1] if r + 1 < len(disp) else None
            for p in plist[r] or ():
                if p == nextid:
                    continue
                rn = self._nextuse(p, r)
                if row <= rn <= r + DOWNARROWLEN + MINGAPLEN + UPARROWLEN:
                    ids.add((ot(p), p))
            r += 1
        while r < row:
            nextid = disp[r + 1] if r + 1 < len(disp) else None
            for p in plist[r] or ():
                if p == nextid:
                    continue
                rn = self._nextuse(p, r)
                if rn < 0 or rn >= row:
                    ids.add((ot(p), p))
            r += 1
        id = disp[row]
        ids.add((ot(id), id))
        while r < rb:
            for p in plist[r] or ():
                fk = self._first_kid_row(p)
                if fk is not None and fk < row:
                    ids.add((ot(p), p))
            r += 1
            id = disp[r] if r < len(disp) else None
            if id is not None:
                fk = self._first_kid_row(id)
                if fk is not None and fk < row:
                    ids.add((ot(id), id))
        return [id for _, id in sorted(ids)]

    def _changed_row(self, row: int):
        n = len(self.rowisopt)
        for r in (row, row + 1, row + 2):
            if r < n:
                self.rowisopt[r] = False
        self.generation += 1

    def _makeupline(self, id: str, row: int, rend: int, col: int):
        r = rend
        while True:
            rstart = self._prevuse(id, r)
            if rstart < 0:
                return
            if rstart < row:
                break
            r = rstart
        if rstart + UPARROWLEN + MINGAPLEN + DOWNARROWLEN < rend:
            rstart = rend - UPARROWLEN - 1
        for r in range(rstart + 1, row + 1):
            idlist = self._rowids(r)
            if idlist and id not in idlist:
                col = self._idcol(idlist, id, col)
                idlist.insert(col, id)
                self._changed_row(r)

    def _rowids(self, r: int) -> typing.Optional[typing.List[str]]:
        return self.rowidlist[r] if 0 <= r < len(self.rowidlist) else None

    def _layout_rows(self, row: int, endrow: int) -> int:
        """gitk: layoutrows - compute rowidlist for rows row..endrow-1."""
        self._make_disporder(row - 1, endrow + UPARROWLEN)
        disp, plist = self.displayorder, self.parentlist
        children = self.children
        num = self.num_rows
        idlist: typing.List[str] = []
        final = True
        if row > 0:
            prev = self._rowids(row - 1)
            if prev:
                idlist = [id for id in prev if id]
                final = self.rowfinal[row - 1]
        while row < endrow:
            rm1 = row - 1
            if rm1 < 0 or not idlist:
                idlist = self._make_idlist(row)
                final = True
            else:
                id = disp[rm1]
                col = idlist.index(id) if id in idlist else -1
                if col >= 0:
                    del idlist[col]
                for p in plist[rm1] or ():
                    if p not in idlist:
                        col = self._idcol(idlist, p, col)
                        idlist.insert(col, p)
                        # if not the first child, we have to insert a line going up
                        if id != children[p][0]:
                            self._makeupline(p, rm1, row, col)
                id = disp[row]
                if row > DOWNARROWLEN:
                    termrow = row - DOWNARROWLEN - 1
                    for p in plist[termrow] or ():
                        if p not in idlist:
                            continue
                        nr = self._nextuse(p, termrow)
                        if nr < 0 or nr >= row + MINGAPLEN + UPARROWLEN:
                            idlist.remove(p)
                if id in idlist:
                    col = idlist.index(id)
                else:
                    col = self._idcol(idlist, id)
                    idlist.insert(col, id)
                    if children.get(id):
                        self._makeupline(id, rm1, row, col)
                r = row + UPARROWLEN - 1
                if r < num:
                    x = col
                    for p in plist[r] or ():
                        if p in idlist:
                            continue
                        fk = self._first_kid_row(p)
                        if fk is not None and fk < row:
                            x = self._idcol(idlist, p, x)
                            idlist.insert(x, p)
                    r += 1
                    if r < num:
                        p = disp[r] if r < len(disp) else None
                        if p is not None and p not in idlist:
                            fk = self._first_kid_row(p)
                            if fk is not None and fk < row:
                                x = self._idcol(idlist, p, x)
                                idlist.insert(x, p)
            if (
                final
                and not self.complete
                and row + UPARROWLEN + MINGAPLEN + DOWNARROWLEN >= num
            ):
                final = False
            n = len(self.rowidlist)
            if row == n:
                self.rowidlist.append(list(idlist))
                self.rowisopt.append(False)
                self.rowfinal.append(final)
            elif row < n:
                old = self.rowidlist[row]
                if old is None or [i for i in old if i] != idlist:
                    self.rowidlist[row] = list(idlist)
                    self._changed_row(row)
                self.rowfinal[row] = final
            else:
                pad = row - n
                self.rowidlist.extend([None] * pad)
                self.rowidlist.append(list(idlist))
                self.rowfinal.extend([False] * pad)
                self.rowfinal.append(final)
                self.rowisopt.extend([False] * (pad + 1))
            row += 1
        return row

    def _insert_pad(self, row: int, col: int, npad: int):
        idlist = self.rowidlist[row]
        bef = idlist[:col]
        aft = idlist[col:]
        if "" in aft:
            i = aft.index("")
            if i > 0:
                del aft[i]
        self.rowidlist[row] = bef + [""] * npad + aft
        self._changed_row(row)

    def _optimize_rows(self, row: int, col: int, endrow: int):
        """gitk: optimize_rows - insert padding so lines go straight down or
        at most one column sideways per row."""
        disp = self.displayorder
        children = self.children
        rowidlist = self.rowidlist
        if row < 1:
            row = 1
        while row < endrow:
            if row < len(self.rowisopt) and self.rowisopt[row]:
                row += 1
                col = 0
                continue
            haspad = False
            y0 = row - 1
            ym = row - 2
            idlist = self._rowids(row)
            previdlist = self._rowids(y0)
            if not idlist or not previdlist:
                row += 1
                col = 0
                continue
            if ym >= 0:
                pprevidlist = self._rowids(ym)
                if not pprevidlist:
                    row += 1
                    col = 0
                    continue
            else:
                pprevidlist = []
            x0 = -1
            while col < len(idlist):
                id = idlist[col]
                if col < len(previdlist) and previdlist[col] == id:
                    col += 1
                    continue
                if not id:
                    haspad = True
                    col += 1
                    continue
                x0 = previdlist.index(id) if id in previdlist else -1
                if x0 < 0:
                    col += 1
                    continue
                z = x0 - col
                isarrow = False
                z0 = None
                if ym >= 0:
                    xm = pprevidlist.index(id) if id in pprevidlist else -1
                    if xm >= 0:
                        z0 = xm - x0
                if z0 is None:
                    # if row y0 is the first child of id then it's not an arrow
                    kids = children.get(id)
                    if not kids or kids[0] != disp[y0]:
                        isarrow = True
                nextids = self._rowids(row + 1) or []
                if not isarrow and id != disp[row] and id not in nextids:
                    isarrow = True
                # Looking at lines from this row to the previous row, make
                # them go straight up if they end in an arrow on the previous
                # row; otherwise make them go straight up or at 45 degrees.
                if z < -1 or (z < 0 and isarrow):
                    # Line currently goes left too much; insert pads in the
                    # previous row, then optimize it
                    npad = -1 - z + int(isarrow)
                    self._insert_pad(y0, x0, npad)
                    if y0 > 0:
                        self._optimize_rows(y0, x0, row)
                    previdlist = rowidlist[y0]
                    x0 = previdlist.index(id) if id in previdlist else -1
                    z = x0 - col
                    if z0 is not None:
                        pprevidlist = rowidlist[ym]
                        xm = pprevidlist.index(id) if id in pprevidlist else -1
                        z0 = xm - x0
                elif z > 1 or (z > 0 and isarrow):
                    # Line currently goes right too much; insert pads in
                    # this line
                    npad = z - 1 + int(isarrow)
                    self._insert_pad(row, col, npad)
                    idlist = rowidlist[row]
                    col += npad
                    z = x0 - col
                    haspad = True
                if z0 is None and not isarrow and ym >= 0:
                    # this line links to its first child on row row-2
                    kid = disp[ym]
                    if kid in pprevidlist:
                        z0 = pprevidlist.index(kid) - x0
                # avoid lines jigging left then immediately right
                if z0 is not None and z < 0 and z0 > 0:
                    self._insert_pad(y0, x0, 1)
                    x0 += 1
                    self._optimize_rows(y0, x0, row)
                    previdlist = rowidlist[y0]
                # Not in gitk, which draws diagonals: render_row draws a line
                # moving one column as a jog with a corner in both columns of
                # the lower row, so the column it leaves must be free there.
                # Neighbours moving together therefore take turns (a
                # staircase): one moving left holds the next one back a row,
                # one moving right is pushed to move a row earlier.
                z = x0 - col
                if z == 1 and col + 1 < len(idlist) and idlist[col + 1]:
                    self._insert_pad(row, col + 1, 1)
                    idlist = rowidlist[row]
                    haspad = True
                elif z == -1 and col >= 1 and idlist[col - 1]:
                    self._insert_pad(y0, x0, 1)
                    if y0 > 0:
                        self._optimize_rows(y0, x0, row)
                    previdlist = rowidlist[y0]
                col += 1
            if not haspad:
                # Find the first column that doesn't have a line going right
                col = len(idlist) - 1
                while col >= 0:
                    id = idlist[col]
                    if not id:
                        break
                    x0 = previdlist.index(id) if id in previdlist else -1
                    if x0 < 0:
                        # check if this is the link to the first child
                        kid = disp[y0]
                        kids = children.get(id)
                        if kids and kids[0] == kid:
                            # it is, work out offset to child
                            x0 = previdlist.index(kid) if kid in previdlist else -1
                    if x0 <= col:
                        break
                    col -= 1
                # Insert a pad at that column as long as it has a line and
                # isn't the last column
                col += 1
                if x0 >= 0 and col < len(idlist):
                    idlist.insert(col, "")
                    self._changed_row(row)
            row += 1
            col = 0

    def _undo_layout(self, row: int):
        r = max(0, row - (UPARROWLEN + MINGAPLEN + DOWNARROWLEN))
        if len(self.rowidlist) > r:
            r -= 1
            del self.rowidlist[r + 1 :]
            del self.rowfinal[r + 1 :]
            del self.rowisopt[r + 1 :]
        self.generation += 1

    def reset_layout(self):
        """Forget every row's lanes; they are laid out again on demand. The
        padding optimize_rows puts in depends on which rows were laid out
        when - while commits streamed in, that is timing - so starting over
        once the log is complete makes the picture depend only on the
        commits (and where the view is)."""
        self.rowidlist = []
        self.rowisopt = []
        self.rowfinal = []
        self.generation += 1

    def layout(self, row: int, endrow: int):
        """gitk: the layout half of drawcommits - make rows row..endrow
        (inclusive) ready to render, with a margin either side."""
        num = self.num_rows
        if num == 0:
            return
        row = max(0, row)
        endrow = min(endrow, num - 1)
        rl1 = max(0, row - DOWNARROWLEN - 3)
        ro1 = max(0, row - 3)
        r2 = min(num, endrow + UPARROWLEN + 3)
        self._make_disporder(max(0, rl1 - 1), r2 + UPARROWLEN + 1)
        for r in range(rl1, r2):
            if self._rowids(r) and self.rowfinal[r]:
                if rl1 < r:
                    self._layout_rows(rl1, r)
                rl1 = r + 1
        if rl1 < r2:
            self._layout_rows(rl1, r2)
        self._optimize_rows(ro1, 0, r2)

    # ------------------------------------------------------------------
    # Text rendering

    def cut_line_target(self, row: int, id: str) -> typing.Optional[int]:
        """The row of the other dashed end (┆) of the cut line `id` (a line is
        named by the commit it leads to) whose ┆ on `row` was clicked: from
        where it was cut short, the row where it resumes further down; from
        where it resumes, the row where it was cut short further up."""
        if id in (self._rowids(row + 1) or []):
            # the line resumes here: walk down from the child it came from
            r = self._prevuse(id, row)
            if r < 0:
                return None
            self.layout(r, r + DOWNARROWLEN + 2)
            while r + 1 < row and id in (self._rowids(r + 1) or []):
                r += 1
            return r
        # the line was cut here: walk up from where it is used next
        r = self._nextuse(id, row)
        if r <= row:
            return None
        self.layout(r - UPARROWLEN - 2, r)
        while r - 1 > row and id in (self._rowids(r - 1) or []):
            r -= 1
        return r

    def lane_color(self, id: str, ncolors: int) -> int:
        """A stable colour index for the line leading to `id`. Like gitk, a
        commit keeps the colour of its child when it is that child's only
        parent and only commit - i.e. the whole arc shares one colour - but
        picked from the arc's first sha instead of gitk's draw-order cycling,
        so it never changes between redraws."""
        if not id:
            return 0
        a = self.arc_of.get(id)
        key = self.arc_start[a] if a is not None else id
        if key in self.fake:
            key = self.parents[key][0] if self.parents.get(key) else key
        try:
            return int(key[:6], 16) % ncolors
        except ValueError:
            return sum(map(ord, key)) % ncolors

    def _upper_edges(self, row: int) -> typing.Set[typing.Tuple[int, int, str]]:
        """The edges from the commit on `row` to parents in other columns on
        `row + 1` that are drawn on `row` itself (`┽─┐`), as (from column, to
        column, parent): where possible, so a merge shows on its own row; the
        rest start on the lower row (`├─┐`)."""
        if row + 1 >= self.num_rows:
            return set()
        cur = self._rowids(row) or []
        nxt = self._rowids(row + 1) or []
        node = self.commit_on_row(row)
        if node not in cur:
            return set()
        s = cur.index(node)
        parents = self.parents.get(node, ())
        occupant = nxt[s] if s < len(nxt) else ""
        below = self.commit_on_row(row + 1)
        # (a first parent right below is a node there, which hides a corner)
        blocked = bool(occupant) and (occupant not in parents or occupant == below)
        nc = nxt.index(below) if below in nxt else -1
        edges = set()
        for c, id in enumerate(nxt):
            if not id or c == s or id not in parents:
                continue
            # On the node's own row (●─┐) unless the corner there would land
            # on another line; then on the lower row, unless that one is
            # blocked as well or its run would cross the node there.
            corner = cur[c] if c < len(cur) else ""
            if len(parents) == 1 and id == below and not blocked:
                continue  # a branch joining its parent right below: ┾─┘
            if not corner or corner == id:
                edges.add((s, c, id))
            elif blocked or min(s, c) < nc < max(s, c):
                edges.add((s, c, id))
        return edges

    def render_row(self, row: int) -> typing.Tuple[str, typing.List[str]]:
        """The graph art of `row` and, per character, the id of the line it
        belongs to ("" for blanks) - for colouring. Lays out on demand.

        A line moving between columns is drawn on the lower of the two rows
        as an orthogonal jog (`┌─┘`, `●─┐`), in box-drawing characters, which
        have a glyph for every kind of join; see also _upper_edges."""
        cur = self._rowids(row)
        if not cur or (row + 1 < self.num_rows and not self._rowids(row + 1)):
            self.layout(row, row)
            cur = self._rowids(row)
            if not cur:
                return "", []
        prev = (self._rowids(row - 1) or []) if row > 0 else []
        nxt = (self._rowids(row + 1) or []) if row + 1 < self.num_rows else []
        node = self.commit_on_row(row)
        pnode = self.commit_on_row(row - 1) if row > 0 else None
        pnode_col = prev.index(pnode) if pnode is not None and pnode in prev else -1
        pnode_parents = self.parents.get(pnode, ()) if pnode is not None else ()
        prevpos = {id: i for i, id in enumerate(prev) if id}
        upper_here = self._upper_edges(row)
        node_x = node_arms = -1
        upper_above = self._upper_edges(row - 1) if row > 0 else set()

        width = 2 * max(len(cur), len(prev), len(nxt)) + 1
        conn = [0] * width
        glyph: typing.Dict[int, str] = {}
        owner = [""] * width

        def horiz(xs, xe, id):
            lo, hi = min(xs, xe), max(xs, xe)
            conn[lo] |= _R
            conn[hi] |= _L
            for x in range(lo + 1, hi):
                conn[x] |= _L | _R
                if not owner[x]:
                    owner[x] = id
            # the horizontal run belongs to the moving line
            owner[xs] = owner[xs] or id

        # Every line entering this row: (source column in the row above,
        # column here, id).
        segs = []
        has_source = set()
        for c, id in enumerate(cur):
            if not id:
                continue
            owner[2 * c] = id
            s = prevpos.get(id)
            if s is not None:
                segs.append((s, c, id))
            if pnode_col >= 0 and id in pnode_parents and pnode_col != s:
                if (pnode_col, c, id) in upper_above:
                    # drawn on the row above, which ends in a corner here
                    conn[2 * c] |= _U
                    has_source.add(c)
                else:
                    segs.append((pnode_col, c, id))
        for s, c, id in segs:
            has_source.add(c)
            conn[2 * s] |= _U
            if s != c:
                horiz(2 * s, 2 * c, id)
        for s, c, id in upper_here:
            horiz(2 * s, 2 * c, id)
            conn[2 * c] |= _D
            owner[2 * c] = owner[2 * c] or id
        for c, id in enumerate(cur):
            if not id:
                continue
            x = 2 * c
            if id == node:
                arms = conn[x]
                for p in self.parents.get(node, ()):
                    if p in nxt:
                        pc = nxt.index(p)
                        if pc == c or (c, pc, p) not in upper_here:
                            arms |= _D  # the edge leaves downwards
                glyph[x] = node_glyph(arms)
                node_x, node_arms = x, arms
            elif c not in has_source or id not in nxt:
                # a cut line: its end, or the start of its continuation
                glyph[x] = CUT
                if id in nxt:
                    conn[x] |= _D
            else:
                conn[x] |= _D

        out = []
        for x in range(width):
            m = conn[x]
            g = glyph.get(x)
            if g == CUT and m & (_L | _R):
                g = None  # a cut end that also turns: draw the turn
            if g is not None:
                out.append(g)
            elif m == 0:
                out.append(" ")
                owner[x] = ""
            else:
                out.append(_BOX[m])
        # One leading blank column, so every lane has a gap on both sides:
        # the commit's dot goes in the gap beside its cell (⬤┤, ─⬤ ...), with
        # the cell's arm to it in a light line. With no free gap, the cell
        # keeps the heavy stub (node_glyph) to mark the commit instead.
        out.insert(0, " ")
        owner.insert(0, "")
        if node_x >= 0:
            side = _marker(node_arms)
            dx = node_x if side == _L else node_x + 2
            if side != _L | _R:
                if dx >= len(out):
                    out.extend(" " * (dx - len(out) + 1))
                    owner.extend([""] * (dx - len(owner) + 1))
                if out[dx] == " ":
                    out[dx] = self.dot
                    owner[dx] = node
                    out[node_x + 1] = _BOX[node_arms | side]
        text = "".join(out).rstrip()
        return text, owner[: len(text)]
