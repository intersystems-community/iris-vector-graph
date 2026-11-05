"""4.6.0 R295, live: fixes45's leftovers, each on the SQL path and the native one, under whichever driver runs this.

Measured on the release head (IVG460FZ2, 2026.3.0AI, 2026-11-05):

- under driver 5.4.1, `UNWIND range(1, 2) AS i WITH i RETURN i` answered '1', '2' (JSON_TABLE's VARCHAR column),
  and `RETURN i + 1` text as well (LIST_CONCAT, the translator not knowing i is a number);
- `MATCH (n) WITH n RETURN id(n)` failed -29 (Stage1.node_id; the stage's column is n);
- `RETURN elementId(n)` failed -359 (sent to SQL as Graph_KG.elementId);
- the native executor read an empty-string property as '\\x00', and `WHERE n.s = ''` / `{s: ''}` matched nothing
  there (IRIS stores SQL's '' as $Char(0); the executor read the stored value and bound '' as NULL);
- a leading OPTIONAL MATCH with a property map, followed by a WITH, answered no row where its label has nodes;
- `OPTIONAL MATCH (x {name: 'b'})-[r]->(:L)` answered no row: its start has no label and its target has one, so no
  null-row fallback was registered.

The graph, worked by hand: (:L295 {name: 'a'}) -[:R295]-> (:L295 {name: 'c'}), (:L295 {name: 'b'}), and
(:E295 {s: '', t: 'x'}). b has no relationship, so the last OPTIONAL MATCH has no match and answers one null row; a
has one, to c. The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); the nodes are
removed after.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

NODES = {"r295a": ("L295", {"name": "a"}), "r295b": ("L295", {"name": "b"}), "r295c": ("L295", {"name": "c"}),
         "r295e": ("E295", {"s": "", "t": "x"})}


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    sql = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=False)
    native = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=True)
    for nid in NODES:
        sql.delete_node(nid)
    for nid, (label, props) in NODES.items():
        sql.create_node(nid, labels=[label], properties=props)
    sql.create_edge("r295a", "R295", "r295c")
    sql.sync()
    yield sql, native
    try:
        for nid in NODES:
            sql.delete_node(nid)
    finally:
        conn.close()


def _both(engines, q):
    """(SQL path's rows, native engine's rows), each sorted; both must answer without error."""
    out = []
    for eng in engines:
        r = eng.execute_cypher(q)
        assert getattr(r, "error", None) in (None, ""), (q, r.error)
        out.append(sorted((list(row) for row in r.rows), key=repr))
    return out


def _same(engines, q):
    a, b = _both(engines, q)
    assert a == b, (q, a, b)
    return a


def test_range_values_are_integers_through_a_with(engines):
    rows = _same(engines, "UNWIND range(1, 2) AS i WITH i RETURN i")
    assert rows == [[1], [2]] and all(type(r[0]) is int for r in rows)
    rows = _same(engines, "UNWIND range(5, 6) AS i RETURN i + 1 AS j, i / 2 AS h")
    assert rows == [[6, 2], [7, 3]] and all(type(v) is int for r in rows for v in r)


def test_id_of_a_node_a_with_passed_on(engines):
    assert _same(engines, "MATCH (n:L295) WITH n RETURN id(n)") == [["r295a"], ["r295b"], ["r295c"]]
    assert _same(engines, "MATCH (n:L295) WITH n AS m RETURN id(m), m.name") == [
        ["r295a", "a"], ["r295b", "b"], ["r295c", "c"]]


def test_element_id_is_the_nodes_id(engines):
    for q in ("MATCH (n:L295) RETURN elementId(n)", "MATCH (n:L295) WITH n RETURN elementId(n)"):
        assert _same(engines, q) == [["r295a"], ["r295b"], ["r295c"]], q


def test_the_native_executor_reads_and_matches_an_empty_string(engines):
    native = engines[1]
    for q, want in (("MATCH (n:E295) RETURN n.s, n.t", [["", "x"]]),
                    ("MATCH (n:E295) WHERE n.s = '' RETURN n.t", [["x"]]),
                    ("MATCH (n:E295 {s: ''}) RETURN n.t", [["x"]])):
        before = getattr(native, "_native_answers", 0)
        assert _same(engines, q) == want, q
        assert getattr(native, "_native_answers", 0) == before + 1, f"not answered natively: {q}"


def test_a_leading_optional_match_then_a_with_answers_the_null_row(engines):
    assert _same(engines, "OPTIONAL MATCH (n:L295 {name: 'zzz'}) WITH n RETURN n.name") == [[None]]
    rows = _same(engines, "OPTIONAL MATCH (n:L295 {name: 'zzz'}) WITH n RETURN n")
    assert len(rows) == 1 and rows[0][0] is None  # n_id: a null node
    assert _same(engines, "OPTIONAL MATCH (n:L295 {name: 'a'}) WITH n RETURN n.name") == [["a"]]
    assert _same(engines, "OPTIONAL MATCH (n:L295 {name: 'zzz'}) WITH count(n) AS c RETURN c") == [[0]]


def test_an_optional_match_from_an_unlabelled_start_to_a_labelled_target_answers_the_null_row(engines):
    assert _same(engines, "OPTIONAL MATCH (x {name: 'b'})-[r]->(:L295) RETURN r") == [[None]]
    assert _same(engines, "OPTIONAL MATCH (x {name: 'b'})-[r]->(:L295) WITH r RETURN r") == [[None]]
    assert _same(engines, "OPTIONAL MATCH (x {name: 'a'})-[r]->(:L295) RETURN type(r)") == [["R295"]]
