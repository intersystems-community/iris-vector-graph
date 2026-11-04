"""4.6.0 R184: each algorithm method and its Cypher procedure answer the same graph without USE GRAPH: the default one.

On a namespace where a named graph holds edges, `engine.strongly_connected_components()` (and `triangle_count`,
`k_core`, `eigenvector_centrality`) answered the default graph while `CALL ivg.scc()` (and the others) answered every
graph merged; `betweenness_centrality`, `closeness_centrality` and `leiden_communities` answered every graph merged too.

`^NKG`, which the seven procedures and the methods' fastest tiers read, is built from every graph's `^KG("out", g)`
with no graph key (Graph.KG.TraversalBuild), so it is the default graph's adjacency exactly when no named graph holds
an edge. `named_graph_with_edges` asks that of `^KG("out")`. When one does:

- the methods skip their `^NKG` tiers and answer from the default graph's `^KG("out"/"in", 0, ...)`: Arno's upload
  (`build_kg_adjacency_chunked`) or LazyKG;
- the procedures, which have no `^KG` path in ObjectScript, raise ValueError naming the procedure, the graph found and
  the engine method that answers the default graph.

The live pairs, with hand-worked answers, are in `tests/e2e/test_460_r184_default_graph_scope_e2e.py`.
"""

from __future__ import annotations

from unittest import mock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.stores.lazy_kg import named_graph_with_edges

#: each procedure, the engine method that answers the default graph, and a CALL of it
PAIRS = [
    ("ivg.betweenness", "betweenness_centrality", "CALL ivg.betweenness({}) YIELD node, score RETURN node, score"),
    ("ivg.closeness", "closeness_centrality", "CALL ivg.closeness({}) YIELD node, score RETURN node, score"),
    ("ivg.eigenvector", "eigenvector_centrality", "CALL ivg.eigenvector({}) YIELD node, score RETURN node, score"),
    ("ivg.leiden", "leiden_communities", "CALL ivg.leiden({}) YIELD node, community RETURN node, community"),
    ("ivg.triangleCount", "triangle_count", "CALL ivg.triangleCount({}) YIELD node, triangles RETURN node, triangles"),
    ("ivg.scc", "strongly_connected_components", "CALL ivg.scc({}) YIELD node, component RETURN node, component"),
    ("ivg.kcore", "k_core", "CALL ivg.kcore({}) YIELD node, coreness RETURN node, coreness"),
]


# ------------------------------------------------------------------------------------------------- the ^KG probe


class _Host:
    """`^KG("out", <graph>)`'s graph keys in collation order, as the Native API hands them back."""

    def __init__(self, keys):
        self.keys = list(keys)
        self.asked = []

    def next_subscript(self, name, *subs, reverse=False):
        assert name == "^KG" and subs[0] == "out" and len(subs) == 2, subs
        self.asked.append(subs[1])
        after = subs[1]
        if after == "":
            return self.keys[0] if self.keys else ""
        i = [str(k) for k in self.keys].index(str(after))
        return self.keys[i + 1] if i + 1 < len(self.keys) else ""


@pytest.mark.parametrize(
    "keys, found",
    [
        ([], None),  # nothing at all
        (["0"], None),  # the default graph alone, as the Native API spells key 0
        ([0], None),
        (["0", "g2"], "g2"),
        ([0, "g2", "g3"], "g2"),
        (["-1", "0"], "-1"),  # a graph named "-1" collates before the default graph's 0
        (["g2"], "g2"),  # a named graph and no default edges
    ],
)
def test_the_probe_names_the_first_graph_that_is_not_the_default(keys, found):
    host = _Host(keys)
    with mock.patch("iris_vector_graph.stores.lazy_kg.host_for", return_value=host):
        assert named_graph_with_edges(object()) == found


def test_the_probe_reads_no_further_than_the_first_named_graph():
    host = _Host(["0", "g2", "g3"])
    with mock.patch("iris_vector_graph.stores.lazy_kg.host_for", return_value=host):
        named_graph_with_edges(object())
    assert host.asked == ["", "0"]


# --------------------------------------------------------------------------------------------- the Cypher forms


class _Engine:
    """What the translator asks of an engine here: the probe, and nothing else."""

    def __init__(self, named):
        self.named = named
        self.asked = 0

    def _named_graph_with_edges(self):
        self.asked += 1
        return self.named


def _translate(cypher, engine):
    return translate_to_sql(parse_query(cypher), {}, engine=engine)


@pytest.mark.parametrize("proc, method, cypher", PAIRS, ids=[p[0] for p in PAIRS])
def test_a_procedure_raises_on_a_namespace_where_a_named_graph_holds_edges(proc, method, cypher):
    with pytest.raises(ValueError) as e:
        _translate(cypher, _Engine("g2"))
    text = str(e.value)
    assert proc in text and "'g2'" in text and f"engine.{method}()" in text and "^NKG" in text, text


@pytest.mark.parametrize("proc, method, cypher", PAIRS, ids=[p[0] for p in PAIRS])
def test_a_procedure_translates_when_only_the_default_graph_holds_edges(proc, method, cypher):
    engine = _Engine(None)
    sql = _translate(cypher, engine).sql
    assert engine.asked == 1
    fn = {"ivg.triangleCount": "kg_TriangleCount", "ivg.kcore": "kg_KCore", "ivg.scc": "kg_SCC"}.get(
        proc, "kg_" + proc.split(".")[1].capitalize()
    )
    assert fn in (sql if isinstance(sql, str) else " ".join(sql)), sql


