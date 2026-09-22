"""The Rust callout names the node `node`; the reader only ever read `id`.

`Graph.KG.PageRank.RunJson` and `PageRankGlobalJson` answer with
`[{"id": ..., "score": ...}]`. The Rust callout behind
`Graph.KG.ArnoAccel.PPRJson` answers the same question with a different key:

    [{"node":"fix_0","score":0.15},{"node":"fix_1","score":0.1275}, ...]

`execute_ppr` and `execute_pagerank` read `r.get("id", "")`, so every row on the
Arno path came back with a **blank** id and a correct score. Measured on
ivg-iris-enterprise:

    store.execute_ppr(["fix_0"], 0.85, 20).rows
    -> [['', 0.15000000000000002], ['', 0.1275], ['', 0.108375], ...]

Nothing raised and `IVGResult.error` was None, so the scores looked fine and the
identities were gone. `kg_PERSONALIZED_PAGERANK` builds `{r[0]: r[1]}` from those
rows and collapses them to one entry keyed `''`, which is what
`test_ppr_seed_has_highest_score` saw as `assert 'alg_0' in {'': 0.0549}`, and
`kg_PPR_GUIDED_SUBGRAPH` passes the same `''` on to
`Graph.KG.Subgraph.SubgraphJson`, which throws on a blank seed — nine integration
failures from one key name.

This was unreachable until the `_detect_arno` smoke probe was fixed (see
tests/unit/test_arno_probe_seed.py): while Arno was disabled for every install,
PPR always took the ObjectScript branch, which does say `id`.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

ARNO_ROWS = [
    {"node": "fix_0", "score": 0.15},
    {"node": "fix_1", "score": 0.1275},
]

OBJECTSCRIPT_ROWS = [
    {"id": "fix_0", "score": 0.15},
    {"id": "fix_1", "score": 0.1275},
]

EXPECTED = [["fix_0", 0.15], ["fix_1", 0.1275]]


def _store(*, arno: bool) -> IRISGraphStore:
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._arno_available = arno
    store._arno_capabilities = {"algorithms": ["ppr", "pagerank"]} if arno else {}
    store._nkg_dirty = False
    return store


def test_ppr_keeps_the_node_ids_the_rust_callout_returns():
    store = _store(arno=True)
    with patch.object(store, "_detect_arno", return_value=True), patch.object(
        store, "_arno_call", return_value=json.dumps(ARNO_ROWS)
    ):
        result = store.execute_ppr(["fix_0"], 0.85, 20)

    assert result.error is None
    assert result.rows == EXPECTED, (
        "the Rust callout's `node` key was dropped, so every row carries a blank "
        f"id with a correct score: {result.rows}"
    )


def test_ppr_still_reads_the_objectscript_key():
    store = _store(arno=False)
    with patch.object(store, "_detect_arno", return_value=False), patch.object(
        store, "_call_classmethod", return_value=json.dumps(OBJECTSCRIPT_ROWS)
    ):
        result = store.execute_ppr(["fix_0"], 0.85, 20)

    assert result.rows == EXPECTED


def test_pagerank_keeps_the_node_ids_the_rust_callout_returns():
    """The same reader, the same callout, the same key."""
    store = _store(arno=True)
    with patch.object(store, "_detect_arno", return_value=True), patch.object(
        store, "_arno_call", return_value=json.dumps(ARNO_ROWS)
    ):
        result = store.execute_pagerank(0.85, 20)

    assert result.rows == EXPECTED


def test_pagerank_still_reads_the_objectscript_key():
    store = _store(arno=False)
    with patch.object(store, "_detect_arno", return_value=False), patch.object(
        store, "_call_classmethod", return_value=json.dumps(OBJECTSCRIPT_ROWS)
    ):
        result = store.execute_pagerank(0.85, 20)

    assert result.rows == EXPECTED


def test_a_row_naming_neither_key_is_not_reported_as_a_scored_node():
    """A blank id with a real score is the failure mode; do not manufacture one.

    Dropping the row is the honest answer: a score with no node to attach it to
    cannot be returned to a caller that keys results by node.
    """
    store = _store(arno=True)
    with patch.object(store, "_detect_arno", return_value=True), patch.object(
        store, "_arno_call", return_value=json.dumps([{"score": 0.5}])
    ):
        result = store.execute_ppr(["fix_0"], 0.85, 20)

    assert result.rows == []
