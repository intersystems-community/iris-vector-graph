"""R115: running a query leaves the tree parse_query caches as it found it.

`parse_query` hands every run of a text the same tree. Scanning every Cypher string in `tests/` (about 4,800 that
parse) through `translate_to_sql` and `execute_cypher` found three places that changed it, besides the
`subsequent_queries` R113 fixed:

* `_lower_delete_containers` rewrote `WITH collect(n) AS l DELETE l[$i]` as `WITH n SKIP i LIMIT 1 DELETE n`
  in the tree, with `i` read from that run's parameters. Every later run of the text deleted the first run's
  element, whatever `$i` was: a wrong entity deleted, with no error;
* `_clip_unwind_range_to_with_limit` replaced `range(1, 500)` by `range(1, 5)` in the tree (the same answer
  for a text whose SKIP and LIMIT are literals, but a changed tree);
* `_name_merge_path_elements` gave the anonymous elements of `MERGE p = (...)` hidden names in the tree.

Each now works on a copy, made only when its rewrite applies.
"""
from __future__ import annotations

import dataclasses
import os
import uuid

import pytest

from iris_vector_graph.cypher import writes_cypher
from iris_vector_graph.cypher.parser import _parse_query_cached, parse_query
from iris_vector_graph.cypher.read_only import split_statements
from iris_vector_graph.cypher.translator import translate_to_sql
from tests.unit.test_r115_read_only_verdict_independent import (  # noqa: F401 - _fresh_caches is autouse here too
    CASES, CHAINED, COUNT_CREATE, ROW_MERGE, SEMICOLON, TREE_CHANGING, _engine, _fresh_caches, _params, _run,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

# memo fields the translator writes on purpose: they depend only on the tree (ast.CypherQuery)
_MEMO_FIELDS = ("param_clash", "param_twin")


def _fingerprint(x):
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return (type(x).__name__, tuple(
            (f.name, _fingerprint(getattr(x, f.name))) for f in dataclasses.fields(x) if f.name not in _MEMO_FIELDS
        ))
    if isinstance(x, (list, tuple)):
        return (type(x).__name__, tuple(_fingerprint(i) for i in x))
    if isinstance(x, dict):
        return ("dict", tuple(sorted((repr(k), _fingerprint(v)) for k, v in x.items())))
    return repr(x)


UNCHANGED = dict(TREE_CHANGING)
UNCHANGED.update({name: q for name, (q, _v) in CASES.items() if name not in ("does not parse", "not Cypher at all")})
UNCHANGED.update({"chained parts": CHAINED, "; split": SEMICOLON})
UNCHANGED.update(ROW_MERGE)
UNCHANGED.update(COUNT_CREATE)


@pytest.mark.parametrize("path", ["execute_cypher", "_execute_parsed", "translate_to_sql"])
@pytest.mark.parametrize("query", list(UNCHANGED.values()), ids=list(UNCHANGED))
def test_execution_leaves_the_cached_tree_unchanged(query, path):
    trees = []
    for statement in split_statements(query):
        try:
            trees.append(parse_query(statement))
        except Exception:  # noqa: BLE001 - SHOW and Neo4j Browser's metadata query do not parse
            pass
    before = [_fingerprint(t) for t in trees]
    eng, _store, _conn = _engine()
    _run(path, eng, query, _params(query))
    assert [_fingerprint(t) for t in trees] == before


def test_delete_by_a_parameter_index_follows_each_runs_parameter():
    """`DELETE friends[$i]` was lowered to `WITH n SKIP i LIMIT 1` in the cached tree, so every later run deleted
    the first run's element: run with i=0, then i=1, and the second run deleted element 0 again."""
    q = TREE_CHANGING["DELETE collect(n)[$i]"]
    expected = translate_to_sql(parse_query(q), {"friendIndex": 1})
    _parse_query_cached.cache_clear()
    translate_to_sql(parse_query(q), {"friendIndex": 0})
    again = translate_to_sql(parse_query(q), {"friendIndex": 1})
    assert again.sql == expected.sql
    assert "__rn > 1 AND" in str(again.sql)


def test_a_clipped_range_is_clipped_for_each_run():
    q = TREE_CHANGING["UNWIND range WITH SKIP LIMIT"]
    first = translate_to_sql(parse_query(q), {})
    second = translate_to_sql(parse_query(q), {})
    assert first.sql == second.sql
    rng = parse_query(q).query_parts[0].clauses[0].expression
    assert [a.value for a in rng.arguments] == [1, 500], "the range in the cached tree was rewritten"


def test_a_merged_path_names_its_elements_only_in_the_translation():
    q = TREE_CHANGING["MERGE p = path"]
    translate_to_sql(parse_query(q), {})
    merge = parse_query(q).query_parts[0].clauses[2]
    assert merge.pattern.relationships[0].variable is None, "the cached MERGE pattern was given hidden names"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestLiveIRIS:
    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        from tests.conftest import SESSION_EMBEDDING_DIM

        # the session's width: initialize_schema re-declares the shared embedding table to the engine's
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=SESSION_EMBEDDING_DIM)
        self.engine.initialize_schema()
        self.L = f"R115{uuid.uuid4().hex[:8]}"
        yield
        for suffix in ("", "F"):
            self.engine.execute_cypher(f"MATCH (n:{self.L}{suffix}) DETACH DELETE n")

    def _count(self, label):
        return self.engine.execute_cypher(f"MATCH (n:{label}) RETURN count(n) AS c").rows[0][0]

    def test_delete_by_index_uses_this_runs_index(self):
        """The first run deleted element 0; an index past the end then deletes nothing. Before R115 the cached
        tree held SKIP 0 LIMIT 1 and the second run deleted another friend."""
        L = self.L
        self.engine.execute_cypher(
            f"CREATE (u:{L} {{id:'u'}}), (u)-[:FRIEND]->(:{L}F {{id:'f1'}}), "
            f"(u)-[:FRIEND]->(:{L}F {{id:'f2'}}), (u)-[:FRIEND]->(:{L}F {{id:'f3'}})"
        )
        q = f"MATCH (:{L})-[:FRIEND]->(n) WITH collect(n) AS friends DETACH DELETE friends[$friendIndex]"
        self.engine.execute_cypher(q, {"friendIndex": 0})
        assert self._count(f"{L}F") == 2
        self.engine.execute_cypher(q, {"friendIndex": 5})
        assert self._count(f"{L}F") == 2, "the second run deleted by the first run's index"
        assert writes_cypher(q) is True

    def test_a_clipped_range_answers_the_same_every_run(self):
        q = "UNWIND range(1, 500) AS i WITH i SKIP 2 LIMIT 3 RETURN i"
        for _ in range(3):
            assert [r[0] for r in self.engine.execute_cypher(q, {}).rows] == [3, 4, 5]

    def test_a_merged_path_is_found_by_the_next_run(self):
        """The second run's MERGE p finds the path the first created: one path, both runs return it."""
        L = self.L
        q = f"MERGE (a:{L} {{num: 1}}) MERGE (b:{L} {{num: 2}}) MERGE p = (a)-[:R]->(b) RETURN length(p) AS n"
        assert [r[0] for r in self.engine.execute_cypher(q, {}).rows] == [1]
        assert [r[0] for r in self.engine.execute_cypher(q, {}).rows] == [1]
        rels = self.engine.execute_cypher(f"MATCH (:{L})-[r:R]->(:{L}) RETURN count(r) AS c").rows
        assert rels == [[1]]
