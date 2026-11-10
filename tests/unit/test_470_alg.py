"""4.7.0 rows 8 and 28, offline.

Row 8: ``CALL ivg.ppr`` and the Python PPR methods took any alpha and maxIter (``CALL ivg.ppr(['a'], 2.0, 5)``
answered b 22.0 and a -21.0). Alpha outside the open interval (0, 1) and maxIter below 1 now raise ``ValueError``
naming the argument, before any database call: the stand-in engine here has no connection, so a check that came after
one would fail on that instead.

Row 28: ``leiden_communities(top_k=1)`` answered a whole community when a route kept communities whole. The engine
cuts every route's rows at ``top_k``; the stand-in store answers as such a route did.

tests/e2e/test_470_ppr_args_e2e.py and test_470_leiden_topk_e2e.py check the same against IRIS.
"""

from __future__ import annotations

import pytest

from iris_vector_graph._engine.algorithms import AlgorithmsMixin
from iris_vector_graph._engine.fhir_graph import FhirGraphMixin
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult


def _translate(query):
    return translate_to_sql(parse_query(query), {})


@pytest.mark.parametrize("args, named", [
    ("2.0, 5", "alpha"), ("0.0, 20", "alpha"), ("1.0, 20", "alpha"), ("-0.5, 20", "alpha"), ("1, 20", "alpha"),
    ("0.85, 0", "maxIter"), ("0.85, -1", "maxIter"),
])
def test_ivg_ppr_refuses_out_of_range(args, named):
    with pytest.raises(ValueError, match=rf"^ivg\.ppr: {named} must be"):
        _translate(f"CALL ivg.ppr(['a'], {args}) YIELD node, score RETURN node, score")


@pytest.mark.parametrize("args", ["0.5, 5", "0.85, 1", "0.000001, 20", "0.999999, 20", "0.5", ""])
def test_ivg_ppr_takes_alpha_inside_the_interval(args):
    sep = ", " if args else ""
    assert _translate(f"CALL ivg.ppr(['a']{sep}{args}) YIELD node, score RETURN node, score").sql


class _NoDatabase:
    """An engine with no connection and no store: any attribute read is a database step the check should precede."""

    def __getattr__(self, name):
        raise AssertionError(f"read {name!r} before the argument check")


@pytest.mark.parametrize("kw, named", [
    ({"damping_factor": 2.0}, "damping_factor"), ({"damping_factor": 0.0}, "damping_factor"),
    ({"damping_factor": 1.0}, "damping_factor"), ({"damping_factor": -0.2}, "damping_factor"),
    ({"max_iterations": 0}, "max_iterations"), ({"max_iterations": -5}, "max_iterations"),
])
def test_kg_personalized_pagerank_refuses_out_of_range(kw, named):
    with pytest.raises(ValueError, match=rf"^kg_PERSONALIZED_PAGERANK: {named} must be"):
        AlgorithmsMixin.kg_PERSONALIZED_PAGERANK(_NoDatabase(), ["a"], **kw)


@pytest.mark.parametrize("kw, named", [
    ({"alpha": 2.0}, "alpha"), ({"alpha": 0}, "alpha"), ({"alpha": 1}, "alpha"), ({"max_iter": 0}, "max_iter"),
])
def test_ppr_refuses_out_of_range(kw, named):
    with pytest.raises(ValueError, match=rf"^ppr: {named} must be"):
        AlgorithmsMixin.ppr(_NoDatabase(), "a", **kw)


@pytest.mark.parametrize("kw, named", [({"damping": 2.0}, "damping"), ({"max_iterations": 0}, "max_iterations")])
@pytest.mark.parametrize("method", ["kg_PPR", "kg_PPR_GUIDED_SUBGRAPH"])
def test_kg_ppr_forms_refuse_out_of_range(method, kw, named):
    with pytest.raises(ValueError, match=rf"^{method}: {named} must be"):
        getattr(AlgorithmsMixin, method)(_NoDatabase(), ["a"], **kw)


@pytest.mark.parametrize("kw, named", [({"damping_factor": 2.0}, "damping_factor"),
                                       ({"max_iterations": 0}, "max_iterations")])
@pytest.mark.parametrize("group_by", [None, "patient"])
def test_fhir_concept_ppr_refuses_out_of_range(kw, named, group_by):
    """Both its routes: kg_PERSONALIZED_PAGERANK, and Graph.KG.FHIRGraph.GroupPPR for group_by='patient'."""
    with pytest.raises(ValueError, match=rf"^fhir_concept_ppr: {named} must be"):
        FhirGraphMixin.fhir_concept_ppr(_NoDatabase(), "fhir", None, ["c"], group_by=group_by, **kw)


