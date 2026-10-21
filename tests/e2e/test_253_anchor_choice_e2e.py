"""Spec 253: where a chain can start at either end, the executor starts where the work is least, not at the smaller
label.

The smaller label was the rule. It lost where the other end counts its matches per anchor (BI_forum_members: from 1.5k
persons, every membership is a group update; from 13k forums, one per forum: 234 ms against 94 at SF0.1), and where
the far node carries the filters (BI02: from tags, every tagged message's date is read; from posts, each post's once:
140 against 105 ms). The executor estimates each start's work from a sample of its members (the anchor's filters'
pass rate, the first hop's fan-out, the far node's filters, whether matches are counted per anchor) and reports the
label it scanned. Every start gives the same answer: shadow mode holds each to the SQL path's.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from iris_vector_graph.cypher.native_plan import PLAN_VERSION, plan_query
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
G = f"ac{T}"
PERSON, FORUM, TAG, POST, COMMENT, PLACE = (f"Ac{x}{T}" for x in ("Person", "Forum", "Tag", "Post", "Comment", "Place"))
MEMBER, TAGGED, IN = f"AC_MEMBER{T}", f"AC_TAG{T}", f"AC_IN{T}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    node = lambda i, label, **p: e.create_node(f"{G}-{i}", labels=[label], properties={"sid": f"{G}-{i}", **p}, graph=G)  # noqa: E731
    edge = lambda a, t, b: e.create_edge(f"{G}-{a}", t, f"{G}-{b}", graph=G)  # noqa: E731
    # 5 persons, each a member of all 60 forums: the smaller label is the costlier start
    for i in range(5):
        node(f"p{i}", PERSON)
    for i in range(60):
        node(f"f{i}", FORUM, title=f"forum {i % 7}")
        for j in range(5):
            edge(f"f{i}", MEMBER, f"p{j}")
    # 10 tags on 100 dated posts and 400 comments: starting at the tags reads every tagged message
    for i in range(10):
        node(f"t{i}", TAG, name=f"tag {i}")
    for i in range(3):
        node(f"c{i}", PLACE, name=f"place {i}")
    for i in range(100):
        node(f"m{i}", POST, d=f"201{i % 4}-06-01")
        edge(f"m{i}", TAGGED, f"t{i % 10}")
        edge(f"m{i}", IN, f"c{i % 3}")
    for i in range(400):
        node(f"k{i}", COMMENT, d=f"201{i % 4}-06-01")
        edge(f"k{i}", TAGGED, f"t{i % 10}")
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    e.erase_graph(G)


CASES = [
    (
        "per-anchor counts beat the smaller label",
        f"USE GRAPH '{G}' MATCH (f:{FORUM})-[:{MEMBER}]->(p:{PERSON}) RETURN f.title AS t, count(p) AS n ORDER BY n DESC, t",
        {},
        FORUM,
    ),
    (
        "an untyped hop is costed by every type",
        f"USE GRAPH '{G}' MATCH (f:{FORUM})-[]->(p:{PERSON}) RETURN f.title AS t, count(p) AS n ORDER BY n DESC, t",
        {},
        FORUM,
    ),
    (
        "the filtered end beats the smaller label",
        f"USE GRAPH '{G}' MATCH (t:{TAG})<-[:{TAGGED}]-(m:{POST}) WHERE m.d > $a AND m.d < $b RETURN t.name AS tag, count(m) AS n ORDER BY n DESC, tag",
        {"a": "2010-01-01", "b": "2012-01-01"},
        POST,
    ),
    (
        "the smaller label where it is the cheaper start",
        f"USE GRAPH '{G}' MATCH (m:{POST})-[:{IN}]->(c:{PLACE}) RETURN c.name AS c, count(m) AS n ORDER BY c",
        {},
        PLACE,
    ),
]


@pytest.mark.parametrize("name,cypher,params,expected", CASES, ids=[c[0] for c in CASES])
def test_the_executor_starts_where_the_work_is_least(engine, name, cypher, params, expected):
    parsed = parse_query(cypher)
    plan = plan_query(parsed, params)
    assert plan is not None and len(plan["alts"]) > 1
    sql_query = engine._native_translation(parsed, params)
    plan.update(columns=[a for a in sql_query.select_aliases if not a.startswith("__")], parallel_min=-1, v=PLAN_VERSION)
    head = json.loads(str(engine._iris_obj().classMethodValue("Graph.KG.Cypher.Exec", "Run", json.dumps(plan), json.dumps(params))))
    assert head.get("anchor") == expected


@pytest.mark.parametrize("name,cypher,params,expected", CASES, ids=[c[0] for c in CASES])
def test_the_chosen_start_answers_as_sql(engine, name, cypher, params, expected):
    before = engine.__dict__.get("_native_answers", 0)
    r = engine.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert engine.__dict__.get("_native_answers", 0) == before + 1
    assert r.rows
