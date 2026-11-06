"""4.6.0 R308, live: every read IVG can answer two ways is answered both ways, and the answers agree.

IVG answers a read-only Cypher query through the native executor over ^KG (`native_cypher=True`, when the planner
takes the shape) or the SQL translation, and inside the SQL path through its own routes: the variable-length BFS routes
(an id-bound start, a property map SQL does not check, path functions), and the edge scan (`IVG_EDGESCAN=1`). Until
this file a disagreement between them was found only by a person reading two answers. Here the same query runs through
each route:

- `native`: native_cypher=True and native_route_ms=0 (no timing-based switch to SQL);
- `sql`: native_cypher=False;
- `edgescan`: native_cypher=False with IVG_EDGESCAN=1;

and, for a query whose `forms` are other spellings of it (a property map written as a WHERE, which takes another
route), each form through `native` and `sql`. Every answer is compared with every other and with the answer of
tests/oracle/cypher_gen.py's reference interpreter, which works out openCypher's answer over the same graph without
IVG. The hand cases (`cypher_gen.hand_cases()`, at least 40) compare with answers worked by hand.

What the comparison normalises, and only that: the documented representation of a list or map in a result row (JSON
text, docs/USER_GUIDE.md, "A list in a result row comes back as JSON text") is decoded where the reference answer is a
list or map, or the column is one whose order openCypher leaves open (`collect`, `labels`), which compares as a
multiset; rows compare as a multiset unless the RETURN orders them (by every column). Types are kept apart: the string
'1', the integer 1 and the float 1.0 differ.

The graph: `cypher_gen.make_graph(seed)` (ids `o308:*`, labels O308A/O308B, types O308R/O308S, named graph o308g) and
`cypher_gen.hand_graph()` (ids `h308:*`, named graph h308g). Nodes are written by `create_node` with their string
properties and then a Cypher SET of the others, so a number reads as the number (docs/KNOWN_ISSUES.md: a number
`create_node` writes reads back as text); relationships by `create_edge` with their properties as qualifiers. Both are
removed before and after.

The namespace is `IVG_NS_IVG460FXOC` (default IVG460FXOC, an IVG460FX* scratch namespace with IVG set up:
`scripts/dev/make_scratch_namespace.py IVG460FXOC`) on `IVG_TEST_CONTAINER` at `IVG_PORT`. Seeds `IVG_R308_SEEDS`
(default 1,2,3), queries per seed `IVG_R308_COUNT` (default 260). Every disagreement is written to
`IVG_R308_REPORT` (default ~/.cache/ivg-bench/oracle-cypher/r308-<driver>.json) with the query, its seed and every
answer, and the test fails naming them. Run it under each driver (5.4.0 and 5.4.1):

    IVG_NS_IVG460FXOC=IVG460FXOC pytest tests/e2e/test_460_r308_cypher_differential_e2e.py
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tests.e2e import ns262
from tests.oracle import cypher_gen as cg

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_NS_IVG460FXOC", "IVG460FXOC")
SEEDS = [int(s) for s in os.environ.get("IVG_R308_SEEDS", "1,2,3").split(",") if s.strip()]
COUNT = int(os.environ.get("IVG_R308_COUNT", "260"))
ROUTES = ("native", "sql", "edgescan")


def _driver() -> str:
    import iris

    for name in ("intersystems-irispython", "intersystems_irispython"):
        try:
            from importlib.metadata import version

            v = version(name)
            if v:
                return v
        except Exception:  # metadata missing: the module's own attribute below
            continue
    return str(getattr(iris, "__version__", "unknown"))


DRIVER = _driver()
REPORT = Path(os.environ.get("IVG_R308_REPORT", str(Path.home() / f".cache/ivg-bench/oracle-cypher/r308-{DRIVER}.json")))


@dataclass
class Answer:
    rows: Optional[List[tuple]]
    error: Optional[str]
    native: bool
    columns: List[str] = field(default_factory=list)

    def show(self) -> Dict[str, Any]:
        return {"rows": self.rows, "error": self.error, "native": self.native, "columns": self.columns}


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    try:
        conn = iris.connect("localhost", ns262.PORT, NS, "_SYSTEM", "SYS")
    except Exception as ex:
        pytest.skip(f"namespace {NS} is not reachable ({ex}): make it with scripts/dev/make_scratch_namespace.py {NS}")
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    native = IRISGraphEngine(conn, namespace=NS, schema_prefix=schema, native_cypher=True, native_route_ms=0)
    sql = IRISGraphEngine(conn, namespace=NS, schema_prefix=schema, native_cypher=False)
    gate = cg_native_gate(native)
    assert gate is None, f"the native executor cannot run in {NS}: {gate}"
    yield {"native": native, "sql": sql, "conn": conn}
    conn.close()


def cg_native_gate(engine):
    from iris_vector_graph.cypher import native_plan

    return native_plan.native_gate(engine)


def _clean(engine, g: cg.Graph) -> None:
    engine.delete_nodes_by_prefix(g.id_prefix, graph=None)
    engine.delete_nodes_by_prefix(g.id_prefix, graph=g.named)


def load(engine, g: cg.Graph) -> None:
    """The graph, written as the module docstring says; stored values keep their form."""
    _clean(engine, g)
    for n in g.nodes:
        strings = {k: v for k, v in n.props.items() if isinstance(v, str)}
        assert engine.create_node(n.id, labels=list(n.labels), properties=strings, graph=n.graph or None), n.id
        typed = {k: v for k, v in n.props.items() if not isinstance(v, str)}
        if typed:
            sets = ", ".join(f"n.{k} = {cg.cy_text(v)}" for k, v in typed.items())
            use = f"USE GRAPH {n.graph} " if n.graph else ""
            engine.execute_cypher(f"{use}MATCH (n) WHERE n.node_id = $id SET {sets}", {"id": n.id},
                                  raise_on_error=True)
    for e in g.edges:
        assert engine.create_edge(e.src, e.type, e.dst, qualifiers=dict(e.props) or None, graph=e.graph or None), e


def run(engines, route: str, text: str, params: Dict[str, Any]) -> Answer:
    engine = engines["native" if route == "native" else "sql"]
    before = getattr(engine, "_native_answers", 0)
    old = os.environ.get("IVG_EDGESCAN")
    if route == "edgescan":
        os.environ["IVG_EDGESCAN"] = "1"
    try:
        r = engine.execute_cypher(text, dict(params))
    except Exception as ex:  # an exception is an answer of its own, compared like any other
        return Answer(None, f"{type(ex).__name__}: {str(ex)[:300]}", False)
    finally:
        if route == "edgescan":
            if old is None:
                os.environ.pop("IVG_EDGESCAN", None)
            else:
                os.environ["IVG_EDGESCAN"] = old
    native = getattr(engine, "_native_answers", 0) > before
    if r.error:
        return Answer(None, str(r.error)[:300], native, list(r.columns or []))
    return Answer([tuple(row) for row in r.rows], None, native, list(r.columns or []))


def _decode(v, like=None, bag=False):
    """A list or map in a row is JSON text (the documented representation): decoded where the reference holds a list
    or map, or where the column is a multiset."""
    want = isinstance(like, (list, dict)) or (bag and like is None)
    if isinstance(v, str) and want and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def normalise(rows: List[tuple], q: cg.Query, ref: Optional[List[tuple]]) -> List[tuple]:
    if rows is None:
        return None
    # a column the reference holds a list or map in, in any row (a null in the first row says nothing)
    shape = {i: v for r in (ref or []) for i, v in enumerate(r) if isinstance(v, (list, dict))}
    return [tuple(_decode(v, shape.get(i), i in q.bag_cols) for i, v in enumerate(row)) for row in rows]


def same(a: Answer, b: Answer, q: cg.Query, ref) -> bool:
    if a.rows is None or b.rows is None:
        return a.rows is None and b.rows is None
    return cg.same_rows(normalise(a.rows, q, ref), normalise(b.rows, q, ref), q)


def matches_ref(a: Answer, q: cg.Query, ref) -> bool:
    return a.rows is not None and cg.same_rows(normalise(a.rows, q, ref), ref, q)


def check(engines, q: cg.Query, ref):
    """(None when every route and form agrees with the others and with `ref`, else what each answered; whether the
    native executor answered)."""
    answers = {route: run(engines, route, q.text, q.params) for route in ROUTES}
    for i, text in enumerate(q.forms):
        for route in ("native", "sql"):
            answers[f"form{i}:{route}"] = run(engines, route, text, q.params)
    problems = []
    first_key = next(iter(answers))
    first = answers[first_key]
    for key, a in answers.items():
        if key != first_key and not same(first, a, q, ref):
            problems.append(f"{key} differs from {first_key}")
    if ref is not None:
        for key, a in answers.items():
            if not matches_ref(a, q, ref):
                problems.append(f"{key} differs from the reference")
    elif all(a.error for a in answers.values()):
        problems.append("every route failed")
    native = answers["native"].native
    if not problems:
        return None, native
    return {
        "query": q.text, "params": q.params, "seed": q.seed, "index": q.index, "family": q.family,
        "forms": q.forms, "problems": problems, "reference": ref,
        "answers": {k: a.show() for k, a in answers.items()},
    }, native


def _write_report(name: str, payload: Dict[str, Any]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    data[name] = payload
    REPORT.write_text(json.dumps(data, indent=1, default=repr))


def _show(d: Dict[str, Any]) -> str:
    lines = [f"seed {d['seed']} #{d['index']} [{d['family']}] {d['query']}  params={d['params']}",
             f"  problems: {d['problems']}", f"  reference: {d['reference']}"]
    lines += [f"  {k}: {v['error'] or v['rows']}{' (native)' if v['native'] else ''}" for k, v in d["answers"].items()]
    lines += [f"  form{i}: {t}" for i, t in enumerate(d["forms"])]
    return "\n".join(lines)


# ---------------------------------------------------------------- the hand cases


@pytest.fixture(scope="module")
def hand(engines):
    g = cg.hand_graph()
    load(engines["sql"], g)
    yield g
    _clean(engines["sql"], g)


def test_the_hand_graph_reads_back_as_written(engines, hand):
    """Each node's properties, read by node id, are what load() wrote (worked from hand_graph's docstring)."""
    for n in hand.nodes:
        use = f"USE GRAPH {n.graph} " if n.graph else ""
        for route in ("native", "sql"):
            a = run(engines, route, f"{use}MATCH (n) WHERE n.node_id = $id RETURN properties(n) AS p", {"id": n.id})
            assert a.rows is not None and len(a.rows) == 1, (route, n.id, a)
            got = _decode(a.rows[0][0], {})
            assert cg.canon(got) == cg.canon(n.props), (route, n.id, got, n.props)


@pytest.mark.parametrize("case", cg.hand_cases(), ids=lambda c: c.name)
def test_every_route_gives_the_hand_answer(engines, hand, case):
    q = case.query
    answers = {route: run(engines, route, q.text, q.params) for route in ROUTES}
    wrong = {k: a.show() for k, a in answers.items() if not matches_ref(a, q, case.expected)}
    assert not wrong, f"{q.text} params={q.params}\n  hand answer: {case.expected}\n  " + "\n  ".join(
        f"{k}: {v}" for k, v in wrong.items())


# ---------------------------------------------------------------- the generated queries


@pytest.mark.parametrize("seed", SEEDS)
def test_every_route_agrees_over_generated_queries(engines, seed):
    g = cg.make_graph(seed)
    load(engines["sql"], g)
    started = time.time()
    found, native = [], 0
    queries = cg.generate(g, seed, COUNT)
    try:
        for q in queries:
            d, answered = check(engines, q, q.evaluate(g))
            native += answered
            if d is not None:
                found.append(d)
    finally:
        _clean(engines["sql"], g)
    _write_report(f"seed{seed}", {"driver": DRIVER, "queries": len(queries), "native_answered": native,
                                  "seconds": round(time.time() - started, 1), "disagreements": found})
    # not vacuous: the native executor answers a share of the queries itself
    assert native >= len(queries) // 10, f"the native executor answered {native} of {len(queries)}"
    assert not found, f"{len(found)} of {len(queries)} queries disagree (driver {DRIVER}, report {REPORT}):\n\n" + \
        "\n\n".join(_show(d) for d in found[:25])


# ---------------------------------------------------------------- shapes refused rather than answered wrongly

REFUSED = [
    "MATCH (a:H308A {name: 'cat'})-[r:H308R*1..2 {m: {k: a.kind}}]->(b) RETURN b.name",
]


@pytest.mark.parametrize("q", REFUSED)
def test_a_shape_the_statement_cannot_read_raises_on_every_route(engines, hand, q):
    """docs/KNOWN_ISSUES.md, "A variable-length property map whose value is an expression raises"."""
    for route in ROUTES:
        a = run(engines, route, q, {})
        assert a.rows is None and a.error and a.error.startswith("ValueError"), (route, a)


@pytest.mark.parametrize("q, expected", [
    # once refused (the labeled routes answered rows of the wrong width, the start nulled), worked by hand:
    # cat -R-> dan -R-> eve, and both of those relationships hold m {k: 1}
    ("MATCH (a:H308A {name: 'cat'}) OPTIONAL MATCH (a)-[:H308R*1..2]->(b) WHERE b.name = 'eve' RETURN a.name, b.name",
     [("cat", "eve")]),
    ("MATCH (a:H308A {name: 'cat'}) OPTIONAL MATCH (a)-[:H308R*1..2]->(b {name: 'eve'}) RETURN a.name, b.name",
     [("cat", "eve")]),
    ("MATCH (a:H308A {name: 'cat'}) OPTIONAL MATCH (a)-[r:H308R*1..2 {m: {k: 1}}]->(b {name: 'dan'}) "
     "RETURN a.name, b.name", [("cat", "dan")]),
    ("MATCH (a:H308A {name: 'cat'}) OPTIONAL MATCH (a)-[:H308R*1..2]->(b) WHERE b.name = 'zed' RETURN a.name, b.name",
     [("cat", None)]),
])
def test_an_optional_hop_from_a_bound_start_answers_on_every_route(engines, hand, q, expected):
    query = cg.Query(q, {}, False, frozenset(), "optional", [], lambda g: expected)
    for route in ROUTES:
        a = run(engines, route, q, {})
        assert matches_ref(a, query, expected), (route, a)


@pytest.mark.parametrize("q, expected", [
    # the KNOWN_ISSUES workarounds, worked by hand: cat -R-> dan -R-> eve
    ("MATCH (a:H308A {name: 'cat'}) OPTIONAL MATCH (a)-[:H308R*1..2]->(b) WITH a, b WHERE b.name = 'eve' "
     "RETURN a.name, b.name", [("cat", "eve")]),
    ("MATCH (a:H308A {name: 'cat'}) OPTIONAL MATCH (a)-[:H308R*1..2]->(b) "
     "RETURN a.name, [x IN collect(b) WHERE x.name = 'eve' | x.name] AS bs", [("cat", ["eve"])]),
    ("MATCH (a:H308A {name: 'cat'}) OPTIONAL MATCH (a)-[:H308R*1..2]->(b) "
     "RETURN a.name, [x IN collect(b) WHERE x.name = 'zed' | x.name] AS bs", [("cat", [])]),
])
def test_the_documented_rewrites_answer(engines, hand, q, expected):
    query = cg.Query(q, {}, False, frozenset(), "rewrite", [], lambda g: expected)
    for route in ROUTES:
        a = run(engines, route, q, {})
        assert matches_ref(a, query, expected), (route, a)
