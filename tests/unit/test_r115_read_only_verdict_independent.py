"""R115: the read-only verdict does not depend on what execution does to the parse cache.

`writes_cypher` (the rule `execute_cypher(read_only=True)` and every server route apply) read the tree
`parse_query` caches, and execution reads that same tree and, in places, changed it. R113 found one change
(`subsequent_queries = []`, after which a chained `CREATE` was judged a read) and fixed it with a copy. The
verdict now comes from a parse of its own, kept as a boolean per text, so no execution path can reach it.

* each write shape from R113's table, and one shape per execution path (chained parts, `;` split,
  `plan_row_merge`, `plan_count_create`, `_execute_parsed`), run with `read_only=False` through every path,
  is still a write, and `execute_cypher(..., read_only=True)` still refuses it and runs nothing;
* a cached tree changed by hand (every write removed, or one added) does not move the verdict;
* a property: over seeded random sequences of executions, `writes_cypher(text)` gives the table's verdict
  before, between and after every run.

The changes the translator itself made to the cached tree are in test_r115_translation_leaves_the_tree.py.
No IRIS except `TestLiveIRIS`, which runs the paths against a namespace and checks what they stored.
"""
from __future__ import annotations

import os
import random
import re
import uuid
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher import read_only, writes_cypher
from iris_vector_graph.cypher import ast as cast
from iris_vector_graph.cypher.count_create import plan_count_create
from iris_vector_graph.cypher.merge_rows import plan_row_merge
from iris_vector_graph.cypher.parser import _parse_query_cached, parse_query
from iris_vector_graph.cypher.read_only import split_statements
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult
from tests.unit.test_r113_read_only_one_rule import CASES, REFUSED

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

# shapes that take each execution path; the planners are asserted below to accept them
CHAINED = "MATCH (n) RETURN n.id AS id MATCH (m) CREATE (x:Probe {id:'c'}) RETURN x"
SEMICOLON = "CALL db.labels(); CREATE (x:Probe {id:'p'}) RETURN x"
ROW_MERGE = {
    "MERGE on a WITH value": "MATCH (n:Probe) WITH n.x AS x MERGE (:ProbeN {x: x})",
    "MERGE on a MATCH property": "MATCH (p:Probe) MERGE (:ProbeCity {name: p.bornIn})",
    "MERGE after DELETE": "MATCH (a:Probe) DELETE a MERGE (b:Probe)",
}
COUNT_CREATE = {
    "MATCH then CREATE": "MATCH (a:Probe) CREATE (:ProbeC)",
    "CREATE, WITH *, MATCH, CREATE": "MATCH (a:Probe) CREATE (:ProbeC) WITH * MATCH (b:Probe) CREATE (:ProbeD)",
}
# the shapes whose translation changed the cached tree (before R115)
TREE_CHANGING = {
    "DELETE collect(n)[$i]": "MATCH (:User)-[:FRIEND]->(n) WITH collect(n) AS friends DETACH DELETE friends[$friendIndex]",
    "DELETE collect(r)[$i]": (
        "MATCH (:User)-[r:FRIEND]->() WITH collect(r) AS friendships DETACH DELETE friendships[$friendIndex]"),
    "DELETE map key": "MATCH (:User)-[r]->(:User) WITH {key: r} AS rels DELETE rels.key",
    "DELETE collected paths": (
        "MATCH p = (:User)-[r]->(:User) WITH {key: collect(p)} AS pathColls "
        "DELETE pathColls.key[0], pathColls.key[1]"),
    "MERGE p = path": "MERGE (a:L {num: 1}) MERGE (b:L {num: 2}) MERGE p = (a)-[:R]->(b) RETURN p",
    "UNWIND range WITH SKIP LIMIT": "UNWIND range(1, 500) AS i WITH i SKIP 2 LIMIT 3 RETURN i",
    "UNWIND range WITH LIMIT, then CREATE": "UNWIND range(1, 500) AS i WITH i LIMIT 3 CREATE (:Probe {i: i})",
}

WRITES = {name: q for name, (q, verdict) in CASES.items() if verdict is REFUSED}
WRITES.update({"chained parts": CHAINED, "; split": SEMICOLON})
WRITES.update(ROW_MERGE)
WRITES.update(COUNT_CREATE)
WRITES.update({k: v for k, v in TREE_CHANGING.items() if "RETURN i" not in v})
assert "does not parse" in WRITES  # R113's table is the source; a rename there must not empty this one

VERDICTS = {name: (q, verdict) for name, (q, verdict) in CASES.items()}
VERDICTS.update({name: (q, REFUSED) for name, q in WRITES.items()})
VERDICTS["UNWIND range WITH SKIP LIMIT"] = (TREE_CHANGING["UNWIND range WITH SKIP LIMIT"], False)


