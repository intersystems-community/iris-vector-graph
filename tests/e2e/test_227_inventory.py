"""Spec 227 US4 gate — the inventory matches the namespace (SC-008, FR-020, FR-038).

Routing turns one embedding table into one per `(graph, model)`. An operator who
cannot list them cannot tell a graph whose vectors are in another route from a graph
that has none, and those two states need opposite actions. This is the report that
answers it, and the reason it has to run live: every column in it is a claim about
what IRIS holds — a declared width, a row count, an index in the class dictionary —
and a mock can be made to agree with any of them.

`tests/unit/test_227_inventory_honesty.py` (T053) pins the rule that decides
`index_state`. This file checks the report against the database it describes, over the
three scenarios in spec US4:

1. One row per `(graph, model)` naming its table, width, dtype and row count.
2. A route with no ANN index reports no index, plus the recorded reason.
3. A graph with no embeddings appears with `table_name=None` rather than vanishing.
"""

import contextlib

import pytest

pytestmark = [pytest.mark.e2e]

#: A graph with nodes and deliberately no vectors (scenario 3). Named distinctly from
#: `Ivg227Env`'s two graphs because `env.wipe()` does not know about it.
QUIET_GRAPH = "ivg227-graph-quiet"
QUIET_NODE = "ivg227:quiet:1"


def _rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchall()


def _hnsw_index_names(cursor, table: str) -> list:
    """What the class dictionary holds for `table`, read the way the report must.

    Deliberately the same call the implementation uses: the assertion worth making is
    that the report and the dictionary agree, and re-deriving the answer here from a
    second query would test two readings of IRIS against each other instead.
    """
    from iris_vector_graph.schema import GraphSchema

    return [name for name, _properties in GraphSchema.hnsw_indexes(cursor, table)]


@pytest.fixture
def inventoried(ivg227_env):
    """Both graphs populated, plus a third graph holding a node and no vector."""
    env = ivg227_env
    env.populate()
    env.engine.create_node(QUIET_NODE, labels=["Quiet"], graph=QUIET_GRAPH)
    cursor = env.conn.cursor()
    try:
        yield env, cursor
    finally:
        with contextlib.suppress(Exception):
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_labels WHERE COALESCE(graph_id, '') = ?",
                (QUIET_GRAPH,),
            )
        with contextlib.suppress(Exception):
            cursor.execute(
                "DELETE FROM Graph_KG.nodes WHERE COALESCE(graph_id, '') = ?",
                (QUIET_GRAPH,),
            )
        with contextlib.suppress(Exception):
            env.conn.commit()
        with contextlib.suppress(Exception):
            cursor.close()


def _for_graph(inventory, graph):
    return [row for row in inventory if (row.graph_id or "") == graph]


# --- scenario 1: one row per (graph, model) ----------------------------------------


def test_each_graph_and_model_has_one_row_naming_its_table(inventoried):
    env, _cursor = inventoried
    inventory = env.engine.embedding_inventory()

    a = _for_graph(inventory, env.graph_a)
    b = _for_graph(inventory, env.graph_b)
    assert len(a) == 1, f"graph A has {len(a)} rows, expected one per model: {a}"
    assert len(b) == 1, f"graph B has {len(b)} rows, expected one per model: {b}"

    assert a[0].model_key == env.model_a
    assert b[0].model_key == env.model_b
    assert a[0].table_name and b[0].table_name
    assert a[0].table_name != b[0].table_name, (
        "both graphs route to one table, which cannot hold two declared widths "
        f"(IRIS refuses the second INSERT with SQLCODE -104): {a[0].table_name}"
    )


def test_the_reported_width_is_the_width_the_column_declares(inventoried):
    """The registry records a width; the column enforces one. They have to agree.

    A recorded width that has gone stale against its column is the FR-031 failure:
    every write at the recorded width is refused by a column that declares another.
    """
    env, cursor = inventoried
    from iris_vector_graph.schema import GraphSchema

    inventory = env.engine.embedding_inventory()
    for graph, expected in ((env.graph_a, env.dim_a), (env.graph_b, env.dim_b)):
        row = _for_graph(inventory, graph)[0]
        assert row.dimension == expected, (
            f"{graph} reports width {row.dimension}, its model writes {expected}"
        )
        declared = GraphSchema.get_embedding_dimension(
            cursor, f"Graph_KG.{row.table_name}"
        )
        assert declared == expected, (
            f"{row.table_name} declares VECTOR(...,{declared}) but the inventory "
            f"reports {row.dimension}"
        )
        assert (row.dtype or "").upper() == "DOUBLE"


