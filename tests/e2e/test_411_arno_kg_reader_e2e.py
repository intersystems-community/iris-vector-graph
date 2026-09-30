"""4.1.1 — ArnoAccel's default-graph readers answer the whole graph, every call.

The Rust `kg_*_global` readers behind PageRank, PPR, WCC, CDLP and subgraph walk the
live `^KG("out",0,...)` unreliably: the first call in a process sees only the first
source, later calls disagree. The fixture puts a filler source that sorts before the
seed, so a first-source-only reader misses the seed's successor. See
tests/unit/test_411_arno_kg_readers.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PRED = "IVG411_ARNO_READ"


@pytest.fixture
def graph(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true" or iris_connection is None:
        pytest.fail("needs ivg-iris-enterprise: scripts/enterprise-container.sh up")
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    # "!" sorts before letters and digits, so the filler is the first ^KG("out",0) source.
    filler, filler_to = f"!ivg411_{run}_a", f"!ivg411_{run}_b"
    seed, near, far = (f"ivg411_{run}_{s}" for s in ("seed", "near", "far"))
    edges = [(filler, filler_to), (seed, near), (near, far)]
    for s, o in edges:
        engine.create_node(s)
        engine.create_node(o)
        engine.create_edge(s, PRED, o)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    engine._store._detect_arno()  # load the library, as the store does
    yield engine, seed, near, far, filler
    for s, o in edges:
        engine.delete_edge(s, PRED, o)
    for n in {n for e in edges for n in e}:
        with contextlib.suppress(Exception):
            engine.delete_node(n)


def _call(engine, method, *args):
    return json.loads(str(engine._iris_obj().classMethodValue("Graph.KG.ArnoAccel", method, *args)))


def test_first_ppr_call_reaches_the_seeds_successors(graph):
    engine, seed, near, far, _ = graph
    rows = _call(engine, "PPRJson", json.dumps([seed]), "0.85", "20")
    ids = {r.get("id", r.get("node")) for r in rows}
    assert {seed, near, far} <= ids


def test_pagerank_and_subgraph_see_every_node(graph):
    engine, seed, near, far, filler = graph
    ranked = {r.get("id", r.get("node")) for r in _call(engine, "PageRankGlobalJson", "0.85", "20")}
    assert {seed, near, far, filler} <= ranked
    sub = _call(engine, "SubgraphJson", json.dumps([seed]), 2, "", 10000)
    nodes = {n if isinstance(n, str) else n.get("id") for n in sub.get("nodes", [])}
    assert {seed, near, far} <= nodes


def test_repeated_calls_agree(graph):
    engine, seed, *_ = graph
    for method, args in (
        ("WCCJson", (100,)),
        ("CDLPJson", (10,)),
        ("PPRJson", (json.dumps([seed]), "0.85", "20")),
    ):
        answers = [json.dumps(_call(engine, method, *args), sort_keys=True) for _ in range(4)]
        assert len(set(answers)) == 1, method
