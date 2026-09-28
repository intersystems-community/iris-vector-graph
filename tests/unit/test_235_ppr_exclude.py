"""Spec 235 US2: PPR edge exclusion (`exclude_predicates`) on the engine and fallback.

An entry is a bare predicate, which excludes that predicate from every source type, or
`Type.predicate`, which excludes it only where the edge's source key starts `Type/`.
No IRIS: the store and the fallback's cursor are fakes.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine.algorithms import _exclude_matches
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


class TestExcludeMatches:
    def test_bare_predicate_any_source(self):
        assert _exclude_matches("target", "Provenance/v1", "target")
        assert _exclude_matches("target", "AuditEvent/a1", "target")
        assert not _exclude_matches("target", "Provenance/v1", "entity")

    def test_typed_entry_only_that_type(self):
        assert _exclude_matches("Provenance.target", "Provenance/v1", "target")
        assert not _exclude_matches("Provenance.target", "Observation/o1", "target")
        assert not _exclude_matches("Provenance.target", "Provenance/v1", "entity")

    def test_other_type_same_predicate_kept(self):
        assert not _exclude_matches("Provenance.entity", "AuditEvent/a1", "entity")

    def test_type_is_a_whole_prefix(self):
        """`Provenance.target` does not match a `ProvenanceX/...` source."""
        assert not _exclude_matches("Provenance.target", "ProvenanceX/v1", "target")

    @pytest.mark.parametrize("entry", ["", ".p", "Type.", ".", "a.b.c"])
    def test_malformed_raises(self, entry):
        with pytest.raises(ValueError):
            _exclude_matches(entry, "Provenance/v1", "target")


# ------------------------------------------------------------------ fallback


NODES = ["Patient/p1", "Observation/o1", "Observation/o2", "Provenance/v1", "Encounter/e1"]
EDGES = [
    ("Observation/o1", "subject", "Patient/p1"),
    ("Observation/o2", "subject", "Patient/p1"),
    ("Observation/o1", "encounter", "Encounter/e1"),
    ("Encounter/e1", "subject", "Patient/p1"),
    ("Provenance/v1", "target", "Observation/o1"),
    ("Provenance/v1", "target", "Observation/o2"),
    ("Observation/o1", "in_patient_compartment", "Patient/p1"),
    ("Observation/o2", "in_patient_compartment", "Patient/p1"),
    ("Encounter/e1", "in_patient_compartment", "Patient/p1"),
]
_SELECT = re.compile(r"SELECT\s+(.+?)\s+FROM\s+(\S+)", re.S | re.I)


class _FakeCursor:
    """Answers the fallback's two reads (`nodes`, `rdf_edges`) from lists, returning
    whatever columns the SELECT names."""

    def __init__(self, nodes, edges):
        self.nodes, self.edges, self._rows = nodes, edges, []

    def execute(self, sql, params=None):
        cols, table = _SELECT.search(sql).groups()
        cols = [c.strip() for c in cols.split(",")]
        if "nodes" in table:
            self._rows = [(n,) for n in self.nodes]
        else:
            pick = {"s": 0, "p": 1, "o_id": 2}
            self._rows = [tuple(e[pick[c]] for c in cols) for e in self.edges]

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


def _fallback(edges, **kw):
    conn = MagicMock()
    conn.cursor.return_value = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    conn.cursor.return_value = _FakeCursor(NODES, edges)
    return eng._kg_PERSONALIZED_PAGERANK_python_fallback(["Patient/p1"], 0.85, 50, 1e-12, None, True, 1.0, **kw)


def _close(a, b):
    assert set(a) == set(b), (sorted(a), sorted(b))
    for k in a:
        assert abs(a[k] - b[k]) < 1e-12, (k, a[k], b[k])


class TestFallback:
    def test_none_is_unchanged(self):
        _close(_fallback(EDGES, exclude_predicates=None), _fallback(EDGES))

    def test_empty_list_is_unchanged(self):
        _close(_fallback(EDGES, exclude_predicates=[]), _fallback(EDGES))

    def test_bare_exclusion_equals_deleted_edges(self):
        kept = [e for e in EDGES if e[1] != "in_patient_compartment"]
        _close(_fallback(EDGES, exclude_predicates=["in_patient_compartment"]), _fallback(kept))

    def test_typed_exclusion_equals_deleted_edges(self):
        edges = EDGES + [("Encounter/e1", "target", "Observation/o2")]
        kept = [e for e in edges if not (e[0].startswith("Provenance/") and e[1] == "target")]
        _close(_fallback(edges, exclude_predicates=["Provenance.target"]), _fallback(kept))

    def test_exclusion_changes_the_divisor(self):
        """Removing an edge changes its source's out-degree, so the scores differ."""
        a = _fallback(EDGES)
        b = _fallback(EDGES, exclude_predicates=["in_patient_compartment"])
        assert a != b

    def test_default_reads_do_not_select_p(self):
        """No exclusion: the reads are the pre-235 reads."""
        conn = MagicMock()
        cur = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
        conn.cursor.return_value = cur
        eng = IRISGraphEngine(conn, embedding_dimension=4)
        cur.execute.reset_mock()
        cur.fetchall.side_effect = [[("a",)], [], []]
        eng._kg_PERSONALIZED_PAGERANK_python_fallback(["a"])
        sqls = [c.args[0] for c in cur.execute.call_args_list]
        assert any("SELECT s, o_id FROM" in s for s in sqls), sqls