@pytest.mark.parametrize("proc, method, cypher", PAIRS[:1], ids=[PAIRS[0][0]])
def test_a_probe_answer_that_is_not_a_name_is_no_named_graph(proc, method, cypher):
    """A mock engine's probe answers a MagicMock: only a str names a graph (as `_multigraph` reads only True)."""
    engine = mock.MagicMock()
    assert _translate(cypher, engine).sql


@pytest.mark.parametrize("proc, method, cypher", PAIRS, ids=[p[0] for p in PAIRS])
def test_use_graph_still_refuses_first(proc, method, cypher):
    """R163's refusal is unchanged, and the probe is not asked under USE GRAPH."""
    engine = _Engine(None)
    with pytest.raises(ValueError, match="cannot be scoped to a graph"):
        _translate(f"USE GRAPH 'g2' {cypher}", engine)
    assert engine.asked == 0


def test_the_engine_probe_reads_its_connection():
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = object()
    with mock.patch("iris_vector_graph.stores.lazy_kg.host_for", return_value=_Host(["0", "g2"])):
        assert eng._named_graph_with_edges() == "g2"


# -------------------------------------------------------------------------------------------- the Python methods


def _store(calls, named):
    """An IRISGraphStore whose class-method calls are recorded, every one answering a merged graph's row."""
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    iris_obj = mock.Mock()

    def cmv(cls, method, *args):
        calls.append(method)
        if method == "IsLoaded":
            return 0
        return 'OK:[{"id":"merged","score":1.0,"community":0,"size":1}]'

    iris_obj.classMethodValue.side_effect = cmv
    store._iris_obj = lambda: iris_obj
    store.conn = mock.Mock()
    store._named_graph_with_edges = lambda: named
    return store


class _DefaultGraph:
    """LazyKG over the default graph: a -> b -> a."""

    def __init__(self, *a, **kw):
        assert kw.get("graph") in (None, ""), kw

    def iter_nodes(self):
        return iter(["a", "b"])

    def out_neighbors(self, n):
        return {"a": ["b"], "b": ["a"]}[n]

    def in_neighbors(self, n):
        return {"a": ["b"], "b": ["a"]}[n]


#: each store method, its arguments, and the class methods that read ^NKG
NKG_TIERS = [
    ("_betweenness_gref", (0, "out", 0, 10, 256, None), {"BetweennessGlobal"}),
    ("_closeness_gref", ("harmonic", "out", 0, 10, None), {"ClosenessGlobalMSBFS", "ClosenessGlobal"}),
    ("_eigenvector_gref", (30, 1e-6, 10, None), {"EigenvectorGlobal"}),
]


@pytest.mark.parametrize("method, args, nkg", NKG_TIERS, ids=[t[0] for t in NKG_TIERS])
def test_a_method_skips_nkg_when_a_named_graph_holds_edges(method, args, nkg):
    calls = []
    store = _store(calls, "g2")
    with mock.patch("iris_vector_graph.stores.lazy_kg.LazyKG", _DefaultGraph):
        res = getattr(store, method)(*args)
    assert not (set(calls) & nkg), calls
    assert sorted(r[0] for r in res.rows) == ["a", "b"], res.rows


@pytest.mark.parametrize("method, args, nkg", NKG_TIERS, ids=[t[0] for t in NKG_TIERS])
def test_a_method_reads_nkg_when_only_the_default_graph_holds_edges(method, args, nkg):
    calls = []
    res = getattr(_store(calls, None), method)(*args)
    assert set(calls) & nkg, calls
    assert [r[0] for r in res.rows] == ["merged"], res.rows


def test_leiden_skips_the_server_side_nkg_read_when_a_named_graph_holds_edges():
    calls = []
    store = _store(calls, "g2")
    with mock.patch.dict("os.environ", {"IVG_DISABLE_ARNO": "1"}), mock.patch(
        "iris_vector_graph.stores.lazy_kg.LazyKG", _DefaultGraph
    ):
        res = store.execute_leiden(10, 1.0, 1e-4, 10, 256, 0)
    assert "LeidenJsonAuto" not in calls, calls
    assert sorted(r[0] for r in res.rows) == ["a", "b"], (res.rows, res.error)


def test_leiden_reads_nkg_server_side_when_only_the_default_graph_holds_edges():
    calls = []
    res = _store(calls, None).execute_leiden(10, 1.0, 1e-4, 10, 256, 0)
    assert "LeidenJsonAuto" in calls and [r[0] for r in res.rows] == ["merged"], (calls, res.rows)


def test_the_store_probe_reads_its_connection():
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = object()
    with mock.patch("iris_vector_graph.stores.lazy_kg.host_for", return_value=_Host([0, "g2"])):
        assert store._named_graph_with_edges() == "g2"


class _Chain:
    """LazyKG over a -> b -> c."""

    def __init__(self, *a, **kw):
        pass

    def iter_nodes(self):
        return iter(["a", "b", "c"])

    def out_neighbors(self, n):
        return {"a": ["b"], "b": ["c"], "c": []}[n]

    def in_neighbors(self, n):
        return {"a": [], "b": ["a"], "c": ["b"]}[n]


def test_the_default_graph_closeness_is_on_the_nkg_tiers_scale():
    """Harmonic closeness is the raw sum of 1/d, as ClosenessGlobal and ClosenessGlobalMSBFS answer it (and
    networkx.harmonic_centrality): the LazyKG tier divided it by n - 1, so whether a named graph held edges decided
    the scale of the default graph's answer. a reaches b at 1 and c at 2: 1 + 1/2."""
    with mock.patch("iris_vector_graph.stores.lazy_kg.LazyKG", _Chain):
        res = _store([], "g2")._closeness_gref("harmonic", "out", 0, 0, None)
    assert dict((n, s) for n, s in res.rows) == {"a": 1.5, "b": 1.0, "c": 0.0}
