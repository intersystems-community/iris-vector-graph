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
