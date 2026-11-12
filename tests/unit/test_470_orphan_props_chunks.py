"""bulk_create_nodes' orphaned-property DELETE stays under the parameter count IRIS prepares.

The DELETE binds each chunk of ids twice (the rdf_props IN list and the nodes subquery's), plus the graph twice. At
500 ids a chunk that is 1002 parameters, and IRIS 2026.3 refuses to prepare it (-1 on the laptop's build, -202 "closing
parentheses missing" on the Studio's; 4.7.0 gate, tests/e2e/test_460_r310_long_id_lists_e2e.py). A probe found the
cutoff between 400 and 499 ids for this shape, while one IN list of 500 prepares. This pins the count per statement,
and that every id is still covered once.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine

#: the largest parameter list the gate saw IRIS prepare in this statement shape (400 ids, bound twice, plus 2)
PREPARED = 802


def _engine():
    conn = MagicMock()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    return engine


@pytest.mark.parametrize("count", [1, 249, 250, 251, 500, 1201])
@pytest.mark.parametrize("scoped", [True, False])
def test_each_delete_binds_fewer_parameters_than_iris_refuses(count, scoped):
    engine = _engine()
    engine._nodes_has_graph_id = scoped
    engine._children_have_graph_id = scoped
    cursor = MagicMock()
    ids = [f"n{i}" for i in range(count)]
    engine._drop_orphaned_props(cursor, [{"id": i, "graph": "g1"} for i in ids])
    statements = [c.args for c in cursor.execute.call_args_list]
    assert statements, "no DELETE ran"
    covered = []
    for sql, params in statements:
        assert sql.startswith("DELETE FROM")
        assert len(params) <= PREPARED, f"{len(params)} parameters in one DELETE"
        assert sql.count("?") == len(params)
        chunk = params[1 : 1 + (len(params) - 2) // 2] if scoped else params[: len(params) // 2]
        covered.extend(chunk)
    assert covered == ids
