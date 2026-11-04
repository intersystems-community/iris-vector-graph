"""4.6.0 R083: the store's KNN label filter reads the labels of the graph it searches.

`execute_knn_vec(..., label_filter, graph=G)` joined `rdf_labels` on the node id alone, so a node of G that is not
labelled in G passed the filter when a node with the same id is labelled in another graph H.

The graph, worked by hand. In G: a labelled Ivg460Lbl, b labelled Ivg460Other; vectors a = [1,0,0], b = [1,1,0]. In H:
b labelled Ivg460Lbl (no vector). Against q = [1,0,0] the cosines in G are a 1, b 1/sqrt(2); with the filter Ivg460Lbl
only a is a candidate in G, so k = 3 answers [a] alone. In H the filter matches b, which has no vector there: no
neighbours.
"""

from __future__ import annotations

import contextlib
import math
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

LABEL = "Ivg460Lbl"


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    graph, other = f"ivg460lbl_{run}_g", f"ivg460lbl_{run}_h"
    ids = {k: f"ivg460lbl_{run}_{k}" for k in "ab"}
    engine.create_node(ids["a"], labels=[LABEL], graph=graph)
    engine.create_node(ids["b"], labels=["Ivg460Other"], graph=graph)
    engine.create_node(ids["b"], labels=[LABEL], graph=other)
    assert engine.store_embedding(ids["a"], [1.0, 0.0, 0.0], graph=graph)
    assert engine.store_embedding(ids["b"], [1.0, 1.0, 0.0], graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, graph, other, ids
    engine.erase_graph(graph)
    engine.erase_graph(other)


def _named(rows, ids):
    inverse = {v: k for k, v in ids.items()}
    return [(inverse.get(r[0], r[0]), round(float(r[1]), 5)) for r in rows]


def test_without_a_filter_both_nodes_of_the_graph_answer(g):
    engine, graph, _, ids = g
    result = engine.cypher.execute_knn_vec([1.0, 0.0, 0.0], 3, None, graph=graph)
    assert result.error is None, result.error
    assert _named(result.rows, ids) == [("a", 1.0), ("b", round(1 / math.sqrt(2), 5))]


def test_a_label_held_in_another_graph_does_not_pass_the_filter(g):
    engine, graph, _, ids = g
    result = engine.cypher.execute_knn_vec([1.0, 0.0, 0.0], 3, LABEL, graph=graph)
    assert result.error is None, result.error
    assert _named(result.rows, ids) == [("a", 1.0)]


def test_the_other_graphs_label_finds_no_vector_there(g):
    engine, _, other, ids = g
    result = engine.cypher.execute_knn_vec([1.0, 0.0, 0.0], 3, LABEL, graph=other)
    assert (result.error, result.rows) == (None, [])


def test_call_ivg_vector_search_with_a_vector_scopes_the_label_too(g):
    engine, graph, _, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.vector.search('{LABEL}', 'embedding', [1.0, 0.0, 0.0], 3) "
        "YIELD node, score RETURN node, score"
    )
    assert result.error is None, result.error
    assert _named(result.rows, ids) == [("a", 1.0)]
