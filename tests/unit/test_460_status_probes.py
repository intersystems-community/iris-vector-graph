"""4.6.0 R160: `status()` reads whether an HNSW index exists, and names every probe that failed in `status().errors`.

`indexes.hnsw_built` was `COUNT(*) FROM kg_NodeEmbeddings_optimized > 0`: a row count, not an index (spec 226 removed
the same reading from SHOW INDEXES). It now asks the class dictionary, as SHOW INDEXES does
(`GraphSchema.hnsw_indexes`), over every table that holds node vectors: the default table, its `_optimized` mirror and
each routed node table. R134 did the same for BM25, IVF and PLAID (each class's `List()`).

Probes that swallowed their failure, each now an entry in `status().errors`: the adjacency count (when the fallback
answered), the adjacency predicate read, the predicate-consistency count, the class check, the HNSW read and the
ledger read. `security.py` no longer lists `kg_BM25Meta`, `kg_IVFMeta` and `kg_PlaidMeta`, which R134 retired.

Each failure is forced explicitly: the native object, the cursor statement or the method that fails is named.

4.6.0 R199: the predicate read called `native.get(["KG", "out", "", 0, ""])` (a list where the Native API takes the
global's name, and subscripts that are not ^KG's layout) and then `native.orderAll`, which the Native API does not
have. Every server, 2026.1 and 2026.3 alike, answered `<SYNTAX>`, so `status()` of any namespace with edges listed a
probe error and the consistency check never ran. The MagicMock here answered both calls; the fake native object now
has the Native API's methods only (`spec_set`) and holds a `^KG("out", graph, s, p, o)` tree for `nextSubscript`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.unit.route_fakes_227 import FakeCursor, FakeRegistry, engine_with


class _Registry(FakeRegistry):
    """A FakeRegistry whose cursor raises on any statement containing one of `fail_on`'s keys, and answers `rows`
    for one containing one of `answer`'s."""

    def __init__(self, fail_on=None, answer=None, **kwargs):
        super().__init__(**kwargs)
        self.fail_on = dict(fail_on or {})
        self.answer = dict(answer or {})

    def cursor(self):
        registry = self

        class _Cursor(FakeCursor):
            def execute(self, sql, params=None):
                for needle, error in registry.fail_on.items():
                    if needle in str(sql):
                        raise error
                for needle, rows in registry.answer.items():
                    if needle in str(sql):
                        registry.statements.append((" ".join(str(sql).split()), list(params or [])))
                        self._rows = list(rows)
                        return self
                return super().execute(sql, params)

        return _Cursor(self)


#: The Native API methods (iris.IRIS) a probe may call; any other name raises AttributeError, as the driver does.
_NATIVE_API = ["classMethodValue", "isDefined", "nextSubscript", "get", "set", "kill"]


def _collation(key):
    return (isinstance(key, str), key)  # numbers before strings, as IRIS collates subscripts


def _native(kg_edges=0, fail=None, out=None):
    """A Native API object holding `out` as ^KG("out") ({graph key: {s: {p: {o: 1}}}}); by default one edge
    a -R-> b in the default graph (key 0)."""
    native = MagicMock(spec_set=_NATIVE_API)
    tree = {"out": {0: {"a": {"R": {"b": 1}}}} if out is None else out}

    def value(cls, method, *args):
        if fail and method in fail:
            raise fail[method]
        return {"KGEdgeCount": kg_edges, "NKGPopulated": 0}.get(method, "")

    def next_subscript(reverse, name, *subs):
        if fail and "nextSubscript" in fail:
            raise fail["nextSubscript"]
        assert name in ("^KG", "KG") and not reverse, (name, reverse)
        node = tree
        for sub in subs[:-1]:
            node = node[sub]
        keys = sorted(node, key=_collation)
        after = subs[-1]
        later = keys if after == "" else [k for k in keys if _collation(k) > _collation(after)]
        return later[0] if later else ""

    def get(name, *subs):
        if not isinstance(name, str):
            raise RuntimeError("<SYNTAX>")  # the server's answer to a list where the global's name goes
        return ""

    native.classMethodValue.side_effect = value
    native.isDefined.return_value = 1
    native.nextSubscript.side_effect = next_subscript
    native.get.side_effect = get
    return native


def _status(registry=None, native=None, hnsw=None, deployed=False, ledger_error=None):
    registry = registry or _Registry()
    engine = engine_with(registry, embedding_dimension=4)
    engine._iris_obj = lambda: native if native is not None else _native()
    engine.capabilities.objectscript_deployed = deployed
    asked = []

    def indexes(cursor, table, errors=None):
        asked.append(table)
        answer = (hnsw or {}).get(table.split(".")[-1], [])
        if isinstance(answer, Exception):
            if errors is None:
                return []
            errors.append(f"HNSW index read failed for {table}: {answer}")
            return []
        return answer

    patches = [
        patch("iris_vector_graph.schema.GraphSchema.hnsw_indexes", side_effect=indexes),
        patch("iris_vector_graph.schema._call_classmethod", return_value=""),
    ]
    if ledger_error is not None:
        patches.append(patch.object(type(engine), "_ledger_status", side_effect=ledger_error))
    for p in patches:
        p.start()
    try:
        status = engine.status()
    finally:
        for p in patches:
            p.stop()
    return status, asked


def test_rows_in_the_optimized_table_are_not_an_index():
    # the fake answers COUNT(*) = 1 for every table, so the row count said "built"
    status, asked = _status()
    assert status.indexes.hnsw_built is False
    assert any(t.endswith("kg_NodeEmbeddings") for t in asked), asked
    assert any(t.endswith("kg_NodeEmbeddings_optimized") for t in asked), asked


