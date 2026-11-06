"""4.6.0 R307: a relationship qualifier that is NaN or an infinity is refused before anything is written.

`create_edge(qualifiers={"p": float("nan")})` stored `{"p": NaN}`, which json.dumps writes and JSON does not allow;
IRIS's JSON functions then failed (-400) on every relationship read in the namespace that met the row, whatever its
query asked for. `create_edge(weight=nan)` already raises ValueError (4.6.0 R215); a qualifier now does too, in
create_edge, store_edge and the bulk loader. Found by the value matrix (tests/e2e/test_460_r307_value_matrix_e2e.py).
"""

from __future__ import annotations

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
