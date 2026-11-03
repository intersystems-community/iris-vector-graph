"""Spec 262 T005: the function-name helper beside `_table()`, and the names IVG's DDL gives its functions (FR-001,
FR-007; spec Edge Cases "A function named like a table"; research R2)."""

import re

import pytest

from iris_vector_graph.cypher import translator
from iris_vector_graph.cypher.translator import _fn, _table
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.security import VALID_GRAPH_TABLES

_CREATE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE)\s+([\w%]+)\.(\w+)", re.I)


@pytest.fixture
def prefix():
    saved = translator.get_schema_prefix()
    yield
    translator.set_schema_prefix(saved)


def test_fn_qualifies_with_the_schema_given():
    assert _fn("STR_SPLIT", "IVGALT") == "IVGALT.STR_SPLIT"


def test_fn_and_table_agree_on_the_engines_schema(prefix):
    translator.set_schema_prefix("IVGALT")
    assert _fn("STR_SPLIT") == "IVGALT.STR_SPLIT"
    assert _table("rdf_edges").split(".")[0] == _fn("STR_SPLIT").split(".")[0]


def test_fn_is_qualified_even_with_no_engine_prefix(prefix):
    translator.set_schema_prefix("")
    assert _fn("CY_CMP") == "Graph_KG.CY_CMP"


@pytest.mark.parametrize("bad", ["", "a.b", "x;DROP", "1x", "a b"])
def test_fn_refuses_a_name_that_is_not_an_identifier(bad):
    with pytest.raises(ValueError):
        _fn(bad, "IVGALT")


def _created(schema):
    return [(kind.upper(), sch, name) for kind, sch, name in _CREATE.findall(
        "\n".join(GraphSchema.get_procedures_sql_list(table_schema=schema)))]


def test_the_ddl_creates_functions():
    assert len([k for k, _, _ in _created("IVGALT") if k == "FUNCTION"]) >= 30


def test_no_two_ivg_functions_compile_to_one_class():
    names = [n for kind, _, n in _created("IVGALT") if kind == "FUNCTION"]
    by_class = {}
    for n in names:
        by_class.setdefault("func" + n.replace("_", "").upper(), set()).add(n)
    assert {c: s for c, s in by_class.items() if len(s) > 1} == {}


def test_no_ivg_function_shares_a_name_with_an_ivg_table_or_view():
    names = {n.upper() for _, _, n in _created("IVGALT")}
    tables = {t.upper() for t in VALID_GRAPH_TABLES}
    assert names & tables == set()
