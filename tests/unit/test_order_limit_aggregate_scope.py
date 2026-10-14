"""ORDER BY + LIMIT/SKIP under `_fetch_first_unsafe` must not leave inner-scope
references in the outer ORDER BY.

With the build-106 %qaqpre workaround the translator wraps the statement as
`SELECT TOP n * FROM (<inner>) __ob ORDER BY <items>`. An ORDER BY item that
names an aggregate (`COUNT(n2.node_id)`) or a bare table alias (`n0.node_id`)
refers to a table that does not exist outside the derived table, and IRIS
rejects it with SQLCODE -23. Such items must be projected as `__sortN` columns.
"""

import re
from types import SimpleNamespace

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

AGG = "MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) RETURN t.name AS name, count(m) AS c "
_INNER_ALIAS = re.compile(r"\b[nelp]\d+\.")


def _sql(cypher, unsafe=True):
    engine = SimpleNamespace(
        _fetch_first_unsafe=unsafe,
        get_table_mapping=lambda *a, **k: None,
        get_rel_mapping=lambda *a, **k: None,
    )
    out = translate_to_sql(parse_query(cypher), engine=engine).sql
    return out if isinstance(out, str) else "\n".join(out)


def _outer_order_by(sql):
    """Everything after the `__ob` derived-table alias."""
    assert "__ob" in sql, sql
    return sql.split("__ob", 1)[1]


def _after_paged(sql):
    assert "__paged" in sql, sql
    return sql.split("__paged", 1)[1]


class TestAggregateAliasOrderByLimit:
    def test_aggregate_alias_projected_and_outer_order_by_uses_it(self):
        sql = _sql(AGG + "ORDER BY c DESC LIMIT 5")
        assert sql.startswith("SELECT TOP 5 * FROM (")
        assert re.search(r"AS __sort0\b", sql)
        outer = _outer_order_by(sql)
        assert outer.strip() == "ORDER BY __sort0 DESC"
        assert "COUNT(" not in outer.upper()
        assert not _INNER_ALIAS.search(outer)

    def test_skip_limit_uses_row_number_over_projected_column(self):
        sql = _sql(AGG + "ORDER BY c DESC SKIP 2 LIMIT 5")
        assert "ROW_NUMBER() OVER(ORDER BY __sort0 DESC)" in sql
        assert re.search(r"AS __sort0\b", sql)
        over = sql.split("OVER(", 1)[1].split(")", 1)[0]
        assert "COUNT(" not in over.upper()
        assert not _INNER_ALIAS.search(over)
        assert not _INNER_ALIAS.search(_after_paged(sql))

    def test_two_keys_project_both(self):
        sql = _sql(AGG + "ORDER BY c DESC, name LIMIT 5")
        assert re.search(r"AS __sort0\b", sql)
        assert re.search(r"AS __sort1\b", sql)
        outer = _outer_order_by(sql)
        assert outer.strip() == "ORDER BY __sort0 DESC, __sort1 ASC"
        assert not _INNER_ALIAS.search(outer)


class TestBareAliasOrderByLimit:
    @pytest.mark.parametrize(
        "cypher",
        [
            "MATCH (t:Tag) RETURN t ORDER BY t.node_id LIMIT 3",
            "MATCH (t:Tag) RETURN t.node_id AS id ORDER BY id LIMIT 3",
        ],
    )
    def test_bare_node_id_projected(self, cypher):
        sql = _sql(cypher)
        assert re.search(r"AS __sort0\b", sql)
        outer = _outer_order_by(sql)
        assert outer.strip() == "ORDER BY __sort0 ASC"
        assert not _INNER_ALIAS.search(outer)


class TestUnchangedShapes:
    def test_no_limit_is_not_wrapped_or_projected(self):
        sql = _sql(AGG + "ORDER BY c DESC")
        assert "__ob" not in sql
        assert "__sort" not in sql
        assert sql.rstrip().endswith("ORDER BY %EXACT(SQLUser.CY_SORT_KEY(COUNT(n2.node_id), 'x')) DESC")

    def test_flag_off_keeps_fetch_first_and_inner_expression(self):
        sql = _sql(AGG + "ORDER BY c DESC LIMIT 5", unsafe=False)
        assert "FETCH FIRST 5 ROWS ONLY" in sql
        assert "__ob" not in sql
        assert "__sort" not in sql
        assert "ORDER BY %EXACT(SQLUser.CY_SORT_KEY(COUNT(n2.node_id), 'x')) DESC" in sql

    @pytest.mark.parametrize(
        "cypher",
        [
            "MATCH (t:Tag) RETURN t ORDER BY t.node_id LIMIT 3",
            "MATCH (p:Protein) RETURN p.name ORDER BY p.name SKIP 1 LIMIT 4",
            "MATCH (p:Protein) RETURN p.name LIMIT 1",
        ],
    )
    def test_flag_off_has_no_sort_projection(self, cypher):
        sql = _sql(cypher, unsafe=False)
        assert "__sort" not in sql
        assert "__ob" not in sql
        assert "__paged" not in sql

    def test_property_order_by_still_projected(self):
        sql = _sql("MATCH (p:Protein) RETURN p.name ORDER BY p.name LIMIT 4")
        assert "AS __sort0" in sql
        assert _outer_order_by(sql).strip() == "ORDER BY __sort0 ASC"
