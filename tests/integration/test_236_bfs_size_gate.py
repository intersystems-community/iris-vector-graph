"""Spec 236, Story 3: BFS on an adjacency at, under and over the callout's argument limit.

34,791 characters pass as one $ZF argument; 34,792 raise <OUT OF $ZF HEAP SPACE>; 65,536
and up raise <MAX $ZF STRING>. The gate answers from ObjectScript before the call, so
`last_bfs_path` (process-private, set by `NKGAccelTraversal.BFSJson`) says which side of
the limit a graph fell on, and the hits must match the pure-ObjectScript BFS either way.
Runs in the scratch namespace, with the sentinel asserted before every erase or write.
"""

from __future__ import annotations

import json

import pytest

PRED = "IVG236BFS"
#: ZFMAXCHARS: the four string arguments share one heap. Here they are "ArnoKG" (6), the
#: seed "s0000" (5) and the predicate list "[]" (2), so the adjacency may be 34,808 - 13 long.
#: An empty predicate list, because the ObjectScript BFS stops after hop 1 when given one
#: (docs/KNOWN_ISSUES.md), which would make the comparison below a comparison of two bugs.
PREDS = "[]"
BUDGET = 34808
OTHER_ARGS = len("ArnoKG") + len("s0000") + len(PREDS)
LIMIT = BUDGET - OTHER_ARGS
US = "\x1f"


def _build(scratch, target: int):
    """A graph whose exported adjacency is exactly `target` characters; returns (native, seed)."""
    native = scratch.native()
    scratch.require_scratch(native)
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    n = (target - 400) // 22
    base = 0
    for i in range(n):
        s, d = f"s{i:04d}", f"s{i + 1:04d}"
        native.set(1, "^KG", "out", 0, s, PRED, d)
        native.set(1, "^KG", "in", 0, d, PRED, s)
        base += len(s) + len(PRED) + len(d) + 3
    # Subscripts are capped near 511 characters, so the remainder is spread over filler edges.
    remaining, k = target - base, 0
    while remaining > 0:
        overhead = 4 + len(PRED) + 3
        width = 200 if remaining - (overhead + 200) >= overhead + 1 or remaining == overhead + 200 else remaining - overhead
        assert 1 <= width <= 500, (remaining, width)
        src, pad = f"z{k:03d}", f"{k:03d}" + "p" * (width - 3) if width > 3 else "p" * width
        native.set(1, "^KG", "out", 0, src, PRED, pad)
        native.set(1, "^KG", "in", 0, pad, PRED, src)
        remaining -= overhead + width
        k += 1
    native.classMethodValue("Graph.KG.Traversal", "BuildNKG")
    native = scratch.reconnect_after_sync(native)  # BuildNKG can leave the handle in USER
    lib = native.get("ArnoAccel", "lib_path")
    assert lib, "the scratch namespace has no library recorded"
    native.classMethodValue("Graph.KG.ArnoAccel", "Load", lib)
    scratch.require_scratch(native)
    return native, "s0000"


def _hits(native, raw: str) -> set:
    tag = raw.split(":")[1]
    if tag == "0":
        return set()
    return {r["o"] for r in json.loads(str(native.classMethodValue("Graph.KG.Traversal", "ReadBFSResults", tag)))}


@pytest.fixture(autouse=True)
def _clean_up(ivg_236_scratch):
    yield
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")


@pytest.mark.parametrize(
    "target,expected_path",
    [
        (LIMIT - 1, "native"),
        (LIMIT, "native"),
        (LIMIT + 1, "size-gate"),
        (LIMIT + 2, "size-gate"),
        (65535, "size-gate"),
        (65536, "size-gate"),
        (70000, "size-gate"),
    ],
)
def test_bfs_is_correct_and_takes_the_right_path_at_each_size(ivg_236_scratch, target, expected_path):
    native, seed = _build(ivg_236_scratch, target)
    exported = str(native.classMethodValue("Graph.KG.NKGAccelAdjacency", "ExportAdjacencyWithPreds"))
    assert len(exported) == target

    arno = str(native.classMethodValue("Graph.KG.NKGAccel", "BFSJson", seed, PREDS, 2, 0))
    plain = str(
        native.classMethodValue(
            "Graph.KG.Traversal", "BFSFastJsonSorted", seed, PREDS, 2, "", "out", 0
        )
    )
    path = str(native.classMethodValue("Graph.KG.NKGAccelTraversal", "LastBfsPath"))
    error = str(native.classMethodValue("Graph.KG.NKGAccelTraversal", "LastBfsError"))
    assert path == expected_path, f"took {path}: {error}"
    owner_hits = _hits(native, plain)
    assert "s0001" in owner_hits, "the ObjectScript BFS found nothing: the fixture is wrong"
    assert _hits(native, arno) == owner_hits


def test_a_graph_far_past_the_budget_is_turned_away_without_building_its_whole_adjacency(
    ivg_236_scratch,
):
    """Measured on 22,000 edges: the uncapped export was 34 ms every call. With the cap it
    stops once the budget is passed, so its cost no longer grows with the graph."""
    import time

    native, seed = _build(ivg_236_scratch, 200_000)
    full = str(native.classMethodValue("Graph.KG.NKGAccelAdjacency", "ExportAdjacencyWithPreds"))
    assert len(full) == 200_000
    capped = str(native.classMethodValue("Graph.KG.NKGAccelAdjacency", "ExportAdjacencyWithPreds", "", 34_000))
    assert capped == "OVERSIZE"

    def median(fn, k=15):
        xs = []
        for _ in range(k):
            t = time.perf_counter()
            fn()
            xs.append(time.perf_counter() - t)
        return sorted(xs)[k // 2]

    t_full = median(lambda: native.classMethodValue("Graph.KG.NKGAccelAdjacency", "ExportAdjacencyWithPreds"))
    t_capped = median(
        lambda: native.classMethodValue("Graph.KG.NKGAccelAdjacency", "ExportAdjacencyWithPreds", "", 34_000)
    )
    assert t_capped * 3 < t_full, (t_capped, t_full)
    arno = str(native.classMethodValue("Graph.KG.NKGAccel", "BFSJson", seed, PREDS, 2, 0))
    assert str(native.classMethodValue("Graph.KG.NKGAccelTraversal", "LastBfsPath")) == "size-gate"
    assert _hits(native, arno)
