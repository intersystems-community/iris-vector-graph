"""An adopted route has no recorded width, and an embedded connection reads that NULL as ''.

Reported by Mindwalk against 4.2.0 on a fresh database: ``initialize_schema`` adopts
``kg_NodeEmbeddings`` into ``embedding_registry`` with ``dimension`` NULL, the embedded cursor hands
back ``''``, and ``int('')`` killed the first ``store_embedding``.
"""

import pytest

from tests.unit.test_227_route_cache import _engine


@pytest.mark.parametrize("empty", [None, ""])
def test_an_adopted_route_with_no_recorded_width_reads_as_no_width(empty):
    engine, _ = _engine(rows=[("kg_NodeEmbeddings", empty, empty, empty, empty)])

    found, route = engine._read_route("", None)

    assert found is True
    assert route.table_name == "kg_NodeEmbeddings"
    assert route.dimension is None
    assert route.dtype == "DOUBLE"
    assert route.index_state is None and route.index_error is None


@pytest.mark.parametrize("empty", [None, ""])
def test_the_recorded_identity_of_an_adopted_table_has_no_width(empty):
    engine, cursor = _engine()
    cursor.fetchone.return_value = (empty, empty, empty, empty, empty)

    identity = engine.get_embedding_identity("kg_NodeEmbeddings")

    assert identity.dimension is None