def test_an_hnsw_index_on_a_node_vector_table_is_built():
    status, _ = _status(hnsw={"kg_NodeEmbeddings": [("idx_ann", "emb")]})
    assert status.indexes.hnsw_built is True


def test_a_routed_node_table_is_asked_too():
    registry = _Registry(rows=[{"table_name": "kg_emb_78f993c3392521c9", "graph_id": "g", "kind": "node", "dimension": 4}])
    status, asked = _status(registry, hnsw={"kg_emb_78f993c3392521c9": [("idx_kg_emb_78f993c3392521c9_ann", "emb")]})
    assert any(t.endswith("kg_emb_78f993c3392521c9") for t in asked), asked
    assert status.indexes.hnsw_built is True


def test_a_failed_hnsw_read_is_reported():
    status, _ = _status(hnsw={"kg_NodeEmbeddings": RuntimeError("<PROTECT>")})
    assert status.indexes.hnsw_built is False
    assert any("HNSW index read failed" in e and "<PROTECT>" in e for e in status.errors), status.errors


def test_the_adjacency_count_failure_is_reported_when_the_fallback_answers():
    status, _ = _status(native=_native(fail={"KGEdgeCount": RuntimeError("<METHOD DOES NOT EXIST> KGEdgeCount")}))
    assert status.adjacency.kg_populated is True  # the fallback's answer
    assert any("KGEdgeCount" in e for e in status.errors), status.errors


def test_the_adjacency_predicate_read_failure_is_reported():
    status, _ = _status(native=_native(kg_edges=5, fail={"nextSubscript": RuntimeError("<PROTECT> ^KG")}))
    assert any("adjacency predicate probe failed" in e and "<PROTECT>" in e for e in status.errors), status.errors


PROBE = "WHERE s = ? AND p = ?"


def _probe_args(registry):
    return [args for sql, args in registry.statements if "rdf_edges" in sql and PROBE in sql]


def test_the_predicate_probe_asks_rdf_edges_for_the_first_adjacency_entry():
    # the first entry of ^KG("out", graph, s, p, o) in collation order: graph 0 (the default), then x:a, then AKA
    registry = _Registry()
    out = {0: {"x:b": {"LIKES": {"x:c": 1}}, "x:a": {"KNOWS": {"x:c": 1}, "AKA": {"x:d": 1}}}, "g2": {"x:a": {"Z": {}}}}
    status, _ = _status(registry, native=_native(kg_edges=5, out=out))
    probes = ("adjacency predicate", "predicate consistency", "Index mismatch")
    assert not [e for e in status.errors if any(p in e for p in probes)], status.errors
    assert status.adjacency.kg_predicates_consistent is True
    assert _probe_args(registry) == [["x:a", "AKA", ""]]


def test_a_named_graph_entry_is_asked_with_its_graph():
    registry = _Registry()
    _status(registry, native=_native(kg_edges=5, out={"g2": {"x:a": {"Z": {"x:b": 1}}}}))
    assert _probe_args(registry) == [["x:a", "Z", "g2"]]


def test_an_adjacency_entry_with_no_edge_row_is_a_mismatch():
    registry = _Registry(answer={PROBE: [(0,)]})
    status, _ = _status(registry, native=_native(kg_edges=5))
    assert status.adjacency.kg_predicates_consistent is False
    assert any(e.startswith("Index mismatch") and "a -R-> b" in e for e in status.errors), status.errors


def test_the_predicate_consistency_count_failure_is_reported():
    registry = _Registry(fail_on={PROBE: RuntimeError("[SQLCODE: <-99>:<Privilege violation>]")})
    status, _ = _status(registry, native=_native(kg_edges=5))
    assert any("predicate consistency probe failed" in e and "-99" in e for e in status.errors), status.errors


def test_the_class_check_failure_is_reported():
    registry = _Registry(fail_on={"%Dictionary.CompiledClass": RuntimeError("Communication link failure")})
    status, _ = _status(registry, deployed=True)
    assert any("class check failed" in e and "Graph.KG.Traversal" in e for e in status.errors), status.errors


def test_a_refused_class_check_is_answered_over_the_native_api():
    """4.6.0 R202: ADMIN_GUIDE's reading role may not SELECT from the class dictionary; one error per class before."""
    registry = _Registry(fail_on={"%Dictionary.CompiledClass": RuntimeError("[SQLCODE: <-99>:<Privilege violation>]")})
    native = _native(kg_edges=5)
    native.classMethodValue.side_effect = lambda cls, method, *args: (
        int(args[0] in ("Graph.KG.Traversal", "Graph.KG.PageRank")) if method == "IDKEYExists"
        else {"KGEdgeCount": 5, "NKGPopulated": 0}.get(method, ""))
    status, _ = _status(registry, native=native, deployed=True)
    assert not [e for e in status.errors if "class check" in e], status.errors
    assert status.objectscript.classes == ["Graph.KG.Traversal", "Graph.KG.PageRank"]


def test_the_ledger_read_failure_is_reported():
    status, _ = _status(ledger_error=RuntimeError("<UNDEFINED> ^IVG.Ledger"))
    assert status.ledger is None
    assert any("ledger status probe failed" in e and "<UNDEFINED>" in e for e in status.errors), status.errors


def test_nothing_failing_reports_none_of_these():
    status, _ = _status(native=_native(kg_edges=5))
    probes = ("HNSW", "KGEdgeCount", "adjacency predicate", "predicate consistency", "class check", "ledger status")
    assert not [e for e in status.errors if any(p in e for p in probes)], status.errors


def test_the_retired_meta_tables_are_not_valid_table_names():
    from iris_vector_graph.security import VALID_GRAPH_TABLES

    assert not {"kg_IVFMeta", "kg_BM25Meta", "kg_PlaidMeta"} & VALID_GRAPH_TABLES
