"""4.6.0 R285: a SET from a WITH column stores the value's own type under either driver.

R242's pair read (`WITH n.k AS k, n SET n.r = k`) captured the stage column as the driver fetched it, and wrote each
value at the SQL type its Python type named. `rdf_props.val` is VARCHAR holding an IRIS number: intersystems-irispython
5.4.0 fetched it as a Python number, 5.4.1 as IRIS's text, so under 5.4.1 the SET stored the string '3' (measured,
IVG460FY7: test_460_r242_set_reads_with_value_e2e 8 of 10 failed, '3' for 3, '2.5' for 2.5). The capture now reads the
column through CY_TYPED, whose mark says the type whatever the driver, and the executor decodes it (as R218 reads a
RETURN column).
"""

from __future__ import annotations

from iris_vector_graph.cypher import translator as t
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.prop_values import TYPED_MARK


def test_the_pair_capture_reads_the_with_column_through_cy_typed():
    sq = t.translate_to_sql(parse_query("MATCH (n:L) WITH n.k AS k, n SET n.r = k"), {}, engine=None)
    stmts = sq.sql if isinstance(sq.sql, list) else [sq.sql]
    captures = [s for s in stmts if "__capture_pairs__" in s]
    assert captures, stmts
    assert any("Graph_KG.CY_TYPED(" in s for s in captures), captures


def test_the_captured_value_is_decoded_to_its_type():
    from iris_vector_graph.stores.iris_sql_store import _PAIRS_SUFFIX, _capture_pairs

    captured: dict = {}
    _capture_pairs("v1", [("a", TYPED_MARK + "N3"), ("b", TYPED_MARK + "D2.5"), ("c", "hi"), ("d", None)], captured)
    assert captured["v1"] == ["a", "b", "c", "d"]
    pairs = captured["v1" + _PAIRS_SUFFIX]
    assert pairs == {"a": 3, "b": 2.5, "c": "hi", "d": None}
    assert type(pairs["a"]) is int and type(pairs["b"]) is float


def test_the_stage_snapshot_reads_each_captured_column_through_cy_typed_and_decodes_it():
    """`... SET n.r = k RETURN k` reads k from the rows captured before the writes (List12): under 5.4.1 it answered
    '3' for 3."""
    from iris_vector_graph.stores.iris_sql_store import _decode_snapshot_typed

    sq = t.translate_to_sql(parse_query("MATCH (n:L) WITH n.k AS k, n SET n.r = 9 RETURN k"), {}, engine=None)
    snap = next(s for s in sq.sql if s.startswith("__snapshot_stage__ "))
    assert snap.rstrip().endswith("SELECT Stage1.*, Graph_KG.CY_TYPED(Stage1.k) AS __typed_k FROM Stage1"), snap
    cols, rows = _decode_snapshot_typed(["K", "N", "__G_N", "__TYPED_K"], [["3", "n1", "", TYPED_MARK + "N3"],
                                                                          ["x", "n2", "", "x"]])
    assert cols == ["K", "N", "__G_N"] and rows == [[3, "n1", ""], ["x", "n2", ""]]
