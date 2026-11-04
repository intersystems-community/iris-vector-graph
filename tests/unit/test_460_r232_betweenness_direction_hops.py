"""4.6.0 R232: `direction` and `maxHops` reach the server's Brandes on both of betweenness's routes.

`CALL ivg.betweenness` called `kg_Betweenness(sampleSize, direction, maxHops, topK, memBudgetMB)`, whose body passed
`BetweennessGlobal` only the sample size and topK; `betweenness_centrality(max_hops=k)` took the same ObjectScript
tier for "out" and "in" and passed no hop limit either. Each answered the directed, unbounded scores without an
error. The live proof against networkx is tests/e2e/test_460_r232_betweenness_direction_hops_e2e.py; here, what each
route passes and refuses.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

ROOT = Path(__file__).resolve().parents[2]


def _store(calls):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    iris_obj = mock.Mock()

    def cmv(cls, method, *args):
        calls.append((method, args))
        return 'OK:[{"id":"a","score":1.0}]'

    iris_obj.classMethodValue.side_effect = cmv
    store._iris_obj = lambda: iris_obj
    store.conn = mock.Mock()
    store._named_graph_with_edges = lambda: None
    return store


@pytest.mark.parametrize("direction", ["out", "in"])
def test_the_objectscript_tier_is_given_the_direction_and_the_hop_limit(direction):
    calls = []
    _store(calls)._betweenness_gref(7, direction, 2, 10, 256, None)
    (args,) = [a for m, a in calls if m == "BetweennessGlobal"]
    assert args == (7, 10, 7, direction, 2)


def test_the_sql_function_passes_both_to_the_server():
    from iris_vector_graph.schema import GraphSchema

    ddl = [s for s in GraphSchema.get_procedures_sql_list("Graph_KG") if ".kg_Betweenness(" in s]
    assert len(ddl) == 1
    assert "BetweennessGlobal(sampleSize, topK, samples, direction, maxHops)" in ddl[0]


def test_the_server_methods_take_both():
    for cls in ("NKGAccel", "NKGAccelCentrality"):
        text = (ROOT / f"iris_src/src/Graph/KG/{cls}.cls").read_text()
        sig = re.search(r"^ClassMethod BetweennessGlobal\((.*?)\)", text, re.M).group(1)
        assert 'direction As %String = "out"' in sig and "maxHops As %Integer = 0" in sig, cls


@pytest.mark.parametrize("opts,word", [("{direction: 'sideways'}", "direction"), ("{maxHops: -1}", "maxHops")])
def test_the_procedure_refuses_what_it_cannot_honour(opts, word):
    with pytest.raises(ValueError, match=word):
        translate_to_sql(parse_query(f"CALL ivg.betweenness({opts}) YIELD node, score RETURN node, score"), {})

