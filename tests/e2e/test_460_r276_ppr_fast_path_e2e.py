"""4.6.0 R276, live: ``kg_PERSONALIZED_PAGERANK``'s ObjectScript fast path (the store not answering PPR) raises on a
real ``<PARAMETER>`` from ``Graph.KG.PageRank.RunJson`` instead of answering from Python, and still answers from
RunJson when the call is good. The ``<PARAMETER>`` is IRIS's own: the call is sent one argument more than RunJson
takes, as a RunJson older than the arguments would see.

    IVG_NAMESPACE=IVG460FY6 IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \\
        pytest tests/e2e/test_460_r276_ppr_fast_path_e2e.py
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from unittest.mock import patch

import networkx as nx
import pytest
from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_python

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")]

NODES = ["a", "b", "c"]
EDGES = [("a", "R", "b"), ("b", "R", "c"), ("c", "R", "a"), ("a", "R", "c")]


@pytest.fixture(scope="module")
def world(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    prefix = f"R276_{uuid.uuid4().hex[:6]}_"
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    for n in NODES:
        eng.create_node(prefix + n)
    for s, p, o in EDGES:
        assert eng.create_edge(prefix + s, p, prefix + o)
    iris_connection.commit()
    try:
        yield prefix
    finally:
        eng.bulk_delete_nodes([prefix + n for n in NODES])
        iris_connection.commit()


@pytest.fixture
def fast_path(monkeypatch, iris_connection):
    """An engine whose store does not answer PPR, Arno off: the ObjectScript fast path answers."""
    from iris_vector_graph import IRISGraphEngine

    monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng._store_capabilities = {**eng._store_capabilities, "ppr": False}
    assert eng.capabilities.objectscript_deployed
    # The fast path also wants Graph.KG.Meta's kg_built marker, which a scratch namespace whose ^KG came only from
    # SQL writes (spec 252's index maintains it) has not set.
    eng.capabilities = dataclasses.replace(eng.capabilities, kg_built=True)
    return eng


def test_the_fast_path_answers_networkx(world, fast_path):
    prefix = world
    g = nx.MultiDiGraph()
    g.add_nodes_from(NODES)
    g.add_edges_from((s, o) for s, _p, o in EDGES)
    want = _pagerank_python(g, alpha=0.85, personalization={"a": 1}, tol=1e-10, max_iter=10000)
    with patch.object(
        fast_path, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fell back")
    ):
        got = fast_path.kg_PERSONALIZED_PAGERANK([prefix + "a"], max_iterations=1000, tolerance=1e-10)
    assert {k[len(prefix):]: v for k, v in got.items() if k.startswith(prefix)} == pytest.approx(want, abs=1e-6)
    # the namespace's other nodes answer 0.0 (4.7.0 row 9: every node of the graph has a row)
    assert all(v == 0.0 for k, v in got.items() if not k.startswith(prefix))


def test_a_real_parameter_error_raises(world, fast_path):
    from iris_vector_graph._engine import algorithms

    prefix = world
    with patch.object(algorithms, "_runjson_tail", return_value=("", 1000, 1e-6, "one too many")), patch.object(
        fast_path, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fell back")
    ), pytest.raises(RuntimeError, match=r"<PARAMETER>RunJson\^Graph\.KG\.PageRank.*initialize_schema"):
        fast_path.kg_PERSONALIZED_PAGERANK([prefix + "a"])
