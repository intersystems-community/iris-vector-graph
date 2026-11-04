"""4.6.0 R161, live: an engine whose user may not read `embedding_registry` gets an error, not the legacy table.

`_read_route` treated any failed registry read as "this database does not route", so `_route_for_read` answered the
legacy `kg_NodeEmbeddings` for a named graph: a user without SELECT on the registry searched the default graph's
vectors. Now only an absent registry (SQLCODE -30) falls back.

In `IVG_NAMESPACE` (IVG Schema RAG), as the admin: graph r161g's node r161:g holds e1 (8 dimensions), which routes
r161g to a table of its own; the default graph's r161:d holds e1 in the legacy table. A reader with every `/kg`
grant but SELECT on the registry (tests/e2e/readers460.py) asks for r161g's route: it must get the -99, not the
legacy table (whose nearest vector to e1 is r161:d, from the wrong graph). The admin's engine reads r161g's own route.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt
from tests.e2e import readers460 as readers

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

GRAPH = "r161g"
E1 = [1.0] + [0.0] * (alt.DIM - 1)


@pytest.fixture(scope="module")
def engines():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    admin = alt.engine(conn)
    admin.initialize_schema()
    admin.create_node("r161:d", labels=["R161"])
    admin.store_embedding("r161:d", E1)
    admin.create_node("r161:g", labels=["R161"], graph=GRAPH)
    admin.store_embedding("r161:g", E1, graph=GRAPH)
    conn.commit()
    user = readers.make("R161_NO_REGISTRY", omit=("embedding_registry",))
    rconn = iris.connect("localhost", alt.PORT, alt.NS, user, readers.PASSWORD)
    reader = alt.engine(rconn)
    yield admin, reader
    rconn.close()
    readers.drop("R161_NO_REGISTRY")
    admin.delete_node("r161:d", graph=None)
    admin.delete_node("r161:g", graph=GRAPH)
    conn.commit()
    conn.close()


def test_the_admin_reads_the_named_graphs_own_route(engines):
    admin, _ = engines
    table, route = admin._route_for_read(GRAPH, None)
    assert route is not None and table == route.table_name and table != "kg_NodeEmbeddings", table


def test_a_reader_without_the_registry_grant_gets_the_privilege_error(engines):
    _, reader = engines
    with pytest.raises(Exception) as raised:
        reader._route_for_read(GRAPH, None)
    assert "-99" in str(raised.value), raised.value
