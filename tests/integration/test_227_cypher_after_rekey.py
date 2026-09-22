"""Spec 227 T014 — Cypher still works, and still stays inside one graph.

"The re-key" here is the SQL key change on `Graph_KG.nodes`, nothing to do with
the `^KG` globals or with running a migration. 3.2.0 declared
`uq_nodes_nodeid UNIQUE (node_id)`, so a node ID existed in exactly one graph.
4.0.0 declares `pk_nodes_graph PRIMARY KEY (node_id, graph_id)` plus
`uq_nodes_graph_node UNIQUE (graph_id, node_id)`, and every child foreign key
became composite against `(graph_id, node_id)` — `rdf_labels`, `rdf_props`,
`rdf_edges` and both embedding tables. `tests/integration/test_227_rekey.py`
asserts those constraints directly; this file asserts what the re-key now makes
possible, which is one node ID present in two graphs at once. The 3.2.0-to-4.0.0
upgrade path is `tests/e2e/test_227_migration.py`.

That state is what the translator has to survive. `rdf_labels` and `rdf_props`
gained `graph_id` and were re-keyed, so SQL that was correct against the 3.2.0
shape — where one ID meant one node — can silently union two graphs against the
new one. The seeded namespace here holds exactly that: one ID, two graphs, two
different labels, two different property values, two different edge targets.

Scoping is what keeps the two apart, and the translator only applies it under a
`USE GRAPH` prefix (`_apply_graph_scope_to_reads`, added by T029a after T014's
premise — "`cypher/translator.py` is untouched" — turned out to be wrong). So
every assertion below runs a scoped query, the way
`tests/e2e/test_227_read_isolation.py` does. A bare query over this namespace
genuinely names two nodes and is expected to return both; that is asserted once, at the
bottom of this file, so the difference is recorded rather than assumed.

Each scoped read is checked twice, once per graph. A scope that returned nothing
at all would satisfy every "does not see the other graph" assertion on its own.

A missing container is a failure, never a skip (constitution VIII gate 1).
"""

import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.fixture
def two_graph_cypher(iris_connection):
    """One node ID in two graphs, each with its own label, property and edge.

    Returns a callable that runs Cypher and the identifiers it was built from.
    Node IDs are prefixed per run so a leaked row from a previous failure cannot
    make a later assertion pass.
    """
    if SKIP_IRIS_TESTS:
        pytest.fail(
            "spec 227's Cypher gate reads SQL that joins re-keyed tables. "
            "SKIP_IRIS_TESTS=true cannot observe it — start ivg-iris-enterprise "
            "with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection: translated SQL needs a real catalog.")

    import contextlib

    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    pfx = f"IVG227C_{uuid.uuid4().hex[:6]}"
    graph_a = f"{pfx}-A"
    graph_b = f"{pfx}-B"
    shared = f"{pfx}:shared"
    target_a = f"{pfx}:target-a"
    target_b = f"{pfx}:target-b"

    cursor = iris_connection.cursor()

    def wipe():
        for table in ("rdf_edges", "rdf_props", "rdf_labels", "nodes"):
            column = "node_id" if table == "nodes" else "graph_id"
            with contextlib.suppress(Exception):
                if table == "nodes":
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} WHERE {column} LIKE ?",
                        (f"{pfx}%",),
                    )
                else:
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} WHERE {column} LIKE ?",
                        (f"{pfx}%",),
                    )
        with contextlib.suppress(Exception):
            iris_connection.commit()

    wipe()

    # graph A: the shared node is a Patient pointing at target-a
    # graph B: the same ID is a Member pointing at target-b
    for graph, label, target in ((graph_a, "Patient", target_a), (graph_b, "Member", target_b)):
        for node in (shared, target):
            cursor.execute(
                "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
                (node, graph),
            )
            # `n.id` is a stored property to the translator, never `nodes.node_id`:
            # every `.id` predicate it emits reads `rdf_props WHERE "key" = 'id'`.
            # `create_node` writes that row for exactly this reason, so a fixture
            # that inserts rows directly has to write it too — without it every
            # `.id` filter below matches nothing and the assertions pass vacuously.
            cursor.execute(
                'INSERT INTO Graph_KG.rdf_props (graph_id, s, "key", val) '
                "VALUES (?, ?, ?, ?)",
                (graph, node, "id", node),
            )
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)",
            (graph, shared, label),
        )
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)",
            (graph, target, "Target"),
        )
        cursor.execute(
            'INSERT INTO Graph_KG.rdf_props (graph_id, s, "key", val) VALUES (?, ?, ?, ?)',
            (graph, shared, "graph_marker", graph),
        )
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (graph_id, s, p, o_id) VALUES (?, ?, ?, ?)",
            (graph, shared, "TREATS", target),
        )
    iris_connection.commit()

    def run(query, graph=None, params=None):
        """Translate and execute, scoped to `graph` unless asked not to be.

        `USE GRAPH` is the only thing that makes the translator emit a graph
        predicate, so passing a graph is what every assertion here depends on.
        `graph=None` is the deliberately unscoped case and belongs to exactly one
        test.
        """
        if graph is not None:
            query = f"USE GRAPH '{graph}' {query}"
        ast = parse_query(query)
        sql_query = translate_to_sql(ast, params=params)
        stmts = sql_query.sql if isinstance(sql_query.sql, list) else [sql_query.sql]
        all_params = sql_query.parameters
        c = iris_connection.cursor()
        rows = []
        try:
            for i, stmt in enumerate(stmts):
                p = all_params[i] if i < len(all_params) else []
                c.execute(stmt, p)
                if c.description:
                    rows = c.fetchall()
        finally:
            c.close()
        return rows

    yield {
        "run": run,
        "graph_a": graph_a,
        "graph_b": graph_b,
        "shared": shared,
        "target_a": target_a,
        "target_b": target_b,
        "prefix": pfx,
        "conn": iris_connection,
    }

    wipe()
    cursor.close()


