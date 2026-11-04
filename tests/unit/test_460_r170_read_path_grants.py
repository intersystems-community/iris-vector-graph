"""4.6.0 R170: a grantee that may run IVG's Cypher read path keeps it across an upgrade.

Spec 262 copies each direct grant on a 4.5.x SQLUser function to its IVG Schema replacement. 4.6.0's translation calls
functions 4.5.x did not have: since R218 every `RETURN n.prop` reads through `CY_TYPED`. So a role that held EXECUTE on
`SQLUser.CY_CMP` (what its 4.5.x queries needed) got EXECUTE on `Graph_KG.CY_CMP` and was refused the same query after
the upgrade (SQLCODE -99, "<PROTECT> Resource %Native_ClassExecution required"; test_262_upgrade_451 T034).

Now every setup gives each grantee holding EXECUTE on one read-path routine EXECUTE on all of them, including any a
later release adds. The read path is derived from the routine list: the `CY_*` functions setup creates and the
`CypherFn_*` the schema class projects. EXECUTE on routines only, never a table. The live check is in
tests/e2e/test_262_upgrade_451.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph._engine import legacy


def test_the_read_path_holds_every_cy_and_cypherfn_routine():
    """Every CY_* function but CY_VLP_* and every CypherFn_*; since R278 also the other functions that read nothing
    (tests/unit/test_460_r278_select_holders_read_path.py)."""
    from iris_vector_graph.schema import GraphSchema

    names = legacy.read_path_routines()
    for n in ("CY_TYPED", "CY_CMP", "CY_JSON_NUM", "CY_LIST_ELEM", "CypherFn_IVGJSONSET", "CypherFn_IVGRANGE"):
        assert n in names, n
    assert "kg_KNN_VEC" not in names and "kg_VEC_ROUTE_GUARD" not in names
    ddl = "\n".join(GraphSchema.get_procedures_sql_list())
    created = set(__import__("re").findall(r"CREATE OR REPLACE FUNCTION Graph_KG\.(CY_\w+)\(", ddl))
    assert {n for n in names if n.startswith("CY_")} == {n for n in created if not n.startswith("CY_VLP_")}


def test_the_variable_length_readers_are_left_out():
    """CY_VLP_ENDS walks ^KG directly (Graph.KG.VarLength.Ends), around SQL privileges: measured 2026-11-04, a caller
    with EXECUTE on it and no SELECT anywhere read a node's neighbour. Until it checks privileges, setup grants no
    CY_VLP_* routine by itself (4.6.0 R170)."""
    names = legacy.read_path_routines()
    assert [n for n in names if n.startswith("CY_VLP_")] == []
    assert "CY_VLP_" in legacy.READ_PATH_EXCLUDED_PREFIXES


class _Cur:
    def __init__(self, log, present):
        self.log, self.present, self._rows = log, present, []

    def execute(self, sql, params=None):
        self.log.append(sql)
        self._rows = [(n,) for n in self.present] if "INFORMATION_SCHEMA.ROUTINES" in sql else []

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _Conn:
    def __init__(self, present):
        self.log: list = []
        self.present = present

    def cursor(self):
        return _Cur(self.log, self.present)


def _grants(held):
    """grants_on stand-in: `held` maps a routine to its direct EXECUTE grantees."""

    def grants_on(_iris, sql_name):
        short = sql_name.split(".", 1)[1]
        return [{"subject": s, "subject_type": "ROLE", "privilege": "EXECUTE", "with_grant": False, "via": "Direct"}
                for s in held.get(short, [])]

    return grants_on


@pytest.fixture
def path(monkeypatch):
    monkeypatch.setattr(legacy, "read_path_routines", lambda: ["CY_CMP", "CY_TYPED", "CY_NEW"])


def test_a_holder_of_one_gets_the_rest(monkeypatch, path):
    conn = _Conn(["CY_CMP", "CY_TYPED", "CY_NEW"])
    monkeypatch.setattr(legacy, "grants_on", _grants({"CY_CMP": ["APPROLE"], "CY_TYPED": ["OTHER"]}))
    out = legacy.grant_read_path(conn, object(), "Graph_KG", dry_run=False)
    grants = sorted(s for s in conn.log if s.startswith("GRANT"))
    assert grants == [
        "GRANT EXECUTE ON Graph_KG.CY_CMP TO OTHER",
        "GRANT EXECUTE ON Graph_KG.CY_NEW TO APPROLE",
        "GRANT EXECUTE ON Graph_KG.CY_NEW TO OTHER",
        "GRANT EXECUTE ON Graph_KG.CY_TYPED TO APPROLE",
    ]
    assert sorted((g["subject"], g["routine"]) for g in out) == [
        ("APPROLE", "Graph_KG.CY_NEW"), ("APPROLE", "Graph_KG.CY_TYPED"),
        ("OTHER", "Graph_KG.CY_CMP"), ("OTHER", "Graph_KG.CY_NEW")]
    assert all(g["privilege"] == "EXECUTE" and g["granted"] for g in out)
    assert not [s for s in conn.log if " ON TABLE" in s or "SELECT ON" in s]


def test_a_second_run_grants_nothing(monkeypatch, path):
    conn = _Conn(["CY_CMP", "CY_TYPED", "CY_NEW"])
    everyone = ["APPROLE"]
    monkeypatch.setattr(legacy, "grants_on", _grants({n: everyone for n in ("CY_CMP", "CY_TYPED", "CY_NEW")}))
    assert legacy.grant_read_path(conn, object(), "Graph_KG", dry_run=False) == []
    assert not [s for s in conn.log if s.startswith("GRANT")]


def test_a_routine_not_in_the_namespace_is_not_granted(monkeypatch, path):
    conn = _Conn(["CY_CMP", "CY_TYPED"])
    monkeypatch.setattr(legacy, "grants_on", _grants({"CY_CMP": ["APPROLE"]}))
    legacy.grant_read_path(conn, object(), "Graph_KG", dry_run=False)
    assert [s for s in conn.log if s.startswith("GRANT")] == ["GRANT EXECUTE ON Graph_KG.CY_TYPED TO APPROLE"]


def test_the_dry_run_lists_and_grants_nothing_counting_the_grants_the_copy_would_make(monkeypatch, path):
    """A 4.5.x namespace before its upgrade: no grant on the IVG Schema's routines yet, one on SQLUser.CY_CMP that
    the copy would carry to Graph_KG.CY_CMP. The dry run says the role would get the rest."""
    conn = _Conn([])
    monkeypatch.setattr(legacy, "grants_on", _grants({}))
    pending = [{"replacement": "CY_CMP", "grants": [{"subject": "APPROLE", "privilege": "EXECUTE"}]}]
    out = legacy.grant_read_path(conn, object(), "Graph_KG", dry_run=True, pending=pending)
    assert sorted(g["routine"] for g in out) == ["Graph_KG.CY_NEW", "Graph_KG.CY_TYPED"]
    assert not any(g["granted"] for g in out)
    assert not [s for s in conn.log if s.startswith("GRANT")]


def test_an_unexpected_grantee_name_is_refused(monkeypatch, path):
    conn = _Conn(["CY_CMP", "CY_TYPED"])
    monkeypatch.setattr(legacy, "grants_on", _grants({"CY_CMP": ["x; DROP TABLE y"]}))
    with pytest.raises(ValueError, match="unexpected name"):
        legacy.grant_read_path(conn, object(), "Graph_KG", dry_run=False)
