"""Unit tests for gitk.graph (the port of gitk's arc model and lane layout).

Random histories with branches, merges and clock skew are fed in the order a
plain `git log` would stream them (newest committer date first), interleaved
with lazy layout of random windows - as the UI does while loading - and the
result is checked against invariants rather than snapshots:

- every commit is shown below all of its children (a topological order),
  and row_of / commit_on_row agree;
- each row's lanes hold its commit, and the next row's lanes hold all of
  that commit's parents (so every edge can be drawn);
- the order does not depend on how the stream was interleaved with layout,
  nor on commits arriving later on top of loaded ones (an F5 refresh);
- pseudo-rows (uncommitted changes) sit directly above their parent.

Run with `pytest test/test_graph.py` or `python3 test/test_graph.py`.
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gitk.graph import CUT, CommitGraph  # noqa: E402


def random_history(rnd, n, nbranches):
    """[(id, ctime, parents)] in creation order, with skewed clocks."""
    commits = []
    tips = []
    t = 1_600_000_000
    for i in range(n):
        id = "%040x" % rnd.getrandbits(160)
        parents = []
        if commits:
            if tips and rnd.random() < 0.8:
                parents.append(rnd.choice(tips))
            else:
                parents.append(rnd.choice(commits)[0])
            if rnd.random() < 0.15:
                other = rnd.choice(commits[-200:])[0]
                if other not in parents:
                    parents.append(other)
        t += rnd.randint(1, 3600)
        skew = rnd.randint(-7200, 0) if rnd.random() < 0.05 else 0
        commits.append((id, t + skew, parents))
        tips = [c for c in tips if c not in parents] + [id]
        tips = tips[-nbranches:]
    return commits


def stream_order(commits):
    """Newest committer date first, as `git log` without --topo-order."""
    return sorted(commits, key=lambda c: -c[1])


def build(commits, rnd=None, window=40):
    g = CommitGraph()
    for i, (id, ct, ps) in enumerate(commits):
        g.add_commit(id, ps, ct)
        if rnd is not None and rnd.random() < 0.05:
            top = rnd.randrange(g.num_rows)
            g.layout(top, top + window)
            for r in range(top, min(top + window, g.num_rows)):
                g.render_row(r)
    g.complete = True
    return g


def order(g):
    return [g.commit_on_row(r) for r in range(g.num_rows)]


def check_invariants(g):
    rows = order(g)
    assert len(set(rows)) == g.num_rows and None not in rows
    pos = {id: r for r, id in enumerate(rows)}
    for id, r in pos.items():
        assert g.row_of(id) == r
        for p in g.parents[id]:
            if p in pos:
                assert pos[p] > r, "parent shown above its child"
    for top in range(0, g.num_rows, 50):
        g.layout(top, top + 49)
    for r in range(g.num_rows):
        lanes = g.rowidlist[r]
        assert rows[r] in lanes
        if r + 1 < g.num_rows:
            for p in g.parents[rows[r]]:
                assert p in g.rowidlist[r + 1], "edge to parent cannot be drawn"
        art, owners = g.render_row(r)
        assert len(art) == len(owners)


def test_topological_and_drawable():
    for seed in range(8):
        rnd = random.Random(seed)
        commits = random_history(rnd, 600, nbranches=1 + seed % 6)
        check_invariants(build(stream_order(commits), rnd))


def test_order_independent_of_layout_interleaving():
    rnd = random.Random(42)
    commits = stream_order(random_history(rnd, 800, nbranches=5))
    assert order(build(commits)) == order(build(commits, random.Random(7)))


def test_incremental_refresh_matches_full_load():
    rnd = random.Random(3)
    commits = random_history(rnd, 500, nbranches=3)
    old, new = commits[:400], commits[400:]
    # Load the old history, draw some of it, then file the new commits on
    # top (as `git log old..HEAD` would stream them).
    g = build(stream_order(old), rnd)
    for id, ct, ps in stream_order(new):
        g.add_commit(id, ps, ct)
    check_invariants(g)
    assert order(g) == order(build(stream_order(commits)))


def test_fake_rows_sit_above_parent():
    rnd = random.Random(5)
    commits = random_history(rnd, 300, nbranches=4)
    g = build(stream_order(commits), rnd)
    head = commits[150][0]
    assert g.insert_fake_row("local-staged", head)
    assert g.insert_fake_row("local-working", "local-staged")
    r = g.row_of(head)
    assert g.commit_on_row(r - 1) == "local-staged"
    assert g.commit_on_row(r - 2) == "local-working"
    check_invariants(g)
    assert g.remove_fake_row("local-working")
    assert g.remove_fake_row("local-staged")
    assert order(g) == order(build(stream_order(commits)))


def test_long_lines_are_cut():
    # A side branch forking off early and merged back much later must not
    # keep a lane open across all the rows in between.
    commits = []
    t = 1_600_000_000
    base = ("%040x" % 1, t, [])
    commits.append(base)
    side = ("%040x" % 2, t + 1, [base[0]])
    commits.append(side)
    prev = base[0]
    for i in range(300):
        c = ("%040x" % (10 + i), t + 10 + i, [prev])
        commits.append(c)
        prev = c[0]
    commits.append(("%040x" % 9999, t + 1000, [prev, side[0]]))
    g = build(stream_order(commits))
    check_invariants(g)
    middle = g.num_rows // 2
    assert len([i for i in g.rowidlist[middle] if i]) == 1
    # Each dashed end of the long line (into base: the side commit is shown
    # right below the merge) leads to the row of its other dashed end.
    ends = [
        (r, owners[x])
        for r in range(g.num_rows)
        for text, owners in [g.render_row(r)]
        for x, ch in enumerate(text)
        if ch == CUT
    ]
    assert len(ends) == 2
    (cut_row, line), (resume_row, line2) = ends
    assert line == line2 == base[0]
    assert g.cut_line_target(cut_row, line) == resume_row
    assert g.cut_line_target(resume_row, line) == cut_row


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
