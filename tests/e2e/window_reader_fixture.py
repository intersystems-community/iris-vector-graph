"""Shared set-up for the spec 255 E2E tests: a named graph of its own per module, loaded server-side, erased after.

Every module writes into a graph named for it, so nothing it does is visible to another module's reads, and
`erase_graph` takes the temporal subtrees, the structural rows and the nodes the temporal writes asserted.
"""

from __future__ import annotations

import json
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

INDEX = "Graph.KG.TemporalIndex"
WINDOW = "Graph.KG.TemporalWindow"


def native_of(engine):
    return engine._iris_obj()


def bulk_insert(native, graph, edges, batch=1000, cls=INDEX):
    """Write edges ({"s","p","o","ts","w","attrs"?,"sri"?}) with TemporalIndex.BulkInsert, server-side."""
    written = 0
    for k in range(0, len(edges), batch):
        written += int(native.classMethodValue(cls, "BulkInsert", graph, json.dumps(edges[k : k + batch]), 0, ""))
    return written


def query_window(native, graph, source, predicate, start, end, inbound=False):
    method = "QueryWindowInbound" if inbound else "QueryWindow"
    return json.loads(str(native.classMethodValue(INDEX, method, graph, source, predicate, start, end)))


def edge_attrs(native, graph, e):
    return json.loads(str(native.classMethodValue(INDEX, "GetEdgeAttrs", graph, e["ts"], e["s"], e["p"], e["o"])))


def key(e):
    return (e["ts"], e["s"], e["p"], e["o"])


def coords(events, with_attrs=True):
    """The reader's events reduced to what QueryWindow + GetEdgeAttrs can say about them."""
    out = []
    for e in events:
        row = {"s": e["s"], "p": e["p"], "o": e["o"], "ts": e["ts"], "w": e["w"]}
        if with_attrs:
            row["attrs"] = e["attrs"]
        out.append(row)
    return out


def _fresh(request, iris_connection):
    engine = IRISGraphEngine(iris_connection)
    native = native_of(engine)
    tag = request.module.__name__.rsplit(".", 1)[-1].replace("test_", "")[:20]
    graph = f"g255_{tag}_{uuid.uuid4().hex[:6]}"
    engine.erase_graph(graph)
    return engine, native, graph


@pytest.fixture(scope="module")
def graph_env(request, iris_connection):
    """(engine, native, graph): a fresh named graph for this module, erased before and after."""
    engine, native, graph = _fresh(request, iris_connection)
    yield engine, native, graph
    engine.erase_graph(graph)


@pytest.fixture
def fresh_graph(request, iris_connection):
    """(engine, native, graph): a fresh named graph for one test, for tests that change what they read."""
    engine, native, graph = _fresh(request, iris_connection)
    yield engine, native, graph
    engine.erase_graph(graph)
