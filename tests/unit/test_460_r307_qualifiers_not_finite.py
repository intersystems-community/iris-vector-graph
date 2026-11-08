"""4.6.0 R307: a relationship qualifier that is NaN or an infinity is refused before anything is written.

`create_edge(qualifiers={"p": float("nan")})` stored `{"p": NaN}`, which json.dumps writes and JSON does not allow;
IRIS's JSON functions then failed (-400) on every relationship read in the namespace that met the row, whatever its
query asked for. `create_edge(weight=nan)` already raises ValueError (4.6.0 R215); a qualifier now does too, in
create_edge, store_edge and the bulk loader. Found by the value matrix (tests/e2e/test_460_r307_value_matrix_e2e.py).
"""

from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = [("node_id", None)]
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    engine._adjacency_indexed_cached = True  # rows only: ^KG by hand on this fake fails and raises (4.6.3)
    cursor.reset_mock()
    return engine, cursor


def _inserts(cursor):
    return [c.args[0] for c in cursor.execute.call_args_list if c.args and "INSERT" in str(c.args[0]).upper()]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, [1.0, math.nan], {"x": math.inf}])
@pytest.mark.parametrize("writer", ["create_edge", "store_edge"])
def test_refused_before_any_write(writer, bad):
    engine, cursor = _engine()
    with pytest.raises(ValueError, match="qualifier `p`"):
        getattr(engine, writer)("a", "R", "b", qualifiers={"p": bad, "k": "fine"})
    assert _inserts(cursor) == []


def test_a_finite_qualifier_is_written():
    engine, cursor = _engine()
    cursor.rowcount = 1
    engine.create_edge("a", "R", "b", qualifiers={"p": 0.95})
    assert any("rdf_edges" in sql for sql in _inserts(cursor))


def test_json_value_spells_a_number_as_its_json():
    """4.6.0 R307: IVG's JSON_VALUE answered a JSON number in ObjectScript's form (1.0 as 1, 0.95 as .95), so
    arithmetic over create_edge's 1.0 (`r.w + 1`) answered the integer 2. It answers the number's JSON text, which every
    filter spelling already accepts (number_spellings holds json.dumps's) and CY_ARITH types by its point."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph" / "schema.py").read_text()
    body = next(x for x in src.splitlines() if "FUNCTION Graph_KG.JSON_VALUE(" in x).split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert 'If obj.%GetTypeOf(key) = "number" { Quit obj.%Get(key, , "json") }' in body
    assert "'" not in body and "?" not in body


def test_every_number_spelling_includes_the_json_one():
    from iris_vector_graph.prop_values import number_spellings

    for v in (0.95, 1.0, 2.0, 1e-07, 1e20, -0.5, 7):
        assert json.dumps(v) in number_spellings(v), v


def test_a_number_spelling_includes_irises_plain_decimal():
    """SET r.w = 1e-7 stores the JSON number 0.0000001, which JSON_VALUE now answers as written (4.6.0 R307)."""
    from iris_vector_graph.prop_values import number_spellings

    assert "0.0000001" in number_spellings(1e-07) and ".95" in number_spellings(0.95)