# --- label reads ---------------------------------------------------------------


def test_a_label_filtered_match_returns_one_row_per_graph(two_graph_cypher):
    """`MATCH (n:Patient)` in graph A must not also return graph B's Member row.

    Both rows share a node ID; only graph A calls it a Patient. A `rdf_labels`
    join that dropped `graph_id` returns the ID once per matching label row, so
    the count is the assertion — one, not two.
    """
    run, shared = two_graph_cypher["run"], two_graph_cypher["shared"]

    hits = [r[0] for r in run(f"MATCH (n:Patient) WHERE n.id = '{shared}' RETURN n.id", two_graph_cypher["graph_a"])]
    assert hits.count(shared) == 1, (
        f"the shared node ID came back {hits.count(shared)} times in graph A; "
        "1 is the scoped answer, 2 means the label join is unioning two graphs, "
        "0 means the scope matched nothing at all"
    )

    # Graph B calls the same ID a Member, so the same label filter must find
    # nothing there — and its own label must find it.
    # `list()` because the driver hands back an empty tuple, not an empty list.
    assert not list(
        run(f"MATCH (n:Patient) WHERE n.id = '{shared}' RETURN n.id", two_graph_cypher["graph_b"])
    )
    hits_b = [r[0] for r in run(f"MATCH (n:Member) WHERE n.id = '{shared}' RETURN n.id", two_graph_cypher["graph_b"])]
    assert hits_b.count(shared) == 1, hits_b


def test_labels_returns_one_graphs_label_set(two_graph_cypher):
    """`labels(n)` on the shared ID must read `['Patient']`, never both labels.

    The union is the failure: one node in one graph has one label set, and a
    caller cannot tell a two-graph union from a genuinely multi-labelled node.
    """
    run, shared = two_graph_cypher["run"], two_graph_cypher["shared"]

    for graph, mine, theirs in (
        (two_graph_cypher["graph_a"], "Patient", "Member"),
        (two_graph_cypher["graph_b"], "Member", "Patient"),
    ):
        rows = run(f"MATCH (n) WHERE n.id = '{shared}' RETURN labels(n)", graph)
        assert rows, f"the scoped read found no node at all in graph {graph}"
        for row in rows:
            rendered = str(row[0])
            assert mine in rendered, f"graph {graph} lost its own label: {rendered}"
            assert theirs not in rendered, (
                f"labels(n) in graph {graph} returned the other graph's label: {rendered}"
            )


def test_a_label_held_only_in_b_returns_nothing_when_a_is_searched(two_graph_cypher):
    """Complements the count assertion: absence, not just non-duplication.

    `graph_marker` holds a different value per graph, so this asks for a
    combination that exists in neither — graph B's label beside graph A's marker.
    """
    rows = two_graph_cypher["run"](
        f"MATCH (n:Member) WHERE n.id = '{two_graph_cypher['shared']}' "
        f"AND n.graph_marker = '{two_graph_cypher['graph_a']}' RETURN n.id",
        two_graph_cypher["graph_a"],
    )
    assert not list(rows), rows


