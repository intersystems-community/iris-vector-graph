"""Spec 227 T013 — the re-key, asserted against the catalog rather than Python.

Phase 2's gate. `UNIQUE (graph_id, node_id)` is not a Python fact: it is a
constraint IRIS either holds or does not, and the only honest way to check it is
to insert a row IRIS should refuse and watch it be refused.

Three things are asserted here, in the order contracts/sql-schema.md §4 makes
them true:

1. The same `node_id` inserts into two graphs. Under 3.2.0's
   `uq_nodes_nodeid UNIQUE (node_id)` the second insert was refused, which is
   why one entity could never hold two embeddings.
2. A duplicate `(graph_id, node_id)` is still refused. Replacing the unique
   constraint must not remove uniqueness, only re-scope it.
3. A child row naming a node absent from *its own graph* is refused. This is the
   half that a single-column foreign key could never express: before 227,
   `rdf_labels (s) -> nodes (node_id)` was satisfied by the node existing in
   *any* graph.

Each refusal is asserted by its effect — the statement raised *and* the table is
unchanged — rather than by the exception text alone. The text is checked too, for
the reason IRIS gave, but only when the driver returned something decodable. It
does not always: run this file after
`tests/integration/test_227_fresh_vs_migrated.py`, which drops and recreates
`Graph_KG` in the same session on purpose, and every constraint violation comes
back as `<LIST ERROR> Incorrect list format, unsupported type for IRISList`
instead of its SQLCODE. The constraints are intact in that state — only the
driver's rendering of the error is lost — and a test that failed there would be
reporting on error formatting, not on the re-key.

A missing container is a failure, never a skip (constitution VIII gate 1).
"""

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

GRAPH_A = "ivg227-rekey-A"
GRAPH_B = "ivg227-rekey-B"
NODE_ID = "ivg227:rekey:shared"
ABSENT_NODE_ID = "ivg227:rekey:absent"


#: What the driver reports instead of a SQLCODE when it cannot decode the error
#: IRIS sent. Seen when another test in the same session has dropped and
#: recreated `Graph_KG` — which `tests/integration/test_227_fresh_vs_migrated.py`
#: does deliberately, to compare a fresh catalog against an upgraded one. Every
#: refusal below still happens; only the message comes back unreadable.
_UNDECODABLE = "<LIST ERROR>"


def _refuse(cursor, sql, params, *, count_sql, count_params, expected_count):
    """Run a statement IRIS must refuse, and prove the refusal by its effect.

    An accepted statement is the failure being tested for, so this raises rather
    than returning None — a caller that treated "no exception" as a pass would
    turn a lost constraint into a green test.

    The row count after the fact is the load-bearing assertion, not the exception
    text: a constraint that refused the statement left the table unchanged. The
    text is returned so a caller can also check the SQLCODE through
    `_assert_refusal_reason`, which is best-effort because the driver does not
    always hand back a decodable message.
    """
    try:
        cursor.execute(sql, params)
    except Exception as e:  # noqa: BLE001 — the driver's exception type varies
        err = str(e)
    else:
        raise AssertionError(f"IRIS accepted a statement it must refuse: {sql} {params}")

    cursor.execute(count_sql, count_params)
    actual = int(cursor.fetchone()[0])
    assert actual == expected_count, (
        f"the statement was refused but the table changed anyway: {count_sql} "
        f"returned {actual}, expected {expected_count} ({err})"
    )
    return err


def _assert_refusal_reason(err, *expected):
    """Check *why* IRIS refused, when it said so in a form the driver could read.

    `_refuse` has already proven that the statement had no effect. This adds the
    reason, which matters because a refusal for the wrong reason — a syntax error,
    say — would otherwise read as a constraint doing its job. It is skipped, not
    failed, on an undecodable message: the alternative is a test that goes red
    over the driver's error formatting while the constraint under test is intact.
    """
    if _UNDECODABLE in err:
        return
    lowered = err.lower()
    assert any(token.lower() in lowered for token in expected), err


@pytest.fixture
def rekeyed(iris_connection):
    """A schema at the 4.0.0 shape, with both test graphs empty before and after."""
    if SKIP_IRIS_TESTS:
        pytest.fail(
            "spec 227's re-key is a catalog fact. SKIP_IRIS_TESTS=true cannot "
            "observe it — start ivg-iris-enterprise with "
            "scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail(
            "no live IRIS connection: a UNIQUE constraint cannot be asserted "
            "against a mock."
        )

    from iris_vector_graph import IRISGraphEngine

    # The width is irrelevant to the constraints under test, but the base schema
    # declares `emb VECTOR(DOUBLE, n)` and `initialize_schema` refuses to guess a
    # width — a column declared with no length answers every later INSERT with
    # SQLCODE -260.
    engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
    engine.initialize_schema(auto_deploy_objectscript=False)

    def wipe():
        import contextlib

        cursor = iris_connection.cursor()
        for graph in (GRAPH_A, GRAPH_B):
            for table in ("rdf_labels", "rdf_props", "rdf_edges", "nodes"):
                with contextlib.suppress(Exception):
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} "
                        "WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )
        with contextlib.suppress(Exception):
            iris_connection.commit()
        cursor.close()

    wipe()
    yield iris_connection
    wipe()


# --- 1. one node ID, two graphs ------------------------------------------------


