"""Spec 227 T027 — an empty scoped answer stays empty.

The failure this guards against is not a wrong row, it is a *helpful* one. A read
that finds nothing in graph A and then retries without the predicate looks like a
sensible fallback and is the leak itself: the caller asked about one graph and got
another graph's neighbours, with no error to notice.

Two behaviours, asserted by counting statements rather than rows:

- a scoped KNN that matches nothing issues its scoped statement and stops. No
  second, wider statement (FR-004).
- `vector_search` aimed at a routed embedding table with no `graph` raises, with
  a reason naming the table, instead of scanning the whole route or quietly
  reading the default graph. A route holds one graph's vectors per model; a
  caller who did not say which graph did not ask a well-formed question, and
  answering it with the default graph would be a guess (FR-041). A non-route
  table keeps its 3.2.0 behaviour — `vector_search` is also the generic
  any-table helper, and breaking that is out of 227's scope.
"""

import pytest
from unittest.mock import MagicMock

from iris_vector_graph.constants import ROUTE_TABLE_PREFIX
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import teach_registry_route

GRAPH = "ivg227-empty-A"


def _engine(*, rows=None):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = rows if rows is not None else []
    cursor.fetchone.return_value = None
    cursor.description = [("id",), ("score",)]
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    # The pair has to be routed for a statement to exist to inspect: an unrouted
    # pair reads nothing at all, which would pass every assertion below vacuously.
    teach_registry_route(cursor, GRAPH)
    # The construction-time `WHERE 1=0` native-vector probe names VECTOR_COSINE and
    # would otherwise be counted as a read. It ranks nothing; dropped here so that
    # "no scoring statement" means what it says.
    cursor.execute.reset_mock()
    return engine, cursor


def _scoring_statements(cursor):
    return [
        " ".join(str(c.args[0]).split())
        for c in cursor.execute.call_args_list
        if c.args and "VECTOR_COSINE" in str(c.args[0])
    ]


# --- no widening after an empty answer ------------------------------------------


def test_an_empty_scoped_knn_does_not_retry_unscoped():
    eng, cursor = _engine(rows=[])

    result = eng.kg_KNN_VEC("[0.1,0.2,0.3,0.4]", k=5, graph=GRAPH)

    assert result == []
    statements = _scoring_statements(cursor)
    assert statements, "no scoring statement was issued at all"
    unscoped = [s for s in statements if "COALESCE" not in s]
    assert not unscoped, (
        "an empty scoped answer was followed by an unscoped statement — this is "
        f"the widening FR-004 forbids: {unscoped}"
    )


def test_an_empty_scoped_knn_binds_the_same_graph_every_time():
    """Not just 'no unscoped retry' — no retry against a *different* graph either."""
    eng, cursor = _engine(rows=[])

    eng.kg_KNN_VEC("[0.1,0.2,0.3,0.4]", k=5, graph=GRAPH)

    for c in cursor.execute.call_args_list:
        if c.args and "VECTOR_COSINE" in str(c.args[0]):
            params = list(c.args[1]) if len(c.args) > 1 else []
            graph_params = [p for p in params if isinstance(p, str) and p.startswith("ivg227")]
            assert graph_params in ([GRAPH], []), (
                f"a scoring statement bound a graph other than the one asked for: {params}"
            )


def test_a_missing_seed_node_returns_empty_rather_than_searching_every_graph():
    """`kg_KNN_VEC("node:absent", graph=A)` finds no seed vector.

    Returning `[]` is right. Falling through to a search with an unconverted
    query string would be a wrong answer dressed as a result.
    """
    eng, cursor = _engine(rows=[])
    cursor.fetchone.return_value = None

    assert eng.kg_KNN_VEC("node:absent", k=5, graph=GRAPH) == []
    assert not _scoring_statements(cursor), (
        "a missing seed vector still produced a scoring statement"
    )


# --- vector_search against a routed table --------------------------------------


def test_vector_search_on_a_routed_table_with_no_graph_raises():
    eng, _ = _engine()
    route = route_table_name(GRAPH, "model-a")

    with pytest.raises(ValueError) as exc:
        eng.vector_search(f"Graph_KG.{route}", "emb", [0.1, 0.2, 0.3, 0.4])

    message = str(exc.value)
    assert route in message, f"the reason must name the table: {message}"
    assert "graph" in message.lower(), f"the reason must say what is missing: {message}"


def test_vector_search_on_a_routed_table_issues_no_sql_when_it_raises():
    """A raise that already scanned the route would have leaked before erroring."""
    eng, cursor = _engine()
    route = route_table_name(GRAPH, "model-a")

    with pytest.raises(ValueError):
        eng.vector_search(f"Graph_KG.{route}", "emb", [0.1, 0.2, 0.3, 0.4])

    assert not _scoring_statements(cursor), (
        "the route was scanned before the ValueError was raised"
    )


def test_vector_search_on_a_routed_table_with_a_graph_is_scoped():
    eng, cursor = _engine(rows=[])

    eng.vector_search(
        f"Graph_KG.{route_table_name(GRAPH, 'model-a')}",
        "emb",
        [0.1, 0.2, 0.3, 0.4],
        graph=GRAPH,
    )

    statements = _scoring_statements(cursor)
    assert statements, "no statement was issued"
    for sql in statements:
        assert "COALESCE" in sql, f"a routed read went out unscoped: {sql}"


def test_vector_search_on_a_non_route_table_keeps_3_2_0_behaviour():
    """`vector_search` is also the generic helper; 227 does not break that."""
    eng, cursor = _engine(rows=[])

    eng.vector_search("Graph_KG.docs", "emb", [0.1, 0.2, 0.3, 0.4])

    assert _scoring_statements(cursor), (
        "a non-route table must still be searchable without a graph"
    )


def test_the_route_prefix_is_what_makes_a_table_a_route():
    """Recorded as a test so the check cannot drift to a different rule.

    The decision is deliberately name-based rather than registry-based: refusing
    the call must not require a database read, or the refusal itself becomes a
    query that could fail open.
    """
    eng, _ = _engine()
    assert ROUTE_TABLE_PREFIX == "kg_emb_"

    with pytest.raises(ValueError):
        eng.vector_search(f"Graph_KG.{ROUTE_TABLE_PREFIX}deadbeefdeadbeef", "emb", [0.1])
