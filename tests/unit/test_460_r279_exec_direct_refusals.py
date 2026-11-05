"""4.6.0 R279: every function reading through %ExecDirect raises a refusal left on the result.

`%SQL.Statement.%ExecDirect` returns a refused statement (SQLCODE -99) as a result whose `%Next()` answers 0, so
`CY_PROPS_MAP` answered `{}`, `CY_VLP_CHAIN` `[]` and `CY_VLP_OPT` "no path" to a caller who may not read the table.
Each read is now followed, where `%Next()` answers 0, by Graph.KG.SqlPrivilege.RaiseIfRefused with the table it reads.
Live: tests/e2e/test_460_r279_dynamic_sql_refusals_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph._engine import legacy

RAISE = "Do ##class(Graph.KG.SqlPrivilege).RaiseIfRefused(rs.%SQLCODE, rs.%Message, "


def _ddl():
    from iris_vector_graph.schema import GraphSchema

    out = {}
    for s in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"):
        m = re.search(r"CREATE OR REPLACE FUNCTION Graph_KG\.(\w+)\(", s)
        if m:
            out[m.group(1)] = s
    return out


def test_every_exec_direct_read_is_checked():
    # CY_PROPS_MAP's two %ExecDirect (with and without a graph) feed one loop, checked once after it
    unchecked = [n for n, s in _ddl().items() if "%ExecDirect(" in s and RAISE not in s]
    assert unchecked == []
    for name in ("CY_VLP_CHAIN", "CY_VLP_OPT"):
        s = _ddl()[name]
        assert s.count("If rs.%Next() = 0 { " + RAISE) == s.count("%ExecDirect(") == 1, name


@pytest.mark.parametrize("name, table", [("CY_PROPS_MAP", "tbl"), ("CY_VLP_CHAIN", "tbl"), ("CY_VLP_OPT", "ltbl")])
def test_each_names_the_table_it_reads(name, table):
    assert RAISE + table + ")" in _ddl()[name]


def test_the_props_map_raises_after_its_loop_not_inside_it():
    body = _ddl()["CY_PROPS_MAP"]
    loop = body.index("While rs.%Next() {")
    assert body.index(RAISE) > body.index("}", body.index('Set vals(keys(k), "k")', loop))


def test_the_check_keeps_props_map_on_the_read_path():
    """Graph.KG.SqlPrivilege reads no global, so naming it keeps a routine on R278's list."""
    assert "CY_PROPS_MAP" in legacy.read_path_routines()


def test_raise_if_refused_names_the_table_for_a_refusal_and_passes_otherwise():
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

    src = read_class_sources(packaged_class_dir())["Graph.KG.SqlPrivilege"]
    body = src[src.index("ClassMethod RaiseIfRefused"):]
    body = body[:body.index("\n}")]
    assert "Quit:(+pSQLCODE '< 0) tSC" in body
    assert "Do:(+pSQLCODE = -99) ..RequireSelect(pTable)" in body
    assert 'CreateFromSQLCODE(pSQLCODE, pMessage_" (reading "_pTable_")")' in body
