"""4.6.0 R265: `Graph.KG.PyOps` reads a named graph's vectors from the graph's route, and fuses as kg_RRF_FUSE does.

The shipped bodies of `RouteVectorRows` and `FuseRanks` run outside IRIS, as in tests/unit/test_460_r164_pyops_read_helper.py.
The live answers (a role-only user's /vectorSearch and /hybridSearch of a routed graph) are in
tests/e2e/test_460_r265_vector_procedures_route_e2e.py.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from tests.unit.test_460_pyops_registry_read import _python_method


class _Result(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.ResultSet = type("RS", (), {"_SQLCODE": 100, "_Message": ""})()


class FakeIris:
    """`iris.sql.exec`: the registry answers `registry`; any other statement answers `hits`."""

    def __init__(self, registry, hits=()):
        read_rows = _python_method("ReadRows")
        fake = self
        self.statements: list = []

        class PyOps:
            @staticmethod
            def ReadFailure(*a):
                return ""

            @staticmethod
            def ReadRows(*args):
                return read_rows(*args)

        self._pyops = PyOps

        class Sql:
            @staticmethod
            def exec(sql, *args):
                fake.statements.append((sql, args))
                return _Result(registry if "embedding_registry" in sql else list(hits))

        self.sql = Sql

    def cls(self, name):
        assert name == "Graph.KG.PyOps", name
        return self._pyops


def _route_rows(fake, *args):
    with patch.dict(sys.modules, {"iris": fake}):
        return _python_method("RouteVectorRows")(*args)


def test_a_routed_graph_is_read_from_its_route_with_its_element_type():
    fake = FakeIris([("kg_emb_abc", "FLOAT")], hits=[("a", 1.0), ("b", 0.6)])
    rows = _route_rows(fake, "[1,0,0]", 2, "", "g1")
    assert rows == [("a", 1.0), ("b", 0.6)]
    (reg_sql, reg_args), (sql, args) = fake.statements
    assert reg_args == ("g1",) and "COALESCE(model_key, '') = ''" in reg_sql
    assert "SELECT TOP 2 e.node_id" in sql and "FROM Graph_KG.kg_emb_abc e" in sql
    assert "TO_VECTOR(?, FLOAT)" in sql and "COALESCE(e.graph_id, '') = ?" in sql
    assert args == ("[1,0,0]", "g1")


def test_a_label_joins_the_graphs_labels():
    fake = FakeIris([("Graph_KG.kg_emb_abc", "DOUBLE")])
    _route_rows(fake, "[1,0,0]", 5, "Gene", "g1")
    sql, args = fake.statements[1]
    assert "JOIN Graph_KG.rdf_labels L ON L.s = e.node_id AND COALESCE(L.graph_id, '') = ?" in sql
    assert "L.label = ?" in sql and args == ("[1,0,0]", "g1", "g1", "Gene")


@pytest.mark.parametrize(
    "graph,registry",
    [("", [("kg_emb_abc", "DOUBLE")]), ("g1", []), ("g1", [("kg_NodeEmbeddings", "DOUBLE")])],
    ids=["default graph", "no route", "the legacy table"],
)
def test_the_procedure_reads_the_rest(graph, registry):
    """None: kg_KNN_VEC / kg_RRF_FUSE answer, as before."""
    fake = FakeIris(registry)
    assert _route_rows(fake, "[1,0,0]", 2, "", graph) is None
    assert all("kg_emb" not in sql for sql, _ in fake.statements[1:])


def test_an_unknown_element_type_raises():
    with pytest.raises(ValueError, match="unknown element type"):
        _route_rows(FakeIris([("kg_emb_abc", "VARCHAR(5) --")]), "[1]", 2, "", "g1")


def test_fuse_ranks_is_kg_rrf_fuse():
    """R200's worked example: vector ranks a, b, c; text ranks c (3), a (1); c = 60; a missing rank is 1e9."""
    fuse = _python_method("FuseRanks")
    got = fuse([("a", 1.0), ("b", 0.6), ("c", 0.0)], [("c", 3.0), ("a", 1.0)], 10, 60)
    assert [(n, round(r, 9), vs, ts) for n, r, vs, ts in got] == [
        ("a", round(1 / 61 + 1 / 62, 9), 1.0, 1.0),
        ("c", round(1 / 63 + 1 / 61, 9), 0.0, 3.0),
        ("b", round(1 / 62 + 1 / (60 + 1_000_000_000), 9), 0.6, None),
    ]
    assert [n for n, *_ in fuse([("a", 1.0)], [("t", 2.0)], 1, 60)] == ["a"]
