"""Spec 227 T026 — every KNN statement carries a graph predicate.

`kg_KNN_VEC` builds four different statements depending on whether a label filter
and an exclude-ID are present, and `_kg_KNN_VEC_python_optimized` builds two more.
Six statements is six places to forget a predicate, and forgetting one is silent:
the query still returns rows, just other graphs' rows too.

So this asserts the shape of the SQL rather than the rows it returns. Two things
per statement:

- the embedding table's `graph_id` is compared with `COALESCE` on **both** sides.
  The left side because `graph_id` arrives by `ADD COLUMN` and is nullable; the
  right because an empty host variable binds as SQL `NULL` in IRIS and
  `graph_id = ''` then matches nothing (FR-005).
- the `rdf_labels` join carries the same predicate. A label row belonging to
  another graph's copy of the node ID must not satisfy the join, or the label
  filter itself becomes the leak (FR-035).

The generated statement, not the procedure body, is what the fallback path runs,
which is why this is a unit test and not only an e2e one.
"""

import pytest
from unittest.mock import MagicMock

from iris_vector_graph.engine import IRISGraphEngine
from tests.unit.route_fakes_227 import teach_registry_route

GRAPH = "ivg227-knn-A"

#: The predicate the engine must emit, modulo whitespace. Written out rather than
#: imported from `routing` so a change to the helper cannot silently change what
#: this test demands.
COALESCED = "COALESCE"


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.description = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    # The pair has to be routed for there to be a statement to inspect: an unrouted
    # pair reads nothing, and "no statement" would pass the shape assertions below
    # without ever generating the SQL they are about.
    teach_registry_route(cursor, GRAPH)
    # Construction issues a `WHERE 1=0` probe to find out whether the build has
    # native VECTOR_COSINE. It contains VECTOR_COSINE and would otherwise be
    # counted as an unscoped read; it scores nothing and returns nothing, so it is
    # dropped here rather than excluded by pattern downstream.
    cursor.execute.reset_mock()
    return engine, cursor


def _statements(cursor):
    """Every SQL string the engine handed the cursor, whitespace-normalised."""
    return [" ".join(str(c.args[0]).split()) for c in cursor.execute.call_args_list if c.args]


def _knn_statements(cursor):
    """Only the statements that actually score vectors.

    `kg_KNN_VEC` also issues a lookup to expand an exclude-ID into a vector; that
    statement is asserted separately, because it reads one row by key rather than
    ranking a table.
    """
    return [s for s in _statements(cursor) if "VECTOR_COSINE" in s]


def _emb_predicate_present(sql: str) -> bool:
    """Is the embedding alias's graph_id compared, with COALESCE on both sides?"""
    import re

    return bool(
        re.search(
            r"COALESCE\(\s*n\.graph_id\s*,\s*''\s*\)\s*=\s*COALESCE\(\s*\?\s*,\s*''\s*\)",
            sql,
            re.IGNORECASE,
        )
    )


def _label_predicate_present(sql: str) -> bool:
    import re

    return bool(
        re.search(
            r"COALESCE\(\s*L\.graph_id\s*,\s*''\s*\)\s*=\s*COALESCE\(\s*\?\s*,\s*''\s*\)",
            sql,
            re.IGNORECASE,
        )
    )


# --- the four server-side variants ---------------------------------------------

VARIANTS = [
    # (name, kwargs, the fetchone the exclude-ID lookup needs)
    ("plain", {}, None),
    ("label_filter", {"label_filter": "Patient"}, None),
    ("exclude_id", {}, "0.1,0.2,0.3,0.4"),
    ("label_and_exclude", {"label_filter": "Patient"}, "0.1,0.2,0.3,0.4"),
]


@pytest.mark.parametrize("name,kwargs,stored_vec", VARIANTS)
def test_every_variant_scopes_the_embedding_table(name, kwargs, stored_vec):
    eng, cursor = _engine()
    if stored_vec is not None:
        cursor.fetchone.return_value = (stored_vec,)
        query = "node:seed"
    else:
        query = "[0.1,0.2,0.3,0.4]"

    eng.kg_KNN_VEC(query, k=5, graph=GRAPH, **kwargs)

    scoring = _knn_statements(cursor)
    assert scoring, f"variant {name} issued no scoring statement"
    for sql in scoring:
        assert _emb_predicate_present(sql), f"variant {name} is unscoped: {sql}"