def _params(q):
    return {name: 1 for name in re.findall(r"\$(\w+)", q)}


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Every test starts with nothing cached, and leaves nothing it changed by hand behind."""
    def clear():
        _parse_query_cached.cache_clear()
        read_only._text_writes.cache_clear()

    clear()
    yield
    clear()


def _engine():
    """An engine whose own code all runs (planners, translator, _execute_parsed); only the store is a stub."""
    from iris_vector_graph.engine import IRISGraphEngine

    store = MagicMock(name="store")
    store.capabilities.return_value = {"native_sql": True}
    store.execute_sql.side_effect = lambda *a, **k: IVGResult(columns=["c"], rows=[[1]])
    store.execute_transaction.side_effect = lambda *a, **k: IVGResult(columns=["c"], rows=[[1]])
    conn = MagicMock(name="conn")
    conn.cursor.return_value.fetchall.return_value = []
    conn.cursor.return_value.fetchone.return_value = (0,)
    eng = IRISGraphEngine(conn, embedding_dimension=4, store=store)
    eng.native_cypher = False
    return eng, store, conn


# ---------------------------------------------------------------------------
# execution paths, each run with read_only=False
# ---------------------------------------------------------------------------

def _run_execute_cypher(eng, q, params):
    eng.execute_cypher(q, params)


def _run_execute_parsed(eng, q, params):
    for statement in split_statements(q):
        eng._execute_parsed(parse_query(statement), params)


def _run_chained_parts(eng, q, params):
    tree = parse_query(q)
    for part in [tree] + list(tree.subsequent_queries):
        eng._execute_parsed(part, params)


def _run_row_merge(eng, q, params):
    plan = plan_row_merge(parse_query(q), params)
    if plan is not None:
        eng._execute_row_merge(plan, params)


def _run_count_create(eng, q, params):
    steps = plan_count_create(parse_query(q), params)
    if steps is not None:
        eng._execute_count_create(steps, params)


def _run_translate(eng, q, params):
    for statement in split_statements(q):
        translate_to_sql(parse_query(statement), params, engine=eng)


PATHS = {
    "execute_cypher": _run_execute_cypher,
    "_execute_parsed": _run_execute_parsed,
    "subsequent_queries": _run_chained_parts,
    "plan_row_merge": _run_row_merge,
    "plan_count_create": _run_count_create,
    "translate_to_sql": _run_translate,
}


def _run(path, eng, q, params):
    """Run one path. A shape a path cannot handle may raise (the text that does not parse raises on every
    path): what is checked is the verdict afterwards, whatever the run did."""
    try:
        PATHS[path](eng, q, params)
    except Exception as ex:  # noqa: BLE001 - recorded, not hidden: the caller asserts on the verdict
        return ex
    return None


def test_each_path_shape_takes_its_path():
    """The shapes named for a path really take it, so the tests below exercise what they claim to."""
    assert parse_query(CHAINED).subsequent_queries
    assert len(split_statements(SEMICOLON)) == 2
    for name, q in ROW_MERGE.items():
        assert plan_row_merge(parse_query(q), {}) is not None, name
    for name, q in COUNT_CREATE.items():
        assert plan_count_create(parse_query(q), {}) is not None, name


@pytest.mark.parametrize("path", list(PATHS))
@pytest.mark.parametrize("query", list(WRITES.values()), ids=list(WRITES))
def test_a_write_run_through_any_path_is_still_a_write(query, path):
    eng, store, conn = _engine()
    assert writes_cypher(query) is True
    _run(path, eng, query, _params(query))
    _run(path, eng, query, _params(query))  # twice: a change the first run made shows in the second
    assert writes_cypher(query) is True, f"after {path}, writes_cypher called a write a read"
    store.reset_mock()
    conn.reset_mock()
    with pytest.raises(PermissionError, match="Read-only mode"):
        eng.execute_cypher(query, _params(query), read_only=True)
    assert not store.execute_sql.called and not store.execute_transaction.called
    assert not conn.cursor.called


@pytest.mark.parametrize("path", list(PATHS))
@pytest.mark.parametrize("query", list(WRITES.values()), ids=list(WRITES))
def test_a_write_run_first_then_judged_is_a_write(query, path):
    """The same, with no verdict asked before the run: the first verdict comes after execution."""
    eng, _store, _conn = _engine()
    _run(path, eng, query, _params(query))
    assert writes_cypher(query) is True, f"after {path}, writes_cypher called a write a read"
    with pytest.raises(PermissionError, match="Read-only mode"):
        eng.execute_cypher(query, _params(query), read_only=True)


# ---------------------------------------------------------------------------
# the verdict never reads the parse cache
# ---------------------------------------------------------------------------

def _gut(tree):
    """What a careless execution path could do: empty every place a write clause can sit."""
    tree.query_parts = [cast.QueryPart(clauses=[], with_clause=None)]
    tree.union_queries = []
    tree.subsequent_queries = []


PARSEABLE_WRITES = {
    name: q for name, q in WRITES.items()
    if name not in ("does not parse", "not Cypher at all")
}


@pytest.mark.parametrize("query", list(PARSEABLE_WRITES.values()), ids=list(PARSEABLE_WRITES))
def test_a_gutted_cached_tree_does_not_make_a_write_a_read(query):
    for statement in split_statements(query):
        try:
            _gut(parse_query(statement))
        except Exception:  # noqa: BLE001 - a statement execute_cypher answers without the parser has no tree
            continue
    assert writes_cypher(query) is True
    eng, store, _conn = _engine()
    with pytest.raises(PermissionError, match="Read-only mode"):
        eng.execute_cypher(query, _params(query), read_only=True)
    assert not store.execute_sql.called and not store.execute_transaction.called


READS = {name: q for name, (q, verdict) in CASES.items() if verdict is not REFUSED}


@pytest.mark.parametrize("query", list(READS.values()), ids=list(READS))
def test_a_write_added_to_a_cached_tree_does_not_make_a_read_a_write(query):
    changed = []
    for statement in split_statements(query):
        try:
            tree = parse_query(statement)
        except Exception:  # noqa: BLE001 - SHOW, EXPLAIN and Neo4j Browser's metadata query do not parse here
            continue
        create = cast.CreateClause(patterns=[cast.GraphPattern(nodes=[cast.NodePattern(variable="x")])])
        if not tree.query_parts:  # a bare CALL: the procedure is not a query part
            tree.query_parts.append(cast.QueryPart(clauses=[]))
        tree.query_parts[-1].clauses.append(create)
        changed.append(tree)
    assert all(t.writes for t in changed), "the change did not make the cached tree a write"
    assert writes_cypher(query) is False


# ---------------------------------------------------------------------------
# property: the verdict is the same before, between and after any executions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(8))
def test_the_verdict_is_the_same_before_and_after_any_execution(seed):
    rng = random.Random(seed)
    eng, _store, _conn = _engine()
    names = list(VERDICTS)
    rng.shuffle(names)
    for name in names:
        query, verdict = VERDICTS[name]
        if rng.random() < 0.5:
            assert writes_cypher(query) is verdict, (name, "before")
        for _ in range(rng.randint(1, 4)):
            path = rng.choice(list(PATHS))
            params = {k: rng.randint(0, 3) for k in _params(query)}
            _run(path, eng, query, params)
            assert writes_cypher(query) is verdict, (name, path, params)


# ---------------------------------------------------------------------------
# live: the paths that need IRIS, and what they store
# ---------------------------------------------------------------------------

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
        for suffix in ("", "N", "City", "C", "D", "F"):
            self.engine.execute_cypher(f"MATCH (n:{self.L}{suffix}) DETACH DELETE n")

    def _count(self, label):
        return self.engine.execute_cypher(f"MATCH (n:{label}) RETURN count(n) AS c").rows[0][0]

    def _labels(self):
        return {s: self._count(f"{self.L}{s}") for s in ("", "N", "City", "C", "D", "F")}

    def _shapes(self):
        L = self.L
        return {
            "chained parts": f"MATCH (n:{L}) RETURN n.id AS id MATCH (m:{L}) CREATE (x:{L}C {{id:'c'}}) RETURN x",
            "; split": f"CALL db.labels(); CREATE (x:{L}C {{id:'p'}}) RETURN x",
            "plan_row_merge (WITH value)": f"MATCH (n:{L}) WITH n.x AS x MERGE (:{L}N {{x: x}})",
            "plan_row_merge (MATCH property)": f"MATCH (p:{L}) MERGE (:{L}City {{name: p.bornIn}})",
            "plan_count_create": f"MATCH (a:{L}) CREATE (:{L}C) WITH * MATCH (b:{L}) CREATE (:{L}D)",
        }

    def test_each_path_runs_then_stays_refused(self):
        L = self.L
        self.engine.execute_cypher(f"CREATE (:{L} {{id:'a', x: 1, bornIn: 'Rome'}}), (:{L} {{id:'b', x: 2, bornIn: 'Oslo'}})")
        shapes = self._shapes()
        assert plan_row_merge(parse_query(shapes["plan_row_merge (WITH value)"]), {}) is not None
        assert plan_count_create(parse_query(shapes["plan_count_create"]), {}) is not None
        for name, q in shapes.items():
            before = self._labels()
            result = self.engine.execute_cypher(q, {})
            assert not getattr(result, "error", None), (name, result.error)
            assert self._labels() != before, f"{name} wrote nothing, so it did not test its path"
            assert writes_cypher(q) is True, name
            stored = self._labels()
            with pytest.raises(PermissionError, match="Read-only mode"):
                self.engine.execute_cypher(q, {}, read_only=True)
            assert self._labels() == stored, f"{name}: the refused run wrote"
        # what the paths stored: two matched rows, each CREATE once per row
        assert self._count(f"{L}N") == 2 and self._count(f"{L}City") == 2
        assert self._count(f"{L}D") == 2 * 2  # 2 rows, times 2 matches of b: CREATE ran once per row
