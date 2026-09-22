"""Spec 227 US1 gate — one node ID in two graphs, and four read paths.

The re-key to `UNIQUE (graph_id, node_id)` is what makes this file possible: before
4.0.0 a node ID belonged to exactly one graph, so none of these reads *could* be
wrong. After it, every read that resolves a node by ID alone returns the union of
both graphs and is wrong in the quietest possible way — it answers.

Four paths, one node ID, one gate (FR-010, FR-035, SC-011):

- labels
- properties
- edges
- vectors

The label case is the sharpest: graph A's node is a `Patient`, graph B's is a
`Member`. An unscoped label read reports a node that is both, and then a label
filter on `Member` returns graph A's row.

Reads go through `execute_cypher("USE GRAPH '<g>' ...")` rather than a scoped
accessor because that is the interface the engine actually offers: `get_node`,
`get_nodes_by_label` and `get_node_properties` take no graph, and `USE GRAPH` is
the one scoping the translator honours (tests/unit/test_227_cypher_child_scope.py
covers its generated SQL; this file covers what IRIS does with it).
"""

import pytest

pytestmark = [pytest.mark.e2e]

OTHER_A = "ivg227:iso:a:2"
OTHER_B = "ivg227:iso:b:2"


def _rows(engine, cypher: str):
    """The row tuples of an `execute_cypher` result, read off `IVGResult` by name.

    `IVGResult` is a pydantic model — not a mapping, and not a sequence of rows. The
    earlier "whatever shape it returns" version of this helper did `list(result)`,
    which iterates the model's *fields* and yields `('columns', [...])`,
    `('rows', [])`, `('sql', 'SELECT ...')` pairs. An empty result then looked
    non-empty, and the generated SQL text landed among the values `_flat` collects,
    which is how `test_labels_do_not_union_across_graphs` passed on the word
    `Patient` appearing inside its own query rather than in any row.

    There is no dict-shaped fallback to keep either: `IVGResult.get()` raises
    `TypeError`. The shape is known, so this reads it, and a failed statement is an
    error rather than an empty answer — `rows == []` from a broken query is exactly
    the outcome every test in this file is trying to tell apart from isolation.
    """
    result = engine.execute_cypher(cypher)
    error = getattr(result, "error", None)
    assert error is None, f"query failed, so it proves nothing about scope: {error}\n{cypher}"
    return list(getattr(result, "rows", None) or [])


def _flat(rows) -> set:
    """Every scalar in the result, as strings — the tests care that a value is
    present or absent, not which column carried it."""
    out = set()
    for row in rows:
        cells = row.values() if isinstance(row, dict) else (row if isinstance(row, (list, tuple)) else [row])
        for cell in cells:
            if cell is None:
                continue
            if isinstance(cell, (list, tuple)):
                out.update(str(c) for c in cell if c is not None)
            else:
                out.add(str(cell))
    return out


@pytest.fixture
def two_graphs(ivg227_env):
    """The same node ID in A and B, with different labels, props, edges, vectors."""
    env = ivg227_env
    eng = env.engine

    eng.create_node(env.node_id, labels=["Patient"], properties={"side": "a"}, graph=env.graph_a)
    eng.create_node(env.node_id, labels=["Member"], properties={"side": "b"}, graph=env.graph_b)

    eng.create_node(OTHER_A, labels=["Patient"], graph=env.graph_a)
    eng.create_node(OTHER_B, labels=["Member"], graph=env.graph_b)
    eng.create_edge(env.node_id, "seen_by", OTHER_A, graph=env.graph_a)
    eng.create_edge(env.node_id, "seen_by", OTHER_B, graph=env.graph_b)

    # Same width in both graphs here: this file is about scope, and a width
    # difference would make a failure ambiguous between the two. The width case is
    # tests/e2e/test_227_graph_scoped_knn.py.
    eng.store_embedding(env.node_id, env.vec_a(0.1), graph=env.graph_a, model_key=env.model_a)
    eng.store_embedding(env.node_id, env.vec_a(0.9), graph=env.graph_b, model_key=env.model_a)

    yield env

    cursor = env.conn.cursor()
    try:
        for node_id in (OTHER_A, OTHER_B):
            for sql in (
                "DELETE FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?",
                "DELETE FROM Graph_KG.rdf_props WHERE s = ?",
                "DELETE FROM Graph_KG.rdf_labels WHERE s = ?",
                "DELETE FROM Graph_KG.nodes WHERE node_id = ?",
            ):
                try:
                    args = (node_id, node_id) if "o_id = ?" in sql else (node_id,)
                    cursor.execute(sql, args)
                except Exception:
                    pass
        try:
            env.conn.commit()
        except Exception:
            pass
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def test_labels_do_not_union_across_graphs(two_graphs):
    """`Patient` in A, `Member` in B, and neither read sees both."""
    env = two_graphs
    eng = env.engine

    in_a = _flat(_rows(
        eng,
        f"USE GRAPH '{env.graph_a}' MATCH (n {{node_id: '{env.node_id}'}}) RETURN labels(n)",
    ))
    in_b = _flat(_rows(
        eng,
        f"USE GRAPH '{env.graph_b}' MATCH (n {{node_id: '{env.node_id}'}}) RETURN labels(n)",
    ))

    assert any("Patient" in v for v in in_a), f"graph A lost its own label: {in_a}"
    assert not any("Member" in v for v in in_a), (
        f"graph A sees graph B's label, so a node is reported as being two things "
        f"at once: {in_a}"
    )
    assert any("Member" in v for v in in_b), f"graph B lost its own label: {in_b}"
    assert not any("Patient" in v for v in in_b), f"graph B sees graph A's label: {in_b}"


