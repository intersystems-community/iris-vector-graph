"""4.6.0 R238: a RETURN after a SET answers the rows the MATCH bound, when the SET changes a key the MATCH filters on.

`MATCH (n:QX {name: 'A'}) SET n.name = 'C' RETURN n.id` answered every QX: the RETURN reads the MATCH again and drops
its test on a key the query sets. R211 kept the matched node through the writes' capture for a key that is removed;
now a key that is set does the same (`n0.node_id IN (__IDSALL_<key>__)`), and a relationship the same through its
edge capture (`e2.edge_id IN (__IDSALL_<key>__)`). A test on a key the query does not write stays in the RETURN.

These pin the statements; tests/e2e/test_460_r238_return_after_set_e2e.py checks the stored graph and the answers.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes|kg_NodeEmbeddings)\b")


def _stmts(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


def _captures(stmts):
    return [s.split("\n", 1)[0].split()[1] for s, _ in stmts if s.startswith("__capture_ids__ ")]


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:X {name: 'A'}) SET n.name = 'C' RETURN n.id",
        "MATCH (n:X {name: 'A'}) SET n.name = 'C' RETURN count(*)",
        "MATCH (n:X) WHERE n.name = 'A' SET n.name = 'C' RETURN n.name",
        "MATCH (n:X) WHERE n.name STARTS WITH 'A' SET n.name = 'C' RETURN n.name",
        "MATCH (n:X {name: 'A'}) SET n += {name: 'C'} RETURN n.name",
        "MATCH (n:X {name: $v}) SET n.name = 'C' RETURN n.name",
    ],
)
def test_the_return_reads_the_captured_node_not_the_set_key(q):
    stmts = _stmts(q, {"v": "A"})
    caps = _captures(stmts)
    assert len(caps) == 1, stmts
    final, params = stmts[-1]
    assert final.startswith("SELECT "), final
    assert f"n0.node_id IN (__IDSALL_{caps[0]}__)" in final, final
    where = final.split("\nWHERE ", 1)[1] if "\nWHERE " in final else ""
    # no test on `name`'s old value is left: neither the literal nor the parameter
    assert "'A'" not in final and "LIKE" not in where, final
    assert "A" not in params, params
    assert final.count("?") == len(params), (final, params)


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (x)-[r:T {w: 1}]->(y) SET r.w = 5 RETURN x.name, r.w, y.name",
        "MATCH ()-[r:T {w: 1}]->() SET r.w = 2 RETURN count(*)",
        "MATCH (x)-[r:T]->() WHERE r.w = 1 SET r.w = 3 RETURN x.name",
    ],
)
def test_the_return_reads_the_captured_relationship(q):
    stmts = _stmts(q)
    caps = _captures(stmts)
    assert len(caps) == 1, stmts
    final, params = stmts[-1]
    m = re.search(rf"\b(e\d+)\.edge_id IN \(__IDSALL_{caps[0]}__\)", final)
    assert m, final
    assert f"{m.group(1)}.qualifiers, '$.w'" not in final.split("\nWHERE ", 1)[1], final
    assert final.count("?") == len(params), (final, params)


def test_a_variable_the_set_does_not_write_is_captured_too():
    """`MATCH (a {name: 'A'}), (b) SET b.name = 'Z'`: a's test on `name` goes (when b is a, a's name changed), so a's
    ids are captured ahead of the writes and the RETURN keeps a through them; b is tested on nothing written."""
    stmts = _stmts("MATCH (a:X {name: 'A'}), (b:X) SET b.name = 'Z' RETURN a.id, b.id")
    caps = _captures(stmts)
    assert len(caps) == 2, stmts
    first_write = next(i for i, (s, _) in enumerate(stmts) if not s.startswith("__capture_ids__ "))
    assert all(s.startswith("__capture_ids__ ") for s, _ in stmts[:first_write]) and first_write == 2, stmts
    final, params = stmts[-1]
    assert re.search(rf"n0\.node_id IN \(__IDSALL_({caps[0]}|{caps[1]})__\)", final), final
    assert "n3.node_id IN" not in final and "'A'" not in final, final
    assert final.count("?") == len(params), (final, params)


def test_a_key_the_query_does_not_write_keeps_its_test():
    """`SET n.k = 9` changes nothing the MATCH reads: no capture, and the RETURN keeps `name = 'A'` (it answered every
    X with a name)."""
    stmts = _stmts("MATCH (n:X {name: 'A'}) SET n.k = 9 RETURN n.name, n.k")
    assert not _captures(stmts)
    final, params = stmts[-1]
    assert "__IDSALL_" not in final
    assert ".val = CAST('A' AS VARCHAR(256))" in final, final
    assert final.count("?") == len(params), (final, params)


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:X) SET n.name = 'C' RETURN n",
        "MATCH (n:X {id: 'a'}) SET n.name = 'C' RETURN n",
        "MATCH (n:X {name: 'A'}) RETURN n",
    ],
)
def test_the_return_is_unchanged_when_no_filtered_key_is_written(q):
    assert "__IDSALL_" not in _stmts(q)[-1][0]