# ------------------------------------------------------------------ engine -> store


def _eng():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    store = MagicMock()
    store.execute_ppr.return_value = IVGResult(columns=["id", "score"], rows=[["x", 1.0]])
    eng._store = store
    eng._store_capabilities = {"ppr": True}
    return eng, store, cursor


class TestEngineStore:
    def test_none_passes_neither(self):
        eng, store, _ = _eng()
        eng.kg_PERSONALIZED_PAGERANK(["a"], return_top_k=50)
        kw = store.execute_ppr.call_args.kwargs
        assert "exclude" not in kw and "limit" not in kw

    def test_list_passes_exclude_and_limit(self):
        eng, store, _ = _eng()
        eng.kg_PERSONALIZED_PAGERANK(["a"], return_top_k=50, exclude_predicates=["in_patient_compartment", "Provenance.target"])
        kw = store.execute_ppr.call_args.kwargs
        assert kw["exclude"] == ["in_patient_compartment", "Provenance.target"]
        assert kw["limit"] == 50

    def test_no_top_k_means_every_node(self):
        eng, store, _ = _eng()
        eng.kg_PERSONALIZED_PAGERANK(["a"], exclude_predicates=[])
        kw = store.execute_ppr.call_args.kwargs
        assert kw["exclude"] == [] and kw["limit"] == 0

    def test_malformed_raises_before_io(self):
        eng, store, cursor = _eng()
        cursor.execute.reset_mock()  # the constructor's own probes
        with pytest.raises(ValueError):
            eng.kg_PERSONALIZED_PAGERANK(["a"], exclude_predicates=["Type."])
        store.execute_ppr.assert_not_called()
        cursor.execute.assert_not_called()

    def test_objectscript_path_passes_exclude_and_limit(self):
        import json

        eng, _, _ = _eng()
        eng._store_capabilities = {"ppr": False}
        eng.capabilities = IRISCapabilities(objectscript_deployed=True, kg_built=True)
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = json.dumps([{"id": "a", "score": 0.5}])
        eng._iris_obj = lambda: iris_obj
        eng.kg_PERSONALIZED_PAGERANK(["a"], exclude_predicates=["in_patient_compartment"])
        args = iris_obj.classMethodValue.call_args.args
        assert json.loads(args[8]) == ["in_patient_compartment"]
        assert args[9] == 0

    def test_objectscript_path_default_is_six_args(self):
        import json

        eng, _, _ = _eng()
        eng._store_capabilities = {"ppr": False}
        eng.capabilities = IRISCapabilities(objectscript_deployed=True, kg_built=True)
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = json.dumps([{"id": "a", "score": 0.5}])
        eng._iris_obj = lambda: iris_obj
        eng.kg_PERSONALIZED_PAGERANK(["a"])
        assert len(iris_obj.classMethodValue.call_args.args) == 8


# ------------------------------------------------------------------ store -> RunJson


def _store(arno: bool):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._arno_available = arno
    store._arno_capabilities = {"algorithms": ["ppr"]} if arno else {}
    store._nkg_dirty = False
    return store


class TestStore:
    def test_default_call_is_unchanged(self):
        store = _store(False)
        store._call_classmethod = MagicMock(return_value="[]")
        store.execute_ppr(["a"], 0.85, 20)
        assert len(store._call_classmethod.call_args.args) == 8

    def test_exclude_and_limit_reach_runjson(self):
        import json

        store = _store(False)
        store._call_classmethod = MagicMock(return_value="[]")
        store.execute_ppr(["a"], 0.85, 20, exclude=["Provenance.target"], limit=0)
        args = store._call_classmethod.call_args.args
        assert json.loads(args[8]) == ["Provenance.target"] and args[9] == "0"

    def test_limit_alone_defaults_exclude_to_empty(self):
        store = _store(False)
        store._call_classmethod = MagicMock(return_value="[]")
        store.execute_ppr(["a"], 0.85, 20, limit=0)
        args = store._call_classmethod.call_args.args
        assert args[8] == "" and args[9] == "0"

    def test_exclusion_skips_arno(self):
        """`ArnoAccel.PPRJson` cannot exclude, so it must not answer."""
        store = _store(True)
        store._arno_call = MagicMock(return_value="[]")
        store._call_classmethod = MagicMock(return_value="[]")
        store.execute_ppr(["a"], 0.85, 20, exclude=["in_patient_compartment"])
        store._arno_call.assert_not_called()
        store._call_classmethod.assert_called_once()

    def test_empty_exclusion_default_limit_may_use_arno(self):
        store = _store(True)
        store._arno_call = MagicMock(return_value="[]")
        store.execute_ppr(["a"], 0.85, 20, exclude=[], limit=None)
        store._arno_call.assert_called_once()
