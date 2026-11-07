"""4.6.0 R316: `kg_EdgeEmbeddings` is registered as the edge route it is, and the /kg role reads only what its routes do.

Found by the round-3 walkthrough on Community 2026.1: `Graph.KG.Service.CreateWebApp("/ivgb")` gave its role SELECT on
`kg_NodeEmbeddings`, `rdf_labels`, `docs` and `embedding_registry`, and not on `kg_NodeEmbeddings_optimized` or
`kg_EdgeEmbeddings`, which ADMIN_GUIDE §4 said it grants. Two causes:

- Setup's adoption (`adopt_embedding_identities`) recorded every legacy vector table with the registry's default kind,
  `node`, `kg_EdgeEmbeddings` (keyed `(s, p, o_id)`) included. Every reader that asks the registry for the default
  graph's node route (`PyOps.getVectorTable`, `getExpectedDimension`) then had three rows to pick from, in no order.
- `GrantWebAppSql` grants SELECT on a registry row's table only when its name is a route's own (`kg_emb_` and 16 hex
  digits), since the GRANT is built from data any writer can insert. So neither legacy table was granted, and none of
  the three routes reads either: `/vectorSearch` and `/hybridSearch` read `kg_NodeEmbeddings` (through `kg_KNN_VEC`)
  or a named graph's route, `/metaPath` reads `^KG`. The grants were right and ADMIN_GUIDE was not.

Adoption now records `kg_EdgeEmbeddings` as kind `edge`, setup repairs a row an earlier setup recorded as `node`, and
PyOps reads `kg_NodeEmbeddings` first when the default graph has two node rows (as the engine's `_route_preference`
does). The live grants per table, under a user holding only the role, are in
tests/e2e/test_460_r316_webapp_grants_per_table_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from iris_vector_graph.constants import VECTOR_TABLE_NAMES
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

ROOT = Path(__file__).resolve().parents[2]
DIM = 4


def _adopting_engine():
    registry = FakeRegistry(tables=list(VECTOR_TABLE_NAMES), dimension_for={n: DIM for n in VECTOR_TABLE_NAMES})
    return registry, engine_with(registry, embedding_dimension=DIM)


def test_adoption_records_the_edge_table_as_an_edge_route():
    registry, engine = _adopting_engine()
    engine.adopt_embedding_identities()
    assert registry.row_for("kg_EdgeEmbeddings", "")["kind"] == "edge"


def test_adoption_leaves_the_node_tables_at_the_default_kind():
    registry, engine = _adopting_engine()
    engine.adopt_embedding_identities()
    for name in ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized"):
        assert registry.row_for(name, "").get("kind", "node") == "node", name


def test_setup_repairs_an_edge_table_row_an_earlier_setup_recorded_as_a_node_route():
    registry = FakeRegistry(tables=list(VECTOR_TABLE_NAMES))
    engine = engine_with(registry, embedding_dimension=DIM)
    engine._repair_legacy_edge_route_kind(registry.cursor())
    (sql, params), = registry.statements_matching("UPDATE")
    assert re.search(r"SET kind = 'edge'", sql), sql
    assert "kg_EdgeEmbeddings" in sql + str(params)
    assert "COALESCE(kind, 'node') = 'node'" in sql, "a row already recorded as edge is left alone"


def test_initialize_schema_runs_the_repair_after_adoption():
    src = (ROOT / "iris_vector_graph/_engine/schema.py").read_text()
    adopt = src.index("self.adopt_embedding_identities(cursor)")
    repair = src.index("self._repair_legacy_edge_route_kind(cursor)")
    assert adopt < repair


def _pyops_method(name: str) -> str:
    src = (ROOT / "iris_src/src/Graph/KG/PyOps.cls").read_text()
    start = src.index(f"ClassMethod {name}(")
    return src[start:src.index("\n}\n", start)]


def test_pyops_reads_kg_node_embeddings_first_for_the_default_graph():
    """Two node rows answer the default graph (kg_NodeEmbeddings, and kg_NodeEmbeddings_optimized, a 3.x layout): the
    width and the table come from kg_NodeEmbeddings, whichever row IRIS returns first."""
    for method in ("getVectorTable", "getExpectedDimension"):
        body = _pyops_method(method)
        assert re.search(r"ORDER BY CASE WHEN UPPER\(table_name\) = 'KG_NODEEMBEDDINGS' THEN 0 ELSE 1 END", body), method


def test_the_role_is_granted_no_table_by_name_but_the_routes_own():
    """The grant list names kg_NodeEmbeddings, rdf_labels, docs and embedding_registry; a registry row adds only a
    route of its own shape. Neither legacy table is in the list."""
    src = (ROOT / "iris_src/src/Graph/KG/Service.cls").read_text()
    body = src[src.index("ClassMethod GrantWebAppSql("):]
    body = body[:body.index("\n}\n")]
    selects = re.findall(r'\$ListBuild\("SELECT", tSchema_"\.(\w+)"\)', body)
    assert sorted(selects) == ["docs", "embedding_registry", "kg_NodeEmbeddings", "rdf_labels"]
    assert 'Continue:\'$Match(tShort,"(?i)kg_emb_[0-9a-f]{16}")' in body


def test_admin_guide_says_the_role_reads_neither_legacy_table():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text()
    section = guide[guide.index("In the IVG Schema: EXECUTE on `kg_KNN_VEC`"):]
    section = " ".join(section[:section.index("Running `CreateWebApp` again")].split())
    assert "which setup records there for the default graph" not in section
    assert "`kg_NodeEmbeddings_optimized` or `kg_EdgeEmbeddings`" in section
