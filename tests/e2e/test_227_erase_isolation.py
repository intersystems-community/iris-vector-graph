"""Spec 227 US3 gate — erasing one graph leaves the other one whole (FR-017).

Before the re-key, a node ID belonged to exactly one graph, so reaching a graph's
children through `WHERE s IN (SELECT node_id FROM nodes WHERE graph_id = A)` was a
correct way to find them. After it, that subquery returns the *shared* node ID and
the DELETE takes both graphs' rows with it. Erase is the worst place for this bug
to live: it reports a count that reads like success, and the evidence it destroyed
is the only thing that would have shown the mistake.

`tests/unit/test_227_erase_plan.py` pins the shape of the statements in
`Graph.KG.Eraser`. This file is the live gate, because the guarantee is in what
IRIS actually removes — three scenarios from spec US3:

1. `node:1` gone from A, still in B with its original vector.
2. A's registry rows gone, B's row untouched — `set_at` included, since a rewritten
   identity row would silently re-date the model that wrote B's vectors.
3. The whole-namespace erase empties every route and the registry (SC-003).

Scenario 3 erases the namespace, so it runs last and is marked
`requires_clean_isolation`: it is not isolated from anything else in the session by
design — that is what it tests.
"""

import contextlib

import pytest

pytestmark = [pytest.mark.e2e]


def _rows(cursor, sql, params=()):
    """The result rows as a list.

    `list(...)` rather than the driver's own container: `fetchall()` hands back a
    tuple here, and `() == []` is false however empty the registry is, so
    `assert _routes(...) == []` could only ever fail — the mirror of an assertion
    that can only pass.
    """
    cursor.execute(sql, params)
    return list(cursor.fetchall() or [])


def _routes(cursor, graph=None):
    """`(table_name, graph_id, model_key, set_at)` for routed tables only.

    `%STARTSWITH` rather than `LIKE 'kg_emb_%'`: `_` is a LIKE wildcard, so the
    pattern would also match a legacy name and turn this read into a claim about
    `kg_NodeEmbeddings`.
    """
    sql = (
        "SELECT table_name, graph_id, model_key, set_at "
        "FROM Graph_KG.embedding_registry "
        "WHERE table_name %STARTSWITH 'kg_emb_'"
    )
    params = ()
    if graph is not None:
        sql += " AND COALESCE(graph_id, '') = COALESCE(?, '')"
        params = (graph,)
    return _rows(cursor, sql, params)


def _table_exists(cursor, table) -> bool:
    try:
        cursor.execute(f"SELECT COUNT(*) FROM Graph_KG.{table}")
        cursor.fetchone()
        return True
    except Exception:
        return False


def _count(cursor, table, graph=None) -> int:
    sql = f"SELECT COUNT(*) FROM Graph_KG.{table}"
    params = ()
    if graph is not None:
        sql += " WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
        params = (graph,)
    cursor.execute(sql, params)
    return cursor.fetchone()[0]


@pytest.fixture
def populated(ivg227_env):
    """Both graphs holding the shared node ID, each with its own model and width."""
    env = ivg227_env
    env.populate()
    cursor = env.conn.cursor()
    try:
        yield env, cursor
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


# --- scenario 1: the neighbour's node and vector survive ---------------------------


def test_erasing_a_leaves_bs_node_and_vector(populated):
    env, cursor = populated
    before = env.engine.kg_KNN_VEC(
        env.as_query(env.vec_b()), k=5, graph=env.graph_b, model_key=env.model_b
    )
    assert any(r[0] == env.node_id for r in before), (
        f"graph B could not read its own vector before the erase: {before}"
    )

    env.engine.erase_graph(graph=env.graph_a)

    assert _count(cursor, "nodes", env.graph_a) == 0, "graph A's node survived its erase"
    assert _count(cursor, "nodes", env.graph_b) == 1, (
        "erasing graph A removed graph B's node — the shared node ID was resolved "
        "without its graph"
    )

    after = env.engine.kg_KNN_VEC(
        env.as_query(env.vec_b()), k=5, graph=env.graph_b, model_key=env.model_b
    )
    assert any(r[0] == env.node_id for r in after), (
        f"graph B's vector went with graph A's erase: {after}"
    )