def test_the_same_node_id_lives_in_two_graphs(rekeyed):
    """The change that makes 4.0.0 the right version (FR-007)."""
    cursor = rekeyed.cursor()
    for graph in (GRAPH_A, GRAPH_B):
        cursor.execute(
            "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
            (NODE_ID, graph),
        )
    rekeyed.commit()

    cursor.execute(
        "SELECT graph_id FROM Graph_KG.nodes WHERE node_id = ? ORDER BY graph_id",
        (NODE_ID,),
    )
    assert [r[0] for r in cursor.fetchall()] == [GRAPH_A, GRAPH_B]


# --- 2. uniqueness is re-scoped, not removed -----------------------------------


def test_a_duplicate_graph_and_node_is_still_refused(rekeyed):
    cursor = rekeyed.cursor()
    cursor.execute(
        "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
        (NODE_ID, GRAPH_A),
    )
    rekeyed.commit()

    err = _refuse(
        cursor,
        "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
        (NODE_ID, GRAPH_A),
        count_sql="SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ? AND graph_id = ?",
        count_params=(NODE_ID, GRAPH_A),
        expected_count=1,
    )
    _assert_refusal_reason(err, "unique", "-119", "-108")


def test_the_old_unscoped_unique_constraint_is_gone(rekeyed):
    """`uq_nodes_nodeid` must not merely coexist — it would forbid test 1."""
    cursor = rekeyed.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'nodes' "
        "AND CONSTRAINT_NAME = ?",
        ("uq_nodes_nodeid",),
    )
    assert cursor.fetchone()[0] == 0, (
        "uq_nodes_nodeid still exists; the unique swap did not run, and one node "
        "ID still cannot hold two graphs' embeddings"
    )


def test_the_composite_unique_constraint_exists(rekeyed):
    cursor = rekeyed.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'nodes' "
        "AND CONSTRAINT_NAME = ?",
        ("uq_nodes_graph_node",),
    )
    assert cursor.fetchone()[0] == 1, "uq_nodes_graph_node is missing"


# --- 3. a child row cannot borrow another graph's node -------------------------


def test_a_label_cannot_name_a_node_from_a_different_graph(rekeyed):
    """The half a single-column FK could not express (FR-008).

    The node exists — in graph A. The label claims it in graph B. Under 3.2.0's
    `fk_labels_node (s) -> nodes (node_id)` this was legal, and a label from one
    graph attached to another graph's node.
    """
    cursor = rekeyed.cursor()
    cursor.execute(
        "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
        (NODE_ID, GRAPH_A),
    )
    rekeyed.commit()

    err = _refuse(
        cursor,
        "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)",
        (GRAPH_B, NODE_ID, "Patient"),
        count_sql="SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ?",
        count_params=(GRAPH_B, NODE_ID),
        expected_count=0,
    )
    _assert_refusal_reason(err, "-121", "foreign key", "referential")


def test_a_label_naming_no_node_at_all_is_refused(rekeyed):
    cursor = rekeyed.cursor()
    err = _refuse(
        cursor,
        "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)",
        (GRAPH_A, ABSENT_NODE_ID, "Patient"),
        count_sql="SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE s = ?",
        count_params=(ABSENT_NODE_ID,),
        expected_count=0,
    )
    _assert_refusal_reason(err, "-121", "foreign key", "referential")


def test_a_label_in_its_own_graph_is_accepted(rekeyed):
    """The composite FK must not refuse the legal case — a scoped test that
    only ever refuses would pass with the table empty."""
    cursor = rekeyed.cursor()
    cursor.execute(
        "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
        (NODE_ID, GRAPH_A),
    )
    cursor.execute(
        "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)",
        (GRAPH_A, NODE_ID, "Patient"),
    )
    rekeyed.commit()

    cursor.execute(
        "SELECT label FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ?",
        (GRAPH_A, NODE_ID),
    )
    assert [r[0] for r in cursor.fetchall()] == ["Patient"]


def test_the_same_label_key_exists_once_per_graph(rekeyed):
    """`pk_labels` is `(graph_id, s, label)`, so two graphs can both hold it."""
    cursor = rekeyed.cursor()
    for graph in (GRAPH_A, GRAPH_B):
        cursor.execute(
            "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
            (NODE_ID, graph),
        )
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)",
            (graph, NODE_ID, "Patient"),
        )
    rekeyed.commit()

    cursor.execute(
        "SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE s = ? AND label = ?",
        (NODE_ID, "Patient"),
    )
    assert cursor.fetchone()[0] == 2

    err = _refuse(
        cursor,
        "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)",
        (GRAPH_A, NODE_ID, "Patient"),
        count_sql=(
            "SELECT COUNT(*) FROM Graph_KG.rdf_labels "
            "WHERE graph_id = ? AND s = ? AND label = ?"
        ),
        count_params=(GRAPH_A, NODE_ID, "Patient"),
        expected_count=1,
    )
    _assert_refusal_reason(err, "unique", "-119", "-108")


def test_props_are_keyed_per_graph_too(rekeyed):
    """`rdf_props` gained `graph_id` in the same pass (FR-034)."""
    cursor = rekeyed.cursor()
    for graph, value in ((GRAPH_A, "a"), (GRAPH_B, "b")):
        cursor.execute(
            "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
            (NODE_ID, graph),
        )
        cursor.execute(
            'INSERT INTO Graph_KG.rdf_props (graph_id, s, "key", val) VALUES (?, ?, ?, ?)',
            (graph, NODE_ID, "name", value),
        )
    rekeyed.commit()

    cursor.execute(
        'SELECT val FROM Graph_KG.rdf_props WHERE graph_id = ? AND s = ? AND "key" = ?',
        (GRAPH_A, NODE_ID, "name"),
    )
    assert [r[0] for r in cursor.fetchall()] == ["a"]