def test_a_label_match_for_the_other_graphs_label_returns_nothing(two_graphs):
    """The union failure with a consequence attached: a pattern that reads as
    narrowing is actually crossing."""
    env = two_graphs
    eng = env.engine

    rows = _rows(
        eng,
        f"USE GRAPH '{env.graph_a}' MATCH (n:Member) WHERE n.node_id = "
        f"'{env.node_id}' RETURN n.node_id",
    )

    assert not rows, f"graph A returned rows for a label only graph B holds: {rows}"


def test_a_knn_label_filter_does_not_reach_the_other_graphs_label(two_graphs):
    """FR-035: the label filter inside kg_KNN_VEC is joined to `rdf_labels`, which
    now carries its own graph_id — an unscoped join makes the filter a crossing."""
    env = two_graphs
    eng = env.engine

    rows = eng.kg_KNN_VEC(
        env.as_query(env.vec_a(0.1)), k=10, label_filter="Member", graph=env.graph_a
    )

    assert not rows, f"graph A returned rows for a label only graph B holds: {rows}"


def test_properties_do_not_cross(two_graphs):
    env = two_graphs
    eng = env.engine

    in_a = _flat(_rows(
        eng,
        f"USE GRAPH '{env.graph_a}' MATCH (n {{node_id: '{env.node_id}'}}) RETURN n.side",
    ))
    in_b = _flat(_rows(
        eng,
        f"USE GRAPH '{env.graph_b}' MATCH (n {{node_id: '{env.node_id}'}}) RETURN n.side",
    ))

    assert in_a == {"a"}, f"graph A read a value it does not hold: {in_a}"
    assert in_b == {"b"}, f"graph B read a value it does not hold: {in_b}"


def test_edges_do_not_cross(two_graphs):
    env = two_graphs
    eng = env.engine

    targets_a = _flat(_rows(
        eng,
        f"USE GRAPH '{env.graph_a}' MATCH (n {{node_id: '{env.node_id}'}})-[:seen_by]->(m) "
        f"RETURN m.node_id",
    ))
    targets_b = _flat(_rows(
        eng,
        f"USE GRAPH '{env.graph_b}' MATCH (n {{node_id: '{env.node_id}'}})-[:seen_by]->(m) "
        f"RETURN m.node_id",
    ))

    assert OTHER_A in targets_a, f"graph A lost its edge: {targets_a}"
    assert OTHER_B not in targets_a, f"graph A traversed into graph B: {targets_a}"
    assert OTHER_B in targets_b, f"graph B lost its edge: {targets_b}"
    assert OTHER_A not in targets_b, f"graph B traversed into graph A: {targets_b}"


def _routes_for(cursor, graph: str):
    """`(table_name, dimension)` for every route registered to `graph`."""
    cursor.execute(
        "SELECT table_name, dimension FROM Graph_KG.embedding_registry "
        "WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
        (graph,),
    )
    return [(row[0], row[1]) for row in cursor.fetchall()]


def test_each_graph_reads_back_its_own_vector(two_graphs):
    """One node ID, two vectors, and each one in the table its own `(graph, model)`
    routes to.

    Not one table with two rows: per-model routing (FR-017) writes each pair to its
    own `kg_emb_<hash>` table, so counting `kg_NodeEmbeddings` — which 4.0.0 no
    longer writes — reads 0 and says the re-key failed when it did not. Both graphs
    use the same model here, so the two routes differ only by graph, which is the
    thing under test.
    """
    env = two_graphs
    cursor = env.conn.cursor()
    try:
        routed = {}
        for graph in (env.graph_a, env.graph_b):
            routes = _routes_for(cursor, graph)
            assert len(routes) == 1, (
                f"graph {graph} should route its one model to one table, got {routes}"
            )
            routed[graph] = routes[0][0]

        assert routed[env.graph_a] != routed[env.graph_b], (
            "both graphs routed to the same physical table, so the same node ID "
            f"cannot hold a vector in each: {routed}"
        )

        for graph, table in routed.items():
            cursor.execute(
                f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE node_id = ? "
                "AND COALESCE(graph_id, '') = COALESCE(?, '')",
                (env.node_id, graph),
            )
            assert cursor.fetchone()[0] == 1, (
                f"graph {graph} does not hold exactly one vector for {env.node_id} "
                f"in {table}"
            )
            # The routed table is scoped, so nothing in it may belong elsewhere.
            cursor.execute(
                f"SELECT COUNT(*) FROM Graph_KG.{table} "
                "WHERE COALESCE(graph_id, '') <> COALESCE(?, '')",
                (graph,),
            )
            assert cursor.fetchone()[0] == 0, (
                f"{table} holds rows for a graph other than {graph}"
            )

        # A routed write must not also land in the 3.2.0 table: a reader still on the
        # unscoped table would find a graph-blind row there and answer from it.
        cursor.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'kg_NodeEmbeddings' "
            "AND COLUMN_NAME IN ('node_id', 'id')"
        )
        legacy_keys = [row[0] for row in cursor.fetchall()]
        assert legacy_keys, (
            "Graph_KG.kg_NodeEmbeddings has neither node_id nor id, so this check "
            "cannot tell a clean legacy table from a missing one"
        )
        for key in legacy_keys:
            cursor.execute(
                f"SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE {key} = ?",
                (env.node_id,),
            )
            assert cursor.fetchone()[0] == 0, (
                f"the routed write also wrote {env.node_id} into the unscoped "
                f"kg_NodeEmbeddings ({key})"
            )
    finally:
        try:
            cursor.close()
        except Exception:
            pass