def test_erasing_a_leaves_bs_labels_and_props(populated):
    """`rdf_labels` and `rdf_props` carry their own `graph_id` now (T016).

    Deleting them through `nodes` is the same crossing as the vector case, one table
    further down, and it is the one an operator notices last: the node is still
    there, it has just quietly stopped being a `Member`.
    """
    env, cursor = populated
    env.engine.erase_graph(graph=env.graph_a)

    assert _count(cursor, "rdf_labels", env.graph_a) == 0, (
        "graph A's labels survived its erase"
    )
    assert _count(cursor, "rdf_labels", env.graph_b) >= 1, (
        "erasing graph A took graph B's labels — the child delete resolved the "
        "shared node ID through nodes instead of using its own graph_id"
    )


def test_erasing_a_removes_as_vector_from_its_own_route(populated):
    """The erase has to actually work, not merely be harmless to B."""
    env, cursor = populated
    routes_a = _routes(cursor, env.graph_a)
    assert routes_a, "graph A never got a routed table, so nothing here is being tested"
    tables = [r[0] for r in routes_a]

    env.engine.erase_graph(graph=env.graph_a)

    for table in tables:
        if _table_exists(cursor, table):
            assert _count(cursor, table) == 0, (
                f"graph A's route {table} still holds rows after its erase"
            )


# --- scenario 2: the registry -----------------------------------------------------


def test_as_registry_rows_go_and_bs_row_is_untouched(populated):
    env, cursor = populated
    before_b = _routes(cursor, env.graph_b)
    assert before_b, "graph B never got a routed table, so nothing here is being tested"

    env.engine.erase_graph(graph=env.graph_a)

    assert _routes(cursor, env.graph_a) == [], (
        "graph A's registry rows outlived its tables; the next resolve returns a "
        "route whose table does not exist"
    )
    after_b = _routes(cursor, env.graph_b)
    assert [tuple(r) for r in after_b] == [tuple(r) for r in before_b], (
        "graph B's registry row changed during graph A's erase. Its set_at is when "
        f"B's model was recorded, and rewriting it re-dates B's vectors: "
        f"{before_b} -> {after_b}"
    )


def test_bs_route_table_still_exists_after_as_erase(populated):
    env, cursor = populated
    tables_b = [r[0] for r in _routes(cursor, env.graph_b)]
    assert tables_b

    env.engine.erase_graph(graph=env.graph_a)

    for table in tables_b:
        assert _table_exists(cursor, table), (
            f"graph B's routed table {table} was dropped by graph A's erase, which "
            f"takes B's HNSW index with it"
        )


def test_the_legacy_table_survives_a_default_graph_erase(populated):
    """`kg_NodeEmbeddings` is named statically by `kg_KNN_VEC` and by the bulk
    templates. Its rows go; the table must not."""
    env, cursor = populated
    env.engine.erase_graph(graph="")

    assert _table_exists(cursor, "kg_NodeEmbeddings"), (
        "erasing the default graph dropped kg_NodeEmbeddings, so the schema went "
        "with the content and kg_KNN_VEC now fails with SQLCODE -30"
    )


# --- scenario 3: the whole namespace ----------------------------------------------


@pytest.mark.requires_clean_isolation
def test_erase_all_empties_every_route_and_the_registry(populated):
    """SC-003. Runs last on purpose: it erases the namespace.

    A routed table left behind here is worse than a leftover row — the inventory
    reports a route for a namespace with no content, and a later `create=True` for
    the same pair adopts the orphan at whatever width it was declared with.
    """
    env, cursor = populated
    tables = [r[0] for r in _routes(cursor)]
    assert tables, "no routed tables exist, so nothing here is being tested"

    env.engine.erase_all()

    assert _routes(cursor) == [], (
        "EraseAll left registry rows describing routed tables it removed"
    )
    surviving = [t for t in tables if _table_exists(cursor, t)]
    assert surviving == [], f"EraseAll left routed tables behind: {surviving}"
    assert _count(cursor, "nodes") == 0
    assert _count(cursor, "kg_NodeEmbeddings") == 0