@pytest.mark.parametrize("name,kwargs,stored_vec", VARIANTS)
def test_every_variant_binds_the_graph(name, kwargs, stored_vec):
    """The graph is a parameter, never interpolated — it is caller-supplied (FR-032)."""
    eng, cursor = _engine()
    if stored_vec is not None:
        cursor.fetchone.return_value = (stored_vec,)
        query = "node:seed"
    else:
        query = "[0.1,0.2,0.3,0.4]"

    eng.kg_KNN_VEC(query, k=5, graph=GRAPH, **kwargs)

    scoring_calls = [
        c for c in cursor.execute.call_args_list
        if c.args and "VECTOR_COSINE" in str(c.args[0])
    ]
    for c in scoring_calls:
        params = c.args[1] if len(c.args) > 1 else []
        assert GRAPH in list(params), (
            f"variant {name} did not bind the graph; params={params}. An "
            "interpolated graph ID would be injectable and would defeat the "
            "COALESCE on the right-hand side."
        )
        assert GRAPH not in str(c.args[0]), (
            f"variant {name} interpolated the graph into the SQL text"
        )


@pytest.mark.parametrize(
    "name,kwargs,stored_vec",
    [v for v in VARIANTS if v[1].get("label_filter")],
)
def test_the_label_join_carries_the_same_predicate(name, kwargs, stored_vec):
    """Otherwise the label filter is the leak, not the fix (FR-035)."""
    eng, cursor = _engine()
    if stored_vec is not None:
        cursor.fetchone.return_value = (stored_vec,)
        query = "node:seed"
    else:
        query = "[0.1,0.2,0.3,0.4]"

    eng.kg_KNN_VEC(query, k=5, graph=GRAPH, **kwargs)

    for sql in _knn_statements(cursor):
        assert "rdf_labels" in sql, sql
        assert _label_predicate_present(sql), (
            f"variant {name} joins rdf_labels unscoped: {sql}"
        )


def test_the_exclude_id_lookup_is_scoped_too():
    """`kg_KNN_VEC("node:1")` reads that node's stored vector first.

    Unscoped, it reads whichever graph's row IRIS returns first and then searches
    graph A with graph B's vector — a wrong answer with no error anywhere.
    """
    eng, cursor = _engine()
    cursor.fetchone.return_value = ("0.1,0.2,0.3,0.4",)

    eng.kg_KNN_VEC("node:seed", k=5, graph=GRAPH)

    lookups = [
        s for s in _statements(cursor)
        if "SELECT emb" in s.upper().replace("SELECT EMB", "SELECT emb")
        or ("emb FROM" in s and "VECTOR_COSINE" not in s)
    ]
    assert lookups, f"no seed-vector lookup was issued: {_statements(cursor)}"
    for sql in lookups:
        assert COALESCED in sql, f"the seed-vector lookup is unscoped: {sql}"


# --- the client-side fallback ---------------------------------------------------


@pytest.mark.parametrize("label_filter", [None, "Patient"])
def test_the_client_side_fallback_is_scoped(label_filter):
    """The fallback runs when the server-side path raises.

    It is the path a partially-upgraded install actually uses, so an unscoped
    fallback means the upgrade window is the leak.
    """
    eng, cursor = _engine()
    captured = {}

    def fake_exec(sql, params):
        captured["sql"] = " ".join(str(sql).split())
        captured["params"] = params
        return []

    import iris_vector_graph.embedded as embedded

    original = getattr(embedded, "_sql_statement_execute", None)
    embedded._sql_statement_execute = fake_exec
    try:
        eng._kg_KNN_VEC_python_optimized(
            "[0.1,0.2,0.3,0.4]", k=5, label_filter=label_filter, graph=GRAPH
        )
    finally:
        if original is not None:
            embedded._sql_statement_execute = original

    assert captured, "the fallback issued no statement"
    assert _emb_predicate_present(captured["sql"]), captured["sql"]
    assert GRAPH in list(captured["params"]), captured["params"]
    if label_filter:
        assert _label_predicate_present(captured["sql"]), captured["sql"]


def test_an_omitted_graph_means_the_default_graph_not_every_graph():
    """`graph=None` must still emit the predicate, bound to `''` (FR-004).

    Dropping the predicate when no graph is named is the exact bug 227 exists to
    fix: the default-graph caller is the one who gets the cross-graph scan.
    """
    eng, cursor = _engine()

    eng.kg_KNN_VEC("[0.1,0.2,0.3,0.4]", k=5)

    scoring = _knn_statements(cursor)
    assert scoring
    for sql in scoring:
        assert _emb_predicate_present(sql), (
            f"an omitted graph produced an unscoped scan: {sql}"
        )
    for c in cursor.execute.call_args_list:
        if c.args and "VECTOR_COSINE" in str(c.args[0]):
            params = list(c.args[1]) if len(c.args) > 1 else []
            assert "" in params, f"the default graph was not bound: {params}"
