"""4.7.0 row 19: `WITH collect(r) AS rs RETURN rs` answers the relationships, as `RETURN collect(r)` does.

A WITH stage keeps a collected relationship as its type (the `rs` column) and its edge id (`__rels_rs_eids`), which
the stages after it read: `size(rs)`, `rs[0]`, a DELETE of an element. A RETURN of `rs` read the types column, so the
query answered `['R', 'R']` where openCypher answers the relationships. The stage now also keeps each relationship
as RETURN spells one (`{"type", "props"}`, the same JSON text `RETURN collect(r)` aggregates), in the same aggregate,
so in the same order, and a RETURN of the name reads that, marked for the engine to read into dicts. The live answers
are tests/e2e/test_470_with_collected_rels_e2e.py.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _q(cypher):
    return translate_to_sql(parse_query(cypher), {})


def _sql(q):
    return q.sql if isinstance(q.sql, str) else "\n".join(q.sql)


def test_a_return_of_the_collected_list_reads_the_relationships():
    q = _q("MATCH ()-[r:R]->() WITH collect(r) AS rs RETURN rs")
    sql = _sql(q)
    assert "Stage1.__rels_rs_json AS rs" in sql, sql
    assert "JSON_ARRAYAGG('{\"type\":\"' || e" in sql, sql
    assert "rs" in [c.strip('"').lower() for c in q.graph_json_columns]


def test_forwarded_through_a_second_with():
    sql = _sql(_q("MATCH ()-[r:R]->() WITH collect(r) AS rs WITH rs AS xs RETURN xs"))
    assert "Stage1.__rels_rs_json AS __rels_xs_json" in sql, sql
    assert "Stage2.__rels_xs_json AS xs" in sql, sql


def test_the_types_column_still_serves_size_and_subscript():
    sql = _sql(_q("MATCH ()-[r:R]->() WITH collect(r) AS rs RETURN size(rs) AS n"))
    assert "__rels_rs_json AS n" not in sql and "Stage1.rs" in sql, sql
