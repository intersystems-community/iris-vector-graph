"""4.7.0 row 35b, live: the Python writers have no equal-text skip (row 35), because none updates a key in place.

Measured on f12bee92 (IRIS 2026.3, IVG470CY2), over a node holding p '30' and q '30', writing {p: 30, q: 31}:
store_node replaces the node's properties (DELETE then INSERT), so p is the number 30 and q 31; create_node refuses
an existing node (False) and writes nothing; bulk_create_nodes (both paths), IRISGraphStore.write_nodes and
BulkLoader.load_nodes keep every property the node has, whatever the new value (q stays '30'), as USER_GUIDE says:
"`bulk_create_nodes` does the same for an ID that exists (new labels and new properties added, old values kept)".
Pinned here, so a writer that starts updating a key in place has to go through Prop_Set.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470W"
STRING_30, INTEGER_30, INTEGER_31 = b"\x04\x0130", b"\x03\x04\x1e", b"\x03\x04\x1f"


@pytest.fixture()
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    ns463.rows(engs["sql"], f"CREATE (:{LABEL} {{id: 'n1', p: 5, q: 2}})")
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


def _stored(conn, schema, nid):
    cur = conn.cursor()
    cur.execute(f"SELECT \"key\", $LISTBUILD(val) FROM {schema}.rdf_props WHERE s = ? AND \"key\" IN ('p', 'q') "
                "ORDER BY \"key\"", [nid])
    return [tuple(r) for r in cur.fetchall()]


@pytest.mark.parametrize("path", ["create_node", "store_node", "bulk_server", "bulk_sql", "write_nodes", "load_nodes"])
def test_the_python_writers_over_an_existing_string(engines, path):
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.bulk_loader import BulkLoader
    from iris_vector_graph.capabilities import IRISCapabilities
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    e, conn = engines["sql"], engines["sql"].conn
    schema = e._ivg_schema
    nid = f"t470n:{path}"
    e.delete_node(nid)
    ns463.rows(e, f"CREATE (:{LABEL} {{node_id: $id, p: '30', q: '30'}})", {"id": nid})
    conn.commit()
    props = {"p": 30, "q": 31}
    if path == "create_node":
        assert e.create_node(nid, labels=[LABEL], properties=props) is False
    elif path == "store_node":
        assert e.store_node(nid, properties=props, labels=[LABEL])
    elif path == "bulk_server":
        e.capabilities = None
        assert e.capabilities.objectscript_deployed, "the server loader is not deployed: this case proves nothing"
        e.bulk_create_nodes([{"id": nid, "labels": [LABEL], "properties": dict(props)}])
    elif path == "bulk_sql":
        w = IRISGraphEngine(conn, namespace=ns463.NS, schema_prefix=schema)
        w.capabilities = IRISCapabilities()
        w.bulk_create_nodes([{"id": nid, "labels": [LABEL], "properties": dict(props)}])
    elif path == "write_nodes":
        IRISGraphStore(conn, namespace=ns463.NS, schema=schema).write_nodes(
            [{"id": nid, "labels": [LABEL], "properties": dict(props)}])
    elif path == "load_nodes":
        BulkLoader(conn, schema=schema).load_nodes([(nid, dict(props, namespace=LABEL))], use_noindex=False)
    conn.commit()
    want = [("p", INTEGER_30), ("q", INTEGER_31)] if path == "store_node" else [("p", STRING_30), ("q", STRING_30)]
    assert _stored(conn, schema, nid) == want
    e.delete_node(nid)
    conn.commit()
