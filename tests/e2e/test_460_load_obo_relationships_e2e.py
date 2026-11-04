"""4.6.0 R092: `load_obo` stores an OBO `relationship:` line as an edge of that relationship's type.

obonet reads an OBO file into a networkx MultiDiGraph whose edge *key* is the relationship (`is_a`, `part_of`,
`regulates`); the edge's data holds nothing. `load_networkx` iterated `G.edges(data=True)`, which never yields the
key, so every edge fell back to `is_a`.

The ontology, worked by hand (obonet points each edge from the term to its parent or target):
    T:2 is_a T:1, T:3 is_a T:1, T:4 part_of T:3, T:5 is_a T:2, T:5 regulates T:4
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

OBO = """format-version: 1.2

[Term]
id: T:1
name: root
namespace: test_ns

[Term]
id: T:2
name: two
namespace: test_ns
is_a: T:1

[Term]
id: T:3
name: three
namespace: test_ns
is_a: T:1

[Term]
id: T:4
name: four
namespace: test_ns
relationship: part_of T:3

[Term]
id: T:5
name: five
namespace: test_ns
is_a: T:2
relationship: regulates T:4

[Typedef]
id: part_of
name: part of

[Typedef]
id: regulates
name: regulates
"""

EXPECTED = {
    ("T:2", "is_a", "T:1"),
    ("T:3", "is_a", "T:1"),
    ("T:4", "part_of", "T:3"),
    ("T:5", "is_a", "T:2"),
    ("T:5", "regulates", "T:4"),
}


def test_each_relationship_keeps_its_type(iris_connection, iris_master_cleanup, tmp_path):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    path = tmp_path / "tiny.obo"
    path.write_text(OBO)
    assert engine.load_obo(str(path)) == {"nodes": 5, "edges": 5, "skipped_nodes": 0, "skipped_edges": 0}

    cur = iris_connection.cursor()
    try:
        cur.execute(f"SELECT s, p, o_id FROM {engine._t('rdf_edges')} WHERE graph_id = ''")
        stored = {tuple(row) for row in cur.fetchall()}
    finally:
        cur.close()
    assert stored == EXPECTED

    rows = engine.execute_cypher(
        "MATCH ({node_id: 'T:5'})-[r]->(p) RETURN type(r) AS rel, p.name AS target ORDER BY rel"
    ).rows
    assert rows == [["is_a", "two"], ["regulates", "four"]]