@pytest.mark.parametrize("value", [True, "0.5", None])
def test_alpha_must_be_a_number(value):
    with pytest.raises(ValueError, match=r"^ppr: alpha must be a number"):
        AlgorithmsMixin.ppr(_NoDatabase(), "a", alpha=value)


# ------------------------------------------------------------------------------------------------ row 9


class _NodeTable:
    """A connection whose node table holds ``ids`` in index order; records each statement it is sent."""

    def __init__(self, ids):
        self.ids, self.sent = ids, []

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.sent.append((sql, params))
        top = sql.split("TOP ", 1)[1].split()[0] if "TOP " in sql else None
        self.rows = [(v,) for v in self.ids[: int(top) if top else None]]

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _EveryNodeEngine(AlgorithmsMixin):
    def __init__(self, ids):
        self.conn = _NodeTable(ids)

    def _t(self, name):
        return f"Graph_KG.{name}"


#: a million-node graph's first IDs in index order; the walk scored x and n3
IDS = [f"n{i}" for i in range(10)]


def test_a_limit_reads_only_the_rows_it_can_use():
    """top_k 3, 2 scored: at most 3 + 2 IDs are read, in index order, and the zeros are the first unscored ones."""
    eng = _EveryNodeEngine(IDS)
    out = eng._ppr_every_node({"x": 0.6, "n3": 0.4}, "", 3, None)
    assert out == {"x": 0.6, "n3": 0.4, "n0": 0.0}
    assert eng.conn.sent == [("SELECT TOP 5 node_id FROM Graph_KG.nodes WHERE graph_id = ? ORDER BY node_id", [""])]


def test_the_cap_bounds_the_read_without_top_k():
    eng = _EveryNodeEngine(IDS)
    out = eng._ppr_every_node({"n0": 1.0}, "g", None, 4)
    assert out == {"n0": 1.0, "n1": 0.0, "n2": 0.0, "n3": 0.0}
    assert eng.conn.sent == [("SELECT TOP 5 node_id FROM Graph_KG.nodes WHERE graph_id = ? ORDER BY node_id", ["g"])]


def test_no_limit_reads_every_node():
    eng = _EveryNodeEngine(IDS)
    out = eng._ppr_every_node({"n9": 1.0}, "", None, None)
    assert out == {"n9": 1.0, **{f"n{i}": 0.0 for i in range(9)}}
    assert eng.conn.sent == [("SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ? ORDER BY node_id", [""])]


def test_a_full_answer_reads_nothing():
    eng = _EveryNodeEngine(IDS)
    assert eng._ppr_every_node({"a": 0.5, "b": 0.5}, "", 2, None) == {"a": 0.5, "b": 0.5}
    assert eng.conn.sent == []


# ------------------------------------------------------------------------------------------------ row 28


class _WholeCommunityStore:
    """A store whose Leiden route keeps communities whole, as LeidenJsonAuto did: three cliques of 4, 3 and 2."""

    def __init__(self):
        self.asked = None

    def capabilities(self):
        return {"leiden": True}

    def execute_leiden(self, max_levels, gamma, tol, top_k, *rest):
        self.asked = top_k
        rows = [[f"k0_{i}", 0, 4] for i in range(4)] + [[f"k1_{i}", 1, 3] for i in range(3)]
        rows += [[f"k2_{i}", 2, 2] for i in range(2)]
        if top_k <= 0:
            return IVGResult(columns=["id", "community", "size"], rows=rows)
        out, emitted = [], 0
        for c in range(3):
            members = [r for r in rows if r[1] == c]
            out += members
            emitted += len(members)
            if emitted >= top_k:
                break
        return IVGResult(columns=["id", "community", "size"], rows=out)


class _LeidenEngine(AlgorithmsMixin):
    def __init__(self):
        self._store = _WholeCommunityStore()

    def _loners(self, graph=""):
        return {}


@pytest.mark.parametrize("top_k, n", [(1, 1), (2, 2), (5, 5), (8, 8), (0, 9), (100, 9)])
def test_leiden_communities_cuts_every_route_at_top_k_rows(top_k, n):
    eng = _LeidenEngine()
    out = eng.leiden_communities(top_k=top_k)
    assert len(out) == n
    assert eng._store.asked == top_k
    assert [r["id"] for r in out] == ([f"k0_{i}" for i in range(4)] + [f"k1_{i}" for i in range(3)]
                                      + [f"k2_{i}" for i in range(2)])[:n]