def test_the_reported_row_count_is_the_tables_row_count(inventoried):
    env, cursor = inventoried
    inventory = env.engine.embedding_inventory()
    for graph in (env.graph_a, env.graph_b):
        row = _for_graph(inventory, graph)[0]
        actual = _rows(
            cursor,
            f"SELECT COUNT(*) FROM Graph_KG.{row.table_name} "
            "WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
            (graph,),
        )[0][0]
        assert row.row_count == actual, (
            f"{graph} reports {row.row_count} vectors, {row.table_name} holds {actual}"
        )
        assert row.row_count == 1, "the fixture stores exactly one vector per graph"


def test_the_routed_table_count_is_readable_without_sql(inventoried):
    """FR-038: route growth is the thing an operator has to be able to watch."""
    env, cursor = inventoried
    inventory = env.engine.embedding_inventory()
    reported = {row.table_name for row in inventory if row.table_name}
    registry = {
        r[0]
        for r in _rows(
            cursor,
            "SELECT table_name FROM Graph_KG.embedding_registry "
            "WHERE table_name %STARTSWITH 'kg_emb_'",
        )
    }
    assert registry <= reported, (
        f"routes exist that the inventory does not report: {registry - reported}"
    )


# --- scenario 2: the index, and the reason when there is none ----------------------


def test_the_index_report_matches_the_class_dictionary(inventoried):
    """SC-009. Either both say there is an ANN index on that table, or neither does.

    The enterprise image may refuse an HNSW build over these tables, and that is a
    legitimate outcome — a route that scans. What is not legitimate is the report
    disagreeing with the dictionary in either direction.
    """
    env, cursor = inventoried
    for row in env.engine.embedding_inventory():
        if not row.table_name:
            continue
        held = _hnsw_index_names(cursor, f"Graph_KG.{row.table_name}")
        if held:
            assert row.index_state == "present", (
                f"{row.table_name} has HNSW index {held} and the inventory reports "
                f"{row.index_state!r}"
            )
            assert row.index_name in held
        else:
            assert row.index_state != "present", (
                f"{row.table_name} has no HNSW index in %Dictionary.CompiledIndex, "
                "and the inventory claims one — this is the synthesized row spec 226 "
                "removed"
            )
            assert row.index_name is None


def test_a_route_with_no_index_says_why_when_the_build_refused_one(inventoried):
    """US4-2: no index reported, and the recorded reason available.

    Skipped by assertion rather than by `pytest.skip` when every route did get an
    index: on a build where HNSW works there is no refusal to report, and demanding
    one would make this test fail on the healthier database.
    """
    env, cursor = inventoried
    for row in env.engine.embedding_inventory():
        if not row.table_name or row.index_state == "present":
            continue
        recorded = _rows(
            cursor,
            "SELECT index_state, index_error FROM Graph_KG.embedding_registry "
            "WHERE table_name = ? AND COALESCE(graph_id, '') = COALESCE(?, '')",
            (row.table_name, row.graph_id),
        )
        if not recorded:
            continue
        state, error = recorded[0][0], recorded[0][1]
        if state == "refused":
            assert row.index_state == "refused", (
                f"{row.table_name}'s refusal was recorded and the inventory reports "
                f"{row.index_state!r} instead"
            )
            assert row.index_error == error, (
                "the reason IRIS gave is the only thing that tells the operator why "
                f"there is no index: recorded {error!r}, reported {row.index_error!r}"
            )
        else:
            assert row.index_state == "absent"


# --- scenario 3: the graph with nothing in it --------------------------------------


def test_a_graph_with_no_embeddings_appears_with_no_route(inventoried):
    """US4-3. Omission makes "elsewhere" and "never written" the same answer."""
    env, _cursor = inventoried
    inventory = env.engine.embedding_inventory()
    quiet = _for_graph(inventory, QUIET_GRAPH)
    assert len(quiet) == 1, (
        f"a graph with a node and no vectors is reported {len(quiet)} times; it must "
        f"appear exactly once, with no route: {inventory}"
    )
    row = quiet[0]
    assert row.table_name is None
    assert row.dimension is None
    assert row.row_count == 0
    assert row.index_state == "absent"
    assert row.index_name is None


def test_the_quiet_graph_gains_a_route_once_it_has_a_vector(inventoried):
    """The same graph, one write later: the row becomes a route rather than a second row."""
    env, _cursor = inventoried
    env.engine.store_embedding(
        QUIET_NODE, env.vec_a(0.3), graph=QUIET_GRAPH, model_key=env.model_a
    )
    try:
        quiet = _for_graph(env.engine.embedding_inventory(), QUIET_GRAPH)
        assert len(quiet) == 1, f"the no-route row outlived the write: {quiet}"
        assert quiet[0].table_name, "the graph has a vector and reports no table"
        assert quiet[0].row_count == 1
        assert quiet[0].dimension == env.dim_a
    finally:
        cursor = env.conn.cursor()
        with contextlib.suppress(Exception):
            env.engine.erase_graph(graph=QUIET_GRAPH)
        with contextlib.suppress(Exception):
            cursor.close()
