"""Spec 227 US2 gate — one node ID, two graphs, two models, two widths.

This is the file that decides whether routing is real, because nothing in Python
can fake it: IRIS enforces a declared VECTOR width at INSERT (SQLCODE -104), so
two widths under one node ID can only work if they are in two physical tables
with two declarations. A mock cannot be wrong about that in the same way.

Three scenarios (SC-002, SC-005, ADR-0005):

1. A 768-wide write into graph A, whose route is declared at 384, is refused —
   and graph B's 768-wide writes keep working. The refusal is per route.
2. Each graph reads back its own vector at its own width. Not "a vector for that
   ID": the same ID holds two, and before the re-key to
   `UNIQUE (graph_id, node_id)` it could only hold one.
3. A graph-A-width query vector against graph B errors rather than scoring a
   reshaped value. A padded or truncated vector still produces a number, and a
   number is what a caller will use.

`store_embedding` is the write path rather than direct SQL because the route has
to be created by the write (FR-013) — a test that created the tables itself would
pass against an engine that never routes anything.
"""

import contextlib

import pytest

pytestmark = [pytest.mark.e2e]


def _registry_rows(env, graph):
    """Every registry row recorded for one graph: (table_name, model_key, dimension)."""
    cursor = env.conn.cursor()
    try:
        cursor.execute(
            "SELECT table_name, model_key, dimension FROM Graph_KG.embedding_registry "
            "WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
            (graph,),
        )
        return [tuple(r) for r in cursor.fetchall()]
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _declared_width(env, table_name):
    """The width IRIS declares for `table_name.emb`, read from the dictionary.

    The column declaration is the truth about width (FR-006/FR-016), so the
    assertion is against the dictionary and not against the registry row that the
    same code path wrote.
    """
    from iris_vector_graph.schema import GraphSchema

    cursor = env.conn.cursor()
    try:
        return GraphSchema.get_embedding_dimension(cursor, f"Graph_KG.{table_name}")
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


@pytest.fixture
def two_models(ivg227_env):
    env = ivg227_env
    env.wipe()
    env.create_nodes()
    env.store_vectors()
    yield env
    env.wipe()


def test_each_graph_gets_its_own_route(two_models):
    env = two_models

    route_a = env.engine.resolve_route(graph=env.graph_a, model_key=env.model_a)
    route_b = env.engine.resolve_route(graph=env.graph_b, model_key=env.model_b)

    assert route_a is not None, "graph A's write did not create a route"
    assert route_b is not None, "graph B's write did not create a route"
    assert route_a.table_name != route_b.table_name, (
        "both graphs routed to one table, which cannot hold two widths (FR-037)"
    )


def test_each_route_declares_its_own_width(two_models):
    """FR-016: the declaration, not the registry row, is what IRIS enforces."""
    env = two_models

    route_a = env.engine.resolve_route(graph=env.graph_a, model_key=env.model_a)
    route_b = env.engine.resolve_route(graph=env.graph_b, model_key=env.model_b)

    assert _declared_width(env, route_a.table_name) == env.dim_a
    assert _declared_width(env, route_b.table_name) == env.dim_b
    assert route_a.dimension == env.dim_a
    assert route_b.dimension == env.dim_b


def test_the_registry_records_one_row_per_route(two_models):
    env = two_models

    rows_a = _registry_rows(env, env.graph_a)
    rows_b = _registry_rows(env, env.graph_b)

    assert len(rows_a) == 1, rows_a
    assert len(rows_b) == 1, rows_b
    assert rows_a[0][1] == env.model_a
    assert rows_b[0][1] == env.model_b
    assert rows_a[0][2] == env.dim_a
    assert rows_b[0][2] == env.dim_b


def test_a_wrong_width_write_into_graph_a_is_refused(two_models):
    """Scenario 1. Graph A's route is 384; a 768-wide vector for the same node ID
    is a different model's output and must not be reshaped into it."""
    env = two_models

    with pytest.raises(Exception) as excinfo:
        env.engine.store_embedding(
            env.node_id, env.vec_b(0.5), graph=env.graph_a, model_key=env.model_a
        )

    message = str(excinfo.value)
    assert str(env.dim_a) in message or str(env.dim_b) in message, (
        f"the refusal does not say which widths disagreed: {message}"
    )


def test_graph_b_still_accepts_its_own_width_after_graph_a_refused(two_models):
    """The refusal is per route. A shared `self.embedding_dimension` mutated by
    graph A's inference would make this the second failure."""
    env = two_models

    with contextlib.suppress(Exception):
        env.engine.store_embedding(
            env.node_id, env.vec_b(0.5), graph=env.graph_a, model_key=env.model_a
        )

    assert env.engine.store_embedding(
        env.node_id, env.vec_b(0.7), graph=env.graph_b, model_key=env.model_b
    )


def test_each_graph_reads_back_its_own_vector(two_models):
    """Scenario 2. Two vectors, one node ID, and each read gets the right one."""
    env = two_models

    route_a = env.engine.resolve_route(graph=env.graph_a, model_key=env.model_a)
    route_b = env.engine.resolve_route(graph=env.graph_b, model_key=env.model_b)

    cursor = env.conn.cursor()
    try:
        for route, width in ((route_a, env.dim_a), (route_b, env.dim_b)):
            cursor.execute(
                f"SELECT COUNT(*) FROM Graph_KG.{route.table_name} WHERE node_id = ?",
                (env.node_id,),
            )
            assert cursor.fetchone()[0] == 1, (
                f"{route.table_name} does not hold exactly one row for {env.node_id}"
            )
            cursor.execute(
                f"SELECT VECTOR_COSINE(emb, TO_VECTOR(?, DOUBLE)) "
                f"FROM Graph_KG.{route.table_name} WHERE node_id = ?",
                (env.as_query([0.1] * width), env.node_id),
            )
            score = cursor.fetchone()[0]
            assert score is not None, "the stored vector did not score against its own width"
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def test_a_graph_a_width_query_against_graph_b_errors(two_models):
    """Scenario 3, ADR-0005. A 384-wide query against a 768-wide route must fail,
    not silently pad, truncate, or return a plausible-looking score."""
    env = two_models

    with pytest.raises(Exception):
        env.engine.kg_KNN_VEC(
            env.as_query(env.vec_a(0.1)),
            k=5,
            graph=env.graph_b,
            model_key=env.model_b,
        )


def test_a_knn_in_each_graph_returns_only_its_own_route(two_models):
    env = two_models

    hits_a = env.engine.kg_KNN_VEC(
        env.as_query(env.vec_a(0.1)), k=5, graph=env.graph_a, model_key=env.model_a
    )
    hits_b = env.engine.kg_KNN_VEC(
        env.as_query(env.vec_b(0.2)), k=5, graph=env.graph_b, model_key=env.model_b
    )

    assert hits_a, "graph A's own vector did not come back from its own route"
    assert hits_b, "graph B's own vector did not come back from its own route"
    # One row each: the same node ID, from two different tables.
    assert len(hits_a) == 1, hits_a
    assert len(hits_b) == 1, hits_b


def test_an_unrouted_pair_reads_empty_and_creates_nothing(two_models):
    """FR-013's read half, live: a model nobody embedded under is not an error and
    not a fallback to another model's vectors."""
    env = two_models
    before = _registry_rows(env, env.graph_a)

    hits = env.engine.kg_KNN_VEC(
        env.as_query(env.vec_a(0.1)), k=5, graph=env.graph_a, model_key="ivg227-no-such-model"
    )

    assert hits == []
    assert _registry_rows(env, env.graph_a) == before
