"""4.6.0 R134: `status().indexes` lists the BM25, IVF and PLAID indexes the namespace holds.

It read `kg_BM25Meta`, `kg_IVFMeta` and `kg_PlaidMeta`, tables nothing creates, and swallowed the error, so every list
was empty whatever had been built. The indexes are recorded where their classes keep them (`^BM25Idx`, `^IVF`,
`^PLAID`), and each class's `List()` answers their names: what `SHOW INDEXES` reads since R103
(`GraphSchema.global_indexes`). A `List()` that fails is an error on the report, not an empty list.

The calls are patched where the code makes them (`iris_vector_graph.schema._call_classmethod`); the live answer is in
`tests/e2e/test_460_status_index_inventory_e2e.py`.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.unit.route_fakes_227 import FakeRegistry, engine_with

LISTS = {"Graph.KG.BM25Index": "b1,b2", "Graph.KG.IVFIndex": "", "Graph.KG.PLAIDSearch": "p1"}


def _status(listed):
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)
    asked = []

    def call(conn, cls, method, *args):
        if method != "List":
            raise AssertionError(f"status() asked {cls}.{method}, which this test does not answer")
        asked.append(cls)
        answer = listed[cls]
        if isinstance(answer, Exception):
            raise answer
        return answer

    with patch("iris_vector_graph.schema._call_classmethod", call):
        status = engine.status()
    return status, asked, [sql for sql, _ in registry.statements]


def test_each_kind_is_read_from_its_class():
    status, asked, statements = _status(LISTS)
    assert sorted(asked) == sorted(LISTS)
    assert status.indexes.bm25_indexes == ["b1", "b2"]
    assert status.indexes.ivf_indexes == []
    assert status.indexes.plaid_indexes == ["p1"]
    assert not [s for s in statements if "Meta" in s], statements
    assert not [e for e in status.errors if "index list" in e], status.errors


def test_nothing_built_is_three_empty_lists_and_no_error():
    status, _, _ = _status({k: "" for k in LISTS})
    assert (status.indexes.bm25_indexes, status.indexes.ivf_indexes, status.indexes.plaid_indexes) == ([], [], [])
    assert not [e for e in status.errors if "index list" in e], status.errors


def test_a_list_that_fails_is_reported_and_the_others_still_read():
    status, _, _ = _status(dict(LISTS, **{"Graph.KG.IVFIndex": RuntimeError("<CLASS DOES NOT EXIST> IVFIndex")}))
    assert status.indexes.bm25_indexes == ["b1", "b2"] and status.indexes.plaid_indexes == ["p1"]
    assert "IVF index list failed (Graph.KG.IVFIndex.List): <CLASS DOES NOT EXIST> IVFIndex" in status.errors