# ------------------------------------------------------------------------------------- row 9: LIMIT pushdown


def _ppr_row_limit(query, params=None):
    """The row limit the translation hands kg_PPR: its seventh argument, or None when it passes six."""
    import re

    sql = translate_to_sql(parse_query(query), params or {}).sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    args = re.search(r"kg_PPR\(([^)]*)\)", sql).group(1).split(", ")
    return int(args[6]) if len(args) > 6 else None


CALL = "CALL ivg.ppr(['a'], 0.85, 20) YIELD node, score "


@pytest.mark.parametrize("tail, params, limit", [
    ("RETURN node, score ORDER BY score DESC LIMIT 20", None, 20),
    ("RETURN node.node_id AS id, score ORDER BY score DESC LIMIT 1", None, 1),
    ("RETURN node.node_id AS id, score LIMIT 5", None, 5),
    ("RETURN node, score ORDER BY score DESC SKIP 2 LIMIT 3", None, 5),
    ("RETURN node, score ORDER BY score DESC LIMIT $k", {"k": 7}, 7),
    ("RETURN node, score ORDER BY score DESC LIMIT 5000", None, 1000),
])
def test_a_limit_after_the_call_reaches_kg_ppr(tail, params, limit):
    """CALL ivg.ppr ... RETURN ... [ORDER BY score DESC] LIMIT k: the walk's answer is cut at k (+ SKIP), so the
    zeros stop there; at most the 1000 it answers without a LIMIT."""
    assert _ppr_row_limit(CALL + tail, params) == limit


@pytest.mark.parametrize("tail", [
    "RETURN node, score",  # no LIMIT
    "RETURN node, score LIMIT 0",
    "RETURN node, score ORDER BY score LIMIT 3",  # ascending: the zeros come first
    "RETURN node, score ORDER BY node LIMIT 3",
    "RETURN node.node_id AS id, score ORDER BY id LIMIT 3",
    "RETURN node, score ORDER BY score DESC, node LIMIT 3",
    "RETURN DISTINCT score LIMIT 3",
    "RETURN count(*) AS n LIMIT 3",
    "RETURN collect(node) AS ns LIMIT 3",
    "RETURN node, score * 2 AS s ORDER BY s DESC LIMIT 3",
    "WITH node, score WHERE score > 0 RETURN node LIMIT 3",
    "WITH node, score ORDER BY score DESC LIMIT 2 RETURN node",
    "MATCH (node)-->(m) RETURN m LIMIT 3",
])
def test_any_other_shape_keeps_the_full_answer(tail):
    """Between the CALL and the LIMIT nothing may filter, reorder (other than by score, highest first), aggregate
    or join, or the first k rows of the walk are not the first k rows of the query: kg_PPR answers its 1000."""
    assert _ppr_row_limit(CALL + tail, {}) is None


def test_a_yield_alias_is_the_score():
    q = "CALL ivg.ppr(['a']) YIELD node, score AS rank RETURN node, rank ORDER BY rank DESC LIMIT 4"
    assert _ppr_row_limit(q) == 4


# ------------------------------------------------------------------------------------------------ row 8b


@pytest.mark.parametrize("kw, named", [({"damping": 2.0}, "damping"), ({"damping": -0.5}, "damping"),
                                       ({"damping": 1.0}, "damping"), ({"max_iterations": 0}, "max_iterations")])
def test_global_pagerank_refuses_out_of_range(kw, named):
    """kg_PAGERANK without seeds: damping 2.0 gave negative scores (4.7.0 row 8b)."""
    with pytest.raises(ValueError, match=rf"^kg_PAGERANK: {named} must be"):
        AlgorithmsMixin.kg_PAGERANK(_NoDatabase(), **kw)


@pytest.mark.parametrize("metric", ["bogus", "mahalanobis", "", "cos"])
def test_vec_create_index_refuses_an_unknown_metric(metric):
    """Graph.KG.VecIndex.Distance read any other metric as cosine, and TopK then answered score = +distance."""
    from iris_vector_graph._engine.vector import VectorMixin

    with pytest.raises(ValueError, match=r"^vec_create_index: metric must be one of cosine, l2, l1, dot, chebyshev"):
        VectorMixin.vec_create_index(_NoDatabase(), "idx", 2, metric=metric)
