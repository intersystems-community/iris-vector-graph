"""4.6.3, live: two items on the 4.6.0 "Deferred to 4.6.1" list (fixes50, 2026-11-05) that later 4.6.0 work fixed
before 4.6.0 shipped. They pass on 4.6.2 (1b48c8cc); these cases keep them answered.

1. `RETURN n.id, any(...)` beside another column answered '1' as text (alone it answered 1). Fixed by R307 (a)
   (dd4598e5: the list predicate's column is read back as a boolean). openCypher: true, beside the integer 1.
2. The engine's labeled variable-length route read the start by its labels only when a property map held a value SQL
   does not check (a float in a list): `(a:L {k: [1.5]})-[*1..2]->(b)` must not start from an `:L` node whose `k`
   differs, nor `-[*1..2 {l: [1.5]}]->` walk a relationship whose `l` differs. Fixed by R308
   (`_project_checked_paths`). openCypher: only a1's paths (to b1), and for the relationship map only the paths whose
   every relationship holds `l: [1.5]`.

Data: a1 (k [1.5]) -[:R463 {l: [1.5]}]-> b1; a2 (k [2.5]) -[:R463 {l: [2.5]}]-> b2; a1 and a2 both :L463. Each case
runs on the default route and the SQL route.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    eng = engs["sql"]
    eng.execute_cypher("MATCH (n:L463) DETACH DELETE n", raise_on_error=True)
    eng.execute_cypher(
        "CREATE (:L463 {name: 'a1', id: 1, k: [1.5]})-[:R463 {l: [1.5]}]->(:L463 {name: 'b1'}), "
        "(:L463 {name: 'a2', id: 2, k: [2.5]})-[:R463 {l: [2.5]}]->(:L463 {name: 'b2'})",
        raise_on_error=True,
    )
    yield engs
    eng.execute_cypher("MATCH (n:L463) DETACH DELETE n", raise_on_error=True)
    conn.close()


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_list_predicate_beside_a_column_is_a_boolean(engines, route):
    q = "MATCH (n:L463 {name: 'a1'}) RETURN n.id, any(x IN [1, 2] WHERE x = 1)"
    assert ns463.typed(ns463.rows(engines[route], q)) == [[(1, "int"), (True, "bool")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize(
    "query,want",
    [
        ("MATCH (a:L463 {k: [1.5]})-[*1..2]->(b) RETURN a.name, b.name", [["a1", "b1"]]),
        ("MATCH (a:L463 {k: [1.5]})-[*1..2]->(b) RETURN b.name", [["b1"]]),
        ("MATCH (a:L463 {k: [2.5]})-[:R463*1..2]->(b) RETURN DISTINCT b.name", [["b2"]]),
        ("MATCH (a:L463 {k: [9.5]})-[*1..2]->(b) RETURN b.name", []),
        ("MATCH (a:L463)-[*1..2 {l: [1.5]}]->(b) RETURN a.name, b.name", [["a1", "b1"]]),
        ("MATCH (a:L463 {name: 'a2'})-[*1..2 {l: [1.5]}]->(b) RETURN b.name", []),
    ],
)
def test_a_labeled_variable_length_start_holds_its_property_map(engines, route, query, want):
    assert sorted(ns463.rows(engines[route], query)) == want