# --- property reads -----------------------------------------------------------


def test_a_property_read_sees_only_its_own_graphs_value(two_graph_cypher):
    """`rdf_props` is now keyed `(graph_id, s, key)`, so the same key holds twice.

    One ID, one key, two rows — and a scoped read must return the one belonging
    to the graph it was asked about, not both and not neither.
    """
    run, shared = two_graph_cypher["run"], two_graph_cypher["shared"]

    for graph in (two_graph_cypher["graph_a"], two_graph_cypher["graph_b"]):
        rows = run(f"MATCH (n) WHERE n.id = '{shared}' RETURN n.graph_marker", graph)
        values = {r[0] for r in rows if r[0] is not None}
        assert values == {graph}, (
            f"graph {graph} read {values} for one property key; the property join "
            "is unioning two graphs"
        )


# --- Principle VII: direction symmetry ---------------------------------------


def test_direction_symmetry_survives_the_rekey(two_graph_cypher):
    """`(a)-[:R]->(b)` and `(b)<-[:R]-(a)` must agree (constitution VII).

    Asserted with the target pre-bound, because that is the shape that once
    returned rows in one direction and nothing in the other. `rdf_edges` already
    carried `graph_id`, but the `rdf_labels` join beside it did not.
    """
    shared = two_graph_cypher["shared"]
    target = two_graph_cypher["target_a"]
    graph_a = two_graph_cypher["graph_a"]

    outbound = two_graph_cypher["run"](
        f"MATCH (a)-[:TREATS]->(b) WHERE a.id = '{shared}' AND b.id = '{target}' "
        "RETURN a.id, b.id",
        graph_a,
    )
    inbound = two_graph_cypher["run"](
        f"MATCH (b)<-[:TREATS]-(a) WHERE a.id = '{shared}' AND b.id = '{target}' "
        "RETURN a.id, b.id",
        graph_a,
    )

    assert outbound, "the outbound pattern found nothing; the fixture edge is missing"
    assert sorted(tuple(r) for r in outbound) == sorted(tuple(r) for r in inbound), (
        f"direction symmetry broken after the re-key: outbound={outbound} "
        f"inbound={inbound}"
    )


def test_an_edge_does_not_cross_into_the_other_graph(two_graph_cypher):
    """graph A's shared node must reach graph A's target and no other."""
    run, shared = two_graph_cypher["run"], two_graph_cypher["shared"]

    for graph, expected in (
        (two_graph_cypher["graph_a"], two_graph_cypher["target_a"]),
        (two_graph_cypher["graph_b"], two_graph_cypher["target_b"]),
    ):
        rows = run(f"MATCH (a)-[:TREATS]->(b) WHERE a.id = '{shared}' RETURN b.id", graph)
        reached = {r[0] for r in rows}
        # Both targets appearing is the leak: one node ID, two graphs, two edges,
        # and a traversal that cannot tell which graph it was asked about.
        assert reached == {expected}, (
            f"graph {graph} reached {reached}, expected just {expected!r}"
        )


# --- the unscoped baseline ----------------------------------------------------


def test_an_unscoped_query_genuinely_sees_both_graphs(two_graph_cypher):
    """What the assertions above are measured against.

    The translator emits a graph predicate only under `USE GRAPH`
    (`_apply_graph_scope_to_reads`). Without one, this namespace holds two
    distinct nodes that happen to share an ID, and a query that names neither
    graph correctly names both — so both labels come back. Asserting it here
    keeps the scoped tests honest: they are measuring the scope, not an empty
    table, and a future change that makes bare Cypher scoped will fail here
    rather than turning every test above green for the wrong reason.
    """
    rows = two_graph_cypher["run"](
        f"MATCH (n) WHERE n.id = '{two_graph_cypher['shared']}' RETURN labels(n)"
    )
    rendered = " ".join(str(r[0]) for r in rows)

    assert "Patient" in rendered and "Member" in rendered, (
        "an unscoped read returned only one graph's labels "
        f"({rendered!r}); bare Cypher is now scoped somehow, and every scoped "
        "assertion in this file needs re-reading against that"
    )
