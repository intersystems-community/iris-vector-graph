"""Regenerate ``234_mode_off_sql.json``: the SQL the translator emits for CREATE and
MERGE of relationships with multigraph mode off (spec 234 FR-009).

The file was captured at the commit before any multigraph translator code landed,
so it is the "today" that mode off must reproduce byte for byte. Only regenerate
it for a deliberate, reviewed change to single-edge SQL:

    python tests/unit/golden/gen_234_mode_off_sql.py
"""

from __future__ import annotations

import itertools
import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from iris_vector_graph.cypher.parser import parse_query  # noqa: E402
from iris_vector_graph.cypher.translator import (  # noqa: E402
    get_schema_prefix,
    set_schema_prefix,
    translate_to_sql,
)

GOLDEN = HERE / "234_mode_off_sql.json"

QUERIES = [
    "CREATE (a:A), (b:B) CREATE (a)-[:TYPE]->(b) CREATE (a)-[:TYPE]->(b)",
    "CREATE (a:A), (b:B) CREATE (a)-[:T {name: 'r1'}]->(b)",
    "CREATE (a:A)-[:R]->(b:B)",
    "CREATE (a:A), (b:B) CREATE (a)-[r:T]->(b) RETURN r",
    "MATCH (a:A), (b:B) CREATE (a)-[:T]->(b)",
    "MATCH (a:A), (b:B) CREATE (a)-[r:T {name: 'x'}]->(b) RETURN r.name",
    "MATCH (a:A) CREATE (a)-[:R]->(:B)",
    "UNWIND [1, 2] AS i MATCH (a:A), (b:B) CREATE (a)-[:R {i: i}]->(b)",
    "MATCH (a:A), (b:B) MERGE (a)-[r:TYPE]->(b) RETURN count(r)",
    "MATCH (a:A), (b:B) MERGE (a)-[r:TYPE {name: 'r2'}]->(b) RETURN count(r)",
    "MATCH (a:A), (b:B) MERGE (a)-[r:TYPE]-(b) RETURN r",
    "MATCH (a:A), (b:B) MERGE (a)<-[r:TYPE {k: 1}]-(b) RETURN r.k",
    "MATCH (a)-[t:T]->(b) DELETE t MERGE (a)-[t2:T {name: 'rel3'}]->(b) RETURN t2.name",
    "MATCH (a)-[r:T]->(b) DELETE r CREATE (a)-[:U]->(b)",
    "MERGE (a:A)-[r:R]->(b:B) RETURN r",
    "CREATE (a:A), (b:B) MERGE (a)-[r:R]->(b) RETURN r",
    "MATCH (a:A) MERGE (a)-[:R]->(b:B {name: 'x'})",
]


def translate(query: str, engine=None) -> dict:
    """Translate with deterministic node ids and unqualified table names, as a
    JSON-comparable dict. (The schema prefix is module state another test may set.)"""
    counter = itertools.count()
    original, prefix = uuid.uuid4, get_schema_prefix()
    uuid.uuid4 = lambda: uuid.UUID(int=next(counter))
    set_schema_prefix("")
    try:
        result = translate_to_sql(parse_query(query), {}, engine=engine)
    finally:
        uuid.uuid4 = original
        set_schema_prefix(prefix)
    sqls = result.sql if isinstance(result.sql, list) else [result.sql]
    return {"sql": sqls, "params": [[repr(p) for p in ps] for ps in result.parameters]}


if __name__ == "__main__":
    GOLDEN.write_text(
        json.dumps({q: translate(q) for q in QUERIES}, indent=1, sort_keys=True) + "\n"
    )
    print(f"wrote {len(QUERIES)} queries to {GOLDEN}")
