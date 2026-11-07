"""4.6.0 R316: what the /kg routes can read, from the code, so the role's grants can be checked against it.

`Graph.KG.Service` routes `/vectorSearch`, `/hybridSearch` and `/metaPath` to `Graph.KG.PyOps.VectorSearch`,
`HybridSearch` and `MetaPath`. Those, and the PyOps methods they call, read through `kg_KNN_VEC`, `kg_RRF_FUSE`,
`kg_TXT` and `kg_VEC_ROUTE_GUARD` (the SQL functions the role may EXECUTE), name a table from `embedding_registry`
(`getVectorTable`, `getExpectedDimension`, `RouteVectorRows`), or walk `^KG` (`Graph.KG.Traversal.BFSJSON`). None of
that code names `kg_NodeEmbeddings_optimized` or `kg_EdgeEmbeddings`, and the registry readers take `kg_NodeEmbeddings`
first for the default graph and read node routes only, so neither table can be picked there either: the role is
granted neither. Were a route added, the first test fails and this list must be checked again. The live proof (each
route called by a user holding only the role, both tables populated) is
tests/e2e/test_460_r316_webapp_grants_per_table_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "iris_src" / "src" / "Graph" / "KG"
LEGACY = ("kg_NodeEmbeddings_optimized", "kg_EdgeEmbeddings")
#: The PyOps methods a route reaches: the three it calls and what they call in PyOps.
ROUTE_METHODS = ("VectorSearch", "HybridSearch", "MetaPath", "vectorToJson", "getVectorTable", "getExpectedDimension",
                 "RouteVectorRows", "FuseRanks", "ReadRows", "Lacks", "ReadFailure", "NoteNoRegistry",
                 "getLegacyVectorTable", "getVectorColumn", "getDefaultDimension")
ROUTINES = ("kg_KNN_VEC", "kg_RRF_FUSE", "kg_TXT", "kg_VEC_ROUTE_GUARD")


def _method(cls_text: str, name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^}}", cls_text, re.M | re.S)
    assert m, name
    return m.group(0)


def test_the_web_app_has_exactly_these_three_routes():
    routes = re.findall(r'<Route Url="([^"]+)" Method="(\w+)" Call="(\w+)"', (SRC / "Service.cls").read_text())
    assert routes == [("/vectorSearch", "POST", "VectorSearch"), ("/hybridSearch", "POST", "HybridSearch"),
                      ("/metaPath", "POST", "MetaPath")]


def _code(body: str) -> str:
    """`body` without its comments (Python `#`, ObjectScript `//`, and `///` doc lines): a comment that names a table
    is not a read of it."""
    return "\n".join(re.split(r"\s#|^\s*#|//", line, maxsplit=1)[0] for line in body.splitlines())


def test_the_pyops_code_a_route_reaches_names_neither_legacy_table():
    text = (SRC / "PyOps.cls").read_text()
    named = {m: [t for t in LEGACY if t.lower() in _code(_method(text, m)).lower()] for m in ROUTE_METHODS}
    assert {m: t for m, t in named.items() if t} == {}


def test_the_routines_the_role_may_execute_name_neither_legacy_table():
    from iris_vector_graph.schema import GraphSchema

    bodies = {}
    for sql in GraphSchema.get_procedures_sql_list():
        m = re.search(r"(?:FUNCTION|PROCEDURE)\s+\S*?\.?(" + "|".join(ROUTINES) + r")\s*\(", sql)
        if m:
            bodies[m.group(1)] = sql
    assert set(bodies) == set(ROUTINES)  # each routine is found, so an empty answer cannot pass
    assert {r: [t for t in LEGACY if t.lower() in b.lower()] for r, b in bodies.items()} == {r: [] for r in ROUTINES}


def test_the_registry_readers_take_kg_node_embeddings_first_and_node_routes_only():
    text = (SRC / "PyOps.cls").read_text()
    for m in ("getVectorTable", "getExpectedDimension"):
        body = _method(text, m)
        assert "COALESCE(kind, 'node') = 'node'" in body, m
        assert "WHEN UPPER(table_name) = 'KG_NODEEMBEDDINGS' THEN 0" in body, m
    # a named graph's route is read only in its own shape
    assert r'kg_emb_[0-9a-f]{16}' in _method(text, "RouteVectorRows")
