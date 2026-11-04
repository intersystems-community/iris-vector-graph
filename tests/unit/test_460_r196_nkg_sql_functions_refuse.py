"""4.6.0 R196: the seven `^NKG` SQL functions refuse a namespace where a named graph has edges, as their Cypher form
does (R184).

Each body calls `Graph.KG.NKGAccel.RefuseMergedGraphs` before reading `^NKG`; that method throws when `^KG("out")` holds
a graph key other than the default graph's 0. The live refusal is in tests/e2e/test_460_r196_nkg_sql_functions_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris_vector_graph.schema import GraphSchema

NKG_FUNCTIONS = ["kg_Betweenness", "kg_Closeness", "kg_Eigenvector", "kg_Leiden", "kg_TriangleCount", "kg_SCC", "kg_KCore"]
NKGACCEL = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "NKGAccel.cls"


def _bodies():
    return [str(s) for s in GraphSchema.get_procedures_sql_list("Graph_KG")]


@pytest.mark.parametrize("name", NKG_FUNCTIONS)
def test_each_function_refuses_before_reading_nkg(name):
    ddl = next(s for s in _bodies() if f"FUNCTION Graph_KG.{name}(" in s)
    body = ddl.split("{", 1)[1]
    first = next(line.strip() for line in body.splitlines() if line.strip())
    assert first == f'do ##class(Graph.KG.NKGAccel).RefuseMergedGraphs("{name}")', ddl


def test_refuse_merged_graphs_passes_the_default_graph_key_and_throws_on_any_other():
    src = NKGACCEL.read_text()
    body = src.split("ClassMethod RefuseMergedGraphs", 1)[1].split("\nClassMethod ", 1)[0]
    assert re.search(r'\$Order\(\^KG\("out", tKey\)\)', body) and "Continue:tKey=0" in body and "Throw" in body, body
