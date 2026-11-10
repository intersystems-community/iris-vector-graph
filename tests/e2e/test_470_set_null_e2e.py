"""4.7.0 row 35b, live: a SET whose computed value is null removes the property.

Measured on f12bee92 (IRIS 2026.3, IVG470CY2): `SET n.p = n.missing + 1` left a row with a null val: `keys(n)` listed
p and `n.p` read ''. openCypher: a SET to null removes the property, as `SET n.p = null` does. The node: {p: 5, q: 2}.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470U"


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


@pytest.mark.parametrize("value", ["n.missing + 1", "n.missing * 2", "n.missing - 1", "n.q + n.missing"])
@pytest.mark.parametrize("key", ["p", "z"])  # an existing key, and a new one
def test_a_null_value_removes_the_property(engines, value, key):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'n1'}}) SET n.{key} = {value}")
    engines["sql"].conn.commit()
    for route in ns463.ROUTES:
        got = ns463.rows(engines[route], f"MATCH (n:{LABEL} {{id: 'n1'}}) RETURN n.{key}, keys(n)")
        assert got[0][0] is None and key not in got[0][1], (route, got)
    cur = engines["sql"].conn.cursor()
    schema = engines["sql"]._ivg_schema
    cur.execute(f"SELECT COUNT(*) FROM {schema}.rdf_props p JOIN {schema}.rdf_labels l ON l.s = p.s "
                f"WHERE l.label = ? AND p.\"key\" = ?", [LABEL, key])
    assert cur.fetchone()[0] == 0


def test_a_value_still_writes(engines):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'n1'}}) SET n.p = n.q + 1")
    assert ns463.typed(ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'n1'}}) RETURN n.p")) == [[(3, "int")]]
