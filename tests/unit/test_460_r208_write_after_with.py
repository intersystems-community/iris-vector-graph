"""4.6.0 R208: a SET, REMOVE, DELETE or MERGE after a WITH writes the nodes the WITH carried.

Two defects. A node carried by a WITH is the stage column named after its variable (`Stage1.n`), and every SET and
REMOVE selected `Stage1.node_id`, which no stage has: `MATCH (n:X {k: 1}) WITH n SET n.q = 3` raised SQLCODE -29.
And each statement after the WITH re-ran the stage, which re-runs the MATCH: once a write before the WITH had changed
what the MATCH filters on (`MATCH (n:X {k: 1}) SET n.k = 2 WITH n SET n.q = 3`), the stage found nothing, so the
writes after it wrote nothing (a MERGE or CREATE too, silently).

Now the stage is read once, ahead of the writes before it, when they can change what its MATCH selects
(`__pin_stage__ <stage>`), and every later statement reads those rows (`<stage> AS (__PINNED_<stage>__)`, which the
executor fills). A WITH whose own WHERE reads a key those writes changed cannot be read before them or after them;
a write after it is refused instead of writing nothing.

These pin the statements; tests/e2e/test_460_r208_write_after_with_e2e.py checks the stored graph.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from tests.unit._one_graph import ONE_GRAPH

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes|kg_NodeEmbeddings)\b")
_WRITE = re.compile(r"\s*(WITH .*?\)\s*)?(UPDATE|INSERT|DELETE)\b", re.S)
_PIN = "__pin_stage__ "


def _stmts(q, params=None):
    # one graph: the shapes pinned here are a one-graph namespace's (a namespace of several: tests/e2e/
    # test_460_all_graphs_writes_e2e.py)
    r = translate_to_sql(parse_query(q), params or {}, engine=ONE_GRAPH)
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


def _first_write(stmts):
    return next(i for i, (s, _) in enumerate(stmts) if _WRITE.match(s))


@pytest.mark.parametrize(
    "tail",
    [
        "SET n.q = 3",
        "SET n:Y",
        "SET n += {q: 3}",
        "SET n = {q: 3}",
        "REMOVE n.k",
        "REMOVE n:X",
        "SET n.q = n.k + 1",
    ],
)
def test_a_write_after_with_selects_the_stage_column_of_its_variable(tail):
    stmts = _stmts(f"MATCH (n:X {{k: 1}}) WITH n {tail}")
    assert not any("Stage1.node_id" in s for s, _ in stmts), stmts
    assert any("SELECT Stage1.n\nFROM Stage1" in s or "SELECT Stage1.n FROM Stage1" in s for s, _ in stmts), stmts


def test_a_reserved_variable_name_is_quoted_as_the_stage_spells_it():
    stmts = _stmts("MATCH (key:X) WITH key SET key.q = 3")
    assert any('Stage1."key"' in s for s, _ in stmts), stmts


def test_a_write_after_with_that_changes_the_match_key_reads_a_capture():
    """No write before the WITH; its own two items: the second must not re-run the stage after the first."""
    stmts = _stmts("MATCH (n:X {k: 1}) WITH n SET n.k = 2, n.q = 3")
    caps = [s.split("\n", 1)[0].split()[1] for s, _ in stmts if s.startswith("__capture_ids__ ")]
    assert len(caps) == 1, stmts
    assert all(f"__IDS_{caps[0]}__" in s for s, _ in stmts if _WRITE.match(s)), stmts


@pytest.mark.parametrize(
    "tail",
    [
        "SET n.q = 3",
        "SET n.q = 3, n.k = 9",
        "REMOVE n.keep",
        "REMOVE n:X",
        "SET n:Y",
        "DELETE n",
        "DETACH DELETE n",
        "MERGE (n)-[:T]->(m)",
        "CREATE (n)-[:T]->(m)",
        "SET n.q = 3 WITH n SET n.r = 4",
        "SET n.q = 3 RETURN n.k",
    ],
)
def test_the_stage_is_read_before_the_writes_that_change_its_match(tail):
    stmts = _stmts(f"MATCH (n:X {{k: 1}}), (m:M) SET n.k = 2 WITH n, m {tail}")
    pins = [i for i, (s, _) in enumerate(stmts) if s.startswith(_PIN)]
    assert pins, stmts
    assert all(i < _first_write(stmts) for i in pins), stmts
    head, body = stmts[pins[0]][0].split("\n", 1)
    assert head == f"{_PIN}Stage1", head
    assert "Stage1 AS (\nSELECT" in body and body.rstrip().endswith("SELECT * FROM Stage1"), body
    # every statement after the WITH reads the pinned rows, none re-runs the stage
    after = [s for s, _ in stmts if "Stage1" in s and not s.startswith(_PIN)]
    assert after, stmts
    for s in after:
        assert "Stage1 AS (\nSELECT" not in s, s
        assert "Stage1 AS (__PINNED_Stage1__)" in s, s


def test_the_pin_binds_the_stage_parameters_and_later_statements_do_not():
    stmts = _stmts("MATCH (n:X {k: 1}) SET n.k = 2 WITH n SET n.q = 3")
    pin = next((s, p) for s, p in stmts if s.startswith(_PIN))
    assert pin[1] == ["X", "k"], pin
    upd = next((s, p) for s, p in stmts if "SET val = Graph_KG.Prop_Set(%ID, val, ?)" in s and "Stage1" in s)
    assert upd[1] == [3, "q"], upd


@pytest.mark.parametrize(
    "q",
    [
        # nothing before the WITH changes what its MATCH selects
        "MATCH (n:X {k: 1}) SET n.q = 2 WITH n SET n.r = 3",
        "MATCH (n:X) SET n.q = 2 WITH n SET n.r = 3",
        "MATCH (n:X {k: 1}) WITH n SET n.q = 3",
        # a WITH that projects a value, not only the nodes: read live as before
        "MATCH (n:X) WITH n.k AS k RETURN k",
    ],
)
def test_no_pin_when_the_stage_is_unchanged(q):
    assert not any(s.startswith(_PIN) or "__PINNED_" in s for s, _ in _stmts(q))


def test_a_with_reading_a_key_the_writes_changed_reads_the_captured_rows():
    """`SET n.k = 2 WITH n WHERE n.k = 2 SET n.q = 3`: read before the SET the WHERE drops every row, read after it
    the MATCH does. It was refused; since R241 the stage reads the rows the SET captured, after the SET."""
    stmts = _stmts("MATCH (n:X {k: 1}) SET n.k = 2 WITH n WHERE n.k = 2 SET n.q = 3")
    assert not any(s.startswith(_PIN) or "__PINNED_" in s for s, _ in stmts), stmts
    assert any("Stage1 AS (" in s and "n0.node_id IN (__IDSALL_w0__)" in s for s, _ in stmts), stmts


def test_a_with_reading_a_label_the_writes_changed_refuses_a_write_after_it():
    """`REMOVE n:X WITH n WHERE n:X SET n.q = 3`: the label the MATCH tests is gone; a write after it would write
    nothing (R208). A CREATE or MERGE there is refused the same (R241)."""
    for tail in ("SET n.q = 3", "CREATE (:Y)", "MERGE (:Y {k: 1})"):
        with pytest.raises(SyntaxError, match="is not supported"):
            _stmts(f"MATCH (n:X {{k: 1}}) REMOVE n:X WITH n WHERE n:X {tail}")


def test_a_with_reading_a_changed_key_still_returns():
    """No write after it: the RETURN is translated as before (TCK Set6 [5] has this shape without the filter)."""
    _stmts("MATCH (n:X {k: 1}) SET n.k = 2 WITH n WHERE n.k = 2 RETURN n.k")


def test_pinned_stage_sql_reads_the_captured_rows():
    from iris_vector_graph.stores.iris_sql_store import _pinned_stage_sql

    sql = _pinned_stage_sql(["n", "m"], [["a", "x"], ["b", "it's"]])
    assert sql.startswith('SELECT s.c0 AS "n", s.c1 AS "m" FROM JSON_TABLE(\'[{"c0": "a", "c1": "x"}, '), sql
    assert "it''s" in sql and "COLUMNS(c0 VARCHAR(32000) PATH '$.c0', c1 VARCHAR(32000) PATH '$.c1')" in sql, sql
    # a count read back as a number
    assert "c0 INTEGER PATH" in _pinned_stage_sql(["c"], [[2]])
    assert _pinned_stage_sql(["n"], []).endswith("WHERE 1 = 0"), "no rows: the stage selects nothing"


def test_a_statement_reading_a_stage_before_its_pin_raises():
    from iris_vector_graph.stores.iris_sql_store import _fill_pinned

    with pytest.raises(RuntimeError, match="Stage1"):
        _fill_pinned("WITH Stage1 AS (__PINNED_Stage1__)\nSELECT 1", {})
    assert _fill_pinned("WITH Stage1 AS (__PINNED_Stage1__)\nSELECT 1", {"Stage1": (["n"], [["a"]])}).startswith(
        "WITH Stage1 AS (SELECT s.c0 AS \"n\" FROM JSON_TABLE("
    )


def test_a_node_merged_after_with_gets_its_label():
    """The MERGE rewrite read the label as the label insert's second marker; after a WITH the stage's markers come
    first, so `MATCH (p:P) WITH p MERGE (c:C)` labelled the new node with its own id."""
    stmts = _stmts("MATCH (p:P) WITH p MERGE (c:C)")
    lbl = next(p for s, p in stmts if s.startswith("INSERT INTO rdf_labels"))
    created = next(v for s, p in stmts if "INSERT INTO nodes" in s for v in p if v not in ("P", "C", ""))
    assert lbl[:2] == [created, "C"], lbl
