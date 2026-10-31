"""A failed write raises and leaves nothing behind (Tom, 2026-10-31).

Found by the spec 260 long-name probe: `engine.create_edge` with a 256-character source and
a 256-character target gets SQLCODE -415 from the edge row's unique index (the global
reference would pass IRIS's ~511-byte limit). It logged that, returned False (the value a
duplicate returns too), and left the two endpoint node rows it had inserted: each statement
committed on its own outside START TRANSACTION.

Each case below takes a digest of the rows that could have been written (by the names
involved: nodes, rdf_edges, rdf_labels, rdf_props and ^KG("out") of every graph), attempts the
write, and requires the exception and an unchanged digest. The positive cases (a normal
edge, a duplicate) are kept as they were.

`SKIP_IRIS_TESTS=true` fails rather than skips.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.errors import GraphWriteError

pytestmark = [pytest.mark.e2e]

#: The over-long name pair is a <SUBSCRIPT> in the edge index's %SQLInsert. IRIS reports it as
#: SQLCODE -415 in a fresh process and as -400 in the suite (measured, same error text), so both
#: are accepted and the text is checked.
SUBSCRIPT_CODES = (-415, -400)


@pytest.fixture(scope="module")
def eng(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail("these tests assert what a failed write leaves in IRIS; a skip is not a pass")
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=768)


def _tag() -> str:
    return "cea" + uuid.uuid4().hex[:6]


def _digest(eng, names) -> dict:
    cur = eng.conn.cursor()
    try:
        # By the names involved, not whole-table counts: the names are unique to the test, and
        # another writer in a shared namespace would move a table count (measured: the unit
        # suite running beside it did).
        out = {}
        for n in names:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [n])
            out["node " + n[:12]] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?", [n, n])
            out["edge " + n[:12]] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE s = ?", [n])
            out["label " + n[:12]] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE s = ?", [n])
            out["prop " + n[:12]] = int(cur.fetchone()[0])
    finally:
        cur.close()
    import iris

    o = iris.createIRIS(eng.conn)
    for n in names:
        g = ""
        while True:
            g = o.nextSubscript(False, "^KG", "out", g)
            if g in (None, ""):
                break
            try:
                out[f"kg {g} {n[:12]}"] = int(o.isDefined("^KG", "out", g, n))
            except Exception as e:  # a name too long to be a subscript is not in ^KG
                out[f"kg {g} {n[:12]}"] = "unsubscriptable" if "SUBSCRIPT" in str(e) else str(e)
    return out


def _assert_nothing_written(eng, names, attempt, code=None):
    before = _digest(eng, names)
    with pytest.raises(GraphWriteError) as info:
        attempt()
    assert _digest(eng, names) == before
    if code is not None:
        assert info.value.sqlcode in code, info.value
    return info.value


def test_create_edge_with_a_long_name_pair_raises_and_leaves_no_node_rows(eng):
    t = _tag()
    s, o = t + "s" * (256 - len(t) - 1), t + "o" * (256 - len(t) - 1)
    err = _assert_nothing_written(eng, [s, o], lambda: eng.create_edge(s, "PLONG", o), code=SUBSCRIPT_CODES)
    assert err.operation == "create_edge" and "SUBSCRIPT" in err.message


def test_create_edge_in_a_named_graph_with_a_long_pair_raises_and_leaves_no_node_rows(eng):
    # with a 10-character graph id the longest pair that fits is about 235 + 235 (spec 260 probe)
    t = _tag()
    s, o = t + "s" * (250 - len(t)), t + "o" * (250 - len(t))
    _assert_nothing_written(eng, [s, o], lambda: eng.create_edge(s, "PLONG", o, graph=t + "g"), code=SUBSCRIPT_CODES)


def test_create_edge_with_a_predicate_wider_than_its_column_raises_and_leaves_no_node_rows(eng):
    """A second failure: rdf_edges.p is VARCHAR(128); the endpoints were written first."""
    t = _tag()
    s, o = t + "a", t + "b"
    _assert_nothing_written(eng, [s, o], lambda: eng.create_edge(s, "P" * 129, o))


def test_create_edge_still_creates_and_still_answers_false_for_a_duplicate(eng):
    t = _tag()
    s, o = t + "a", t + "b"
    try:
        assert eng.create_edge(s, "OK", o) is True
        assert eng.create_edge(s, "OK", o) is False
    finally:
        eng.delete_node(s)
        eng.delete_node(o)


def test_store_edge_with_a_long_pair_raises_and_leaves_no_node_rows(eng):
    t = _tag()
    s, o = t + "s" * (256 - len(t) - 1), t + "o" * (256 - len(t) - 1)
    _assert_nothing_written(eng, [s, o], lambda: eng.store_edge(s, "PLONG", o), code=SUBSCRIPT_CODES)


def test_store_node_with_a_property_key_wider_than_its_column_raises_and_leaves_no_node_row(eng):
    t = _tag()
    n = t + "n"
    _assert_nothing_written(eng, [n], lambda: eng.store_node(n, properties={"k" * 129: "v"}, labels=["L"]))


def test_store_write_edges_with_a_long_pair_raises_and_writes_nothing(eng):
    t = _tag()
    s, o = t + "s" * (256 - len(t) - 1), t + "o" * (256 - len(t) - 1)
    _assert_nothing_written(
        eng, [s, o], lambda: eng._store.write_edges([{"source": s, "predicate": "PLONG", "target": o}]), code=SUBSCRIPT_CODES
    )


def test_store_write_nodes_with_a_label_wider_than_its_column_raises_and_writes_nothing(eng):
    t = _tag()
    n = t + "n"
    _assert_nothing_written(eng, [n], lambda: eng._store.write_nodes([{"id": n, "labels": ["L" * 129]}]))
