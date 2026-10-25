"""Shared helpers for the spec 259 profiling scripts (SNB1B on ivg-iris-enterprise, read only)."""

import json
import logging
import statistics

import iris

from iris_vector_graph.engine import IRISGraphEngine
from scripts.bench.alt.snb_queries import QUERIES

logging.getLogger("iris_vector_graph").setLevel(logging.ERROR)
PARAMS = "/Users/tdyar/ws/iris-vector-graph/docs/performance/alt-bench/results/aws-native-sf1-6/query_ivg_native.json"
FOUR = ["BI04_top_forums_by_country", "BI14_international_dialog", "BI18_friend_recommendation", "BI11_friend_triangles"]


def connect():
    conn = iris.connect("localhost", 31972, "SNB1B", "_SYSTEM", "SYS")
    return conn, iris.createIRIS(conn)


def sets():
    return json.load(open(PARAMS))["params"]


def x(native, code):
    """Run ObjectScript in the server process; the code sets tOut."""
    return str(native.classMethodValue("ZIVGP.T", "X", code))


def engine(conn, parallel_min=None):
    eng = IRISGraphEngine(conn)
    eng.native_route_ms = 0
    if parallel_min is not None:
        eng.native_parallel_min = parallel_min
    return eng


def capture_plan(eng, name, params):
    """The plan JSON the engine would send for this query."""
    cap = []
    orig = eng._run_native_plan

    def spy(plan, p):
        cap.append((plan, p))
        return orig(plan, p)

    eng._run_native_plan = spy
    q = QUERIES[name]
    eng.execute_cypher(q.get("ivg", q["cypher"]), params)
    eng._run_native_plan = orig
    return cap[-1]


def spread(v):
    v = sorted(v)
    return f"min {v[0]:.2f} med {statistics.median(v):.2f} max {v[-1]:.2f} (n={len(v)})"


def plan_only(name, params):
    """The plan JSON for a query without running it (no columns: only the executor reads it here)."""
    from iris_vector_graph.cypher import native_plan
    from iris_vector_graph.cypher.parser import parse_query

    q = QUERIES[name]
    plan = native_plan.plan_query(parse_query(q.get("ivg", q["cypher"])), params)
    return dict(plan, parallel_min=-1, v=native_plan.PLAN_VERSION), params
