"""BFS is the read path a variable-length Cypher pattern takes, and it was graph-blind.

The two-graph Gate 3 probe added in T025 found this: with one node ID present in
`__smoke_g1` and `__smoke_g2`, each holding one distinct neighbour,

    USE GRAPH '__smoke_g1' MATCH (a)-[:SMOKE*1..1]->(b) WHERE a.node_id = '__smoke_a'

returned *both* neighbours, while the single-hop `[:SMOKE]` form (which is pure
translated SQL) returned exactly the right one. Storage was never the problem —
`Graph_KG.rdf_edges` carries the right `graph_id` on each row and
`^KG("out", <graph>, ...)` holds exactly one edge per graph.

Two defects, in series:

1. `Graph.KG.TraversalBFS` opened every helper with `Set pGraph = 0` — the default
   graph's `^KG` subscript (`Graph.KG.GraphKey.ForIndex("")`), hardcoded. BFS could
   only ever traverse the default graph, so for a named graph it found nothing.
2. Finding nothing, `_run_objectscript_bfs` fell through to `_sql_bfs_fallback`,
   which queries `Graph_KG.rdf_edges` with no graph predicate at all. That is where
   both graphs' neighbours came from.

Defect 2 is the leak and defect 1 is what exposed it: a named-graph BFS that
returned nothing would have been a visible bug, but one that silently degrades into
an unscoped SQL scan reads as a working traversal.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.stores.iris_sql_store import (
    IRISGraphStore,
    _ArnoBfsAdapter,
    _ObjectScriptBfsAdapter,
)

REPO = Path(__file__).parent.parent.parent


def _store() -> IRISGraphStore:
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._namespace = "USER"
    store._namespace_checked = True
    store._arno_available = False
    store._arno_capabilities = {}
    return store


# ---------------------------------------------------------------------------
# The interface: `graph` reaches the BFS adapters
# ---------------------------------------------------------------------------


def test_the_store_protocol_declares_a_graph_on_execute_bfs():
    """A traversal that cannot be told which graph to walk cannot be scoped.

    `GraphStore` is the seam every engine read crosses, so the parameter has to be
    on the protocol and not only on the IRIS implementation — a store that omits it
    would silently traverse the default graph for every caller.
    """
    import inspect

    from iris_vector_graph.store_protocol import GraphStore

    sig = inspect.signature(GraphStore.execute_bfs)
    assert "graph" in sig.parameters, (
        "GraphStore.execute_bfs takes no graph, so no caller can scope a traversal: "
        f"{sig}"
    )


def test_the_objectscript_bfs_call_carries_the_graph():
    """`BFSFastJsonSorted` is where the graph has to arrive to reach `^KG`."""
    store = _store()
    with patch.object(store, "_call_classmethod", return_value="SORTED:0") as cm:
        with patch.object(store, "_sql_bfs_fallback", return_value=MagicMock()):
            store._run_objectscript_bfs("a", ["SMOKE"], 1, "out", 0, graph="g1")
    args = cm.call_args[0]
    assert "g1" in args, (
        "the graph never reaches Graph.KG.Traversal.BFSFastJsonSorted, so ^KG is "
        f"walked at the default graph key regardless of the query: {args}"
    )


def test_the_default_graph_reaches_objectscript_as_the_empty_string():
    """`GraphKey.ForIndex("")` is 0; deriving that is ObjectScript's job, not Python's.

    ADR-0003 puts both graph-key derivations in `Graph.KG.GraphKey` and forbids
    re-deriving either. Python passes the *name* (`''` for the default graph) and
    lets `ForIndex` turn it into the subscript.
    """
    store = _store()
    with patch.object(store, "_call_classmethod", return_value="SORTED:0") as cm:
        with patch.object(store, "_sql_bfs_fallback", return_value=MagicMock()):
            store._run_objectscript_bfs("a", [], 1, "out", 0, graph=None)
    assert cm.call_args[0][-1] == "", (
        "an unscoped BFS must pass the default graph as '' and let ForIndex map it "
        f"to 0: {cm.call_args[0]}"
    )


def test_the_sql_bfs_fallback_scopes_every_hop_by_graph():
    """This is the leak the Gate 3 probe caught.

    The fallback ran `WHERE s IN (?)` against `Graph_KG.rdf_edges` with no graph
    predicate, so a named-graph traversal that reached it returned every graph's
    neighbours. COALESCE on both sides because `graph_id` is `NOT NULL DEFAULT ''`
    on a fresh install but an upgraded row can still read as NULL.
    """
    store = _store()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    store.conn.cursor.return_value = cursor

    store._sql_bfs_fallback("a", ["SMOKE"], 1, "out", 0, graph="g1")

    assert cursor.execute.called, "the fallback issued no SQL"
    sql, params = cursor.execute.call_args[0][0], cursor.execute.call_args[0][1]
    assert "graph_id" in sql, (
        f"the SQL BFS fallback has no graph predicate — it scans every graph: {sql}"
    )
    assert "g1" in params, (
        f"the graph is in the SQL but never bound: {sql} / {params}"
    )


def test_the_sql_bfs_fallback_scopes_a_both_direction_traversal():
    """`direction='both'` takes a separate code path with its own two statements."""
    store = _store()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    store.conn.cursor.return_value = cursor

    store._sql_bfs_fallback("a", [], 1, "both", 0, graph="g1")

    for call in cursor.execute.call_args_list:
        sql, params = call[0][0], call[0][1]
        assert "graph_id" in sql, f"unscoped statement on the both-direction path: {sql}"
        assert "g1" in params, f"graph not bound on the both-direction path: {params}"


def test_a_named_graph_does_not_take_the_arno_path():
    """`Graph.KG.NKGAccel.BFSJson` has no graph argument.

    The Rust accelerator reads `^NKG`, whose integer re-keying predates named
    graphs, and its entry point cannot be told which graph to walk. Routing a
    named-graph traversal through it would be the same leak in a faster loop, so
    a named graph takes the ObjectScript adapter — slower, and correct. The default
    graph still gets Arno, which is where the accelerator's numbers were measured.
    """
    store = _store()
    with patch.object(store, "_detect_arno", return_value=True):
        store._arno_capabilities = {"bfs": True}
        assert isinstance(store._select_bfs_strategy(graph=None), _ArnoBfsAdapter)
        assert isinstance(store._select_bfs_strategy(graph="g1"), _ObjectScriptBfsAdapter), (
            "a named-graph BFS was routed to Arno, which cannot scope by graph"
        )


def test_execute_bfs_threads_the_graph_to_the_strategy():
    store = _store()
    strategy = MagicMock()
    with patch.object(store, "_select_bfs_strategy", return_value=strategy):
        store.execute_bfs("a", [], 1, "out", 0, graph="g1")
    assert "g1" in strategy.run.call_args[0] or (
        strategy.run.call_args[1].get("graph") == "g1"
    ), f"execute_bfs dropped the graph before the adapter: {strategy.run.call_args}"


# ---------------------------------------------------------------------------
# The ObjectScript side: no helper may hardcode the graph key
# ---------------------------------------------------------------------------


def _traversal_bfs_source() -> str:
    return (REPO / "iris_src" / "src" / "Graph" / "KG" / "TraversalBFS.cls").read_text()


def test_no_bfs_helper_hardcodes_the_default_graph_key():
    """`Set pGraph = 0` is the whole of defect 1, in one line, eight times over.

    0 is `GraphKey.ForIndex("")` — correct for the default graph and blind to every
    other one. The helpers have to take the graph from their caller instead.
    """
    src = _traversal_bfs_source()
    hardcoded = [
        lineno
        for lineno, line in enumerate(src.splitlines(), start=1)
        if re.match(r"\s*Set\s+pGraph\s*=\s*0\s*$", line)
    ]
    assert not hardcoded, (
        "TraversalBFS.cls still hardcodes the default graph key at lines "
        f"{hardcoded}. A BFS helper must receive pGraph from its caller, which "
        "derives it once via Graph.KG.GraphKey.ForIndex(graph)."
    )


def test_bfsfast_takes_a_graph_and_derives_the_subscript_through_graphkey():
    """ADR-0003: `GraphKey` owns the derivation; no caller re-derives it."""
    src = _traversal_bfs_source()
    m = re.search(r"ClassMethod BFSFast\(([^)]*)\)", src)
    assert m, "BFSFast is gone from TraversalBFS.cls"
    assert "graph" in m.group(1), (
        f"BFSFast cannot be told which graph to walk: {m.group(1)}"
    )
    assert "GraphKey).ForIndex(" in src, (
        "TraversalBFS.cls derives the ^KG subscript without Graph.KG.GraphKey, "
        "which ADR-0003 forbids — that is how two spellings of one graph name "
        "come to key two different subtrees."
    )


@pytest.mark.parametrize(
    "method",
    [
        "BFSFastJson",
        "BFSFastJsonDirect",
        "BFSFastJsonChunked",
        "BFSFastJsonSorted",
        "BFSFastCountDistinct",
    ],
)
def test_every_bfs_entry_point_accepts_a_graph(method):
    """Each of these is called from Python, and each one calls BFSFast.

    An entry point that takes no graph cannot pass one on, so it would traverse the
    default graph no matter what the query asked for.
    """
    for path in ("TraversalBFS.cls", "Traversal.cls"):
        src = (REPO / "iris_src" / "src" / "Graph" / "KG" / path).read_text()
        m = re.search(r"ClassMethod " + method + r"\(([^)]*)\)", src)
        assert m, f"{method} is gone from {path}"
        assert "graph" in m.group(1), (
            f"{path}:{method} takes no graph: {m.group(1)}"
        )


def test_the_traversal_facade_forwards_the_graph():
    """`Graph.KG.Traversal` is a delegating facade (spec 187) and Python calls it.

    A delegator that accepts `graph` and then calls the sibling without it is the
    same bug one level up, and it compiles cleanly.
    """
    src = (REPO / "iris_src" / "src" / "Graph" / "KG" / "Traversal.cls").read_text()
    for m in re.finditer(
        r"ClassMethod (BFSFast\w*)\([^)]*graph[^)]*\)[^{]*\{([^}]*)\}", src
    ):
        name, body = m.group(1), m.group(2)
        assert "graph" in body, (
            f"Graph.KG.Traversal.{name} accepts a graph and drops it on the way to "
            f"Graph.KG.TraversalBFS: {body.strip()}"
        )


# ---------------------------------------------------------------------------
# The engine side: the query's graph reaches the traversal
# ---------------------------------------------------------------------------


def test_the_translator_records_the_graph_on_the_sql_query():
    """`USE GRAPH` is parsed into `cypher_query.graph_context` and applied to the SQL.

    The variable-length path never runs that SQL — `_route_var_length` intercepts it
    and calls BFS — so the graph has to survive on the `SQLQuery` object itself, not
    only inside the generated predicates.
    """
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    parsed = parse_query(
        "USE GRAPH 'g1' MATCH (a)-[:SMOKE*1..1]->(b) "
        "WHERE a.node_id = 'x' RETURN b.node_id"
    )
    sql_query = translate_to_sql(parsed)
    assert getattr(sql_query, "graph_context", None) == "g1", (
        "the SQLQuery does not carry the USE GRAPH context, so _route_var_length "
        "has nothing to pass to execute_bfs"
    )


def test_route_var_length_passes_the_graph_to_execute_bfs():
    """The end of the chain: a scoped Cypher VLP produces a scoped BFS call."""
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.result import IVGResult

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._nkg_dirty = False
    engine._store = MagicMock()
    engine._store.execute_bfs.return_value = IVGResult(
        columns=["id", "hops", "pred"], rows=[]
    )
    engine._store.get_nodes.return_value = IVGResult(columns=[], rows=[])
    engine._store.query_nodes.return_value = IVGResult(columns=[], rows=[])

    # A bound parameter, not a literal: an inlined 'x' gives the translator no `?`
    # to recognise, so the route takes the labeled multi-source branch instead of
    # the single-source BFS this test is about.
    parsed = parse_query(
        "USE GRAPH 'g1' MATCH (a)-[:SMOKE*1..1]->(b) "
        "WHERE a.node_id = $src RETURN b.node_id"
    )
    sql_query = translate_to_sql(parsed, {"src": "x"})
    engine._route_var_length(sql_query, {"src": "x"})

    calls = engine._store.execute_bfs.call_args_list
    assert calls, "the VLP route never reached execute_bfs"
    for call in calls:
        graph = call[1].get("graph") if call[1] else None
        if graph is None and call[0]:
            graph = call[0][-1] if isinstance(call[0][-1], str) else None
        assert graph == "g1", (
            f"execute_bfs was called unscoped from a USE GRAPH query: {call}"
        )
