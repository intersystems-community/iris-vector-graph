"""4.7.2: `CY_VLP_PATHS` is asked only for the path columns the SQL reads.

The function returns every trail of a start as one JSON string; IRIS refuses a string past 3,641,144 characters, and an
outside run met `<STRINGSTACK>` at five hops and 5,273 trails. A trail carried four columns, `n` (node ids), `y`
(types), `r` (relationships with their properties) and `k` (edge keys), and a query reads one or none of them. These
tests read the SQL the translator gives (the answers are live, tests/e2e/test_472_vlp_five_hops_e2e.py); a column the
SQL reads must never be left out.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _prune_vlp_columns, translate_to_sql

START = "(a {node_id: 'x'})"


def sql_of(q):
    t = translate_to_sql(parse_query(q), {})
    return t.sql if isinstance(t.sql, str) else "\n".join(t.sql)


def call_and_columns(sql):
    m = re.search(r"CY_VLP_PATHS\((.*?)\), '\$\[\*\]' COLUMNS\((.*?)\)\) (vlp\d+)", sql, re.S)
    assert m, sql
    args, columns, alias = m.groups()
    return args, re.findall(r"(\w+) (?:VARCHAR|INTEGER)", columns), alias


@pytest.mark.parametrize(
    "ret, mask, columns",
    [
        ("b.node_id", "'x'", ["t", "l"]),
        ("count(*)", "'x'", ["t", "l"]),
        ("count(r)", "'r'", ["t", "l", "r"]),
        ("r", "'r'", ["t", "l", "r"]),
        ("b.node_id, r", "'r'", ["t", "l", "r"]),
    ],
)
def test_only_the_columns_the_return_reads(ret, mask, columns):
    sql = sql_of(f"MATCH {START}-[r*1..5]->(b) RETURN {ret}")
    args, cols, _alias = call_and_columns(sql)
    assert args.endswith(f"'Graph_KG.rdf_edges', {mask}"), args
    assert cols == columns


def test_a_qualifier_test_reads_the_relationships_only():
    sql = sql_of(f"MATCH {START}-[r*1..5]->(b) WHERE all(x IN r WHERE x.state <> 'stale') RETURN DISTINCT b.node_id")
    args, cols, _alias = call_and_columns(sql)
    assert args.endswith("'Graph_KG.rdf_edges', 'r'"), args
    assert cols == ["t", "l", "r"]


def test_a_named_path_reads_the_nodes_and_the_types():
    sql = sql_of(f"MATCH p = {START}-[r*1..5]->(b) RETURN p")
    args, cols, _alias = call_and_columns(sql)
    assert cols == ["t", "l", "n", "y"] and args.endswith(", 'ny'"), (args, cols)


def test_a_call_with_no_pruning_to_do_is_left_as_it_was():
    # every column read: the call keeps the eight arguments it had (the function's `cols` argument defaults to all)
    sql = ("SELECT 1 FROM JSON_TABLE(Graph_KG.CY_VLP_PATHS(n0.node_id, '[]', 'out', 1, 2, 1, '-', 'Graph_KG.rdf_edges'), "
           "'$[*]' COLUMNS(t VARCHAR(512) PATH '$.t', l INTEGER PATH '$.l', n VARCHAR(32000) PATH '$.n', "
           "y VARCHAR(32000) PATH '$.y', r VARCHAR(32000) PATH '$.r', k VARCHAR(32000) PATH '$.k')) vlp1 "
           "WHERE vlp1.n IS NOT NULL AND vlp1.y IS NOT NULL AND vlp1.r IS NOT NULL AND vlp1.k IS NOT NULL")
    assert _prune_vlp_columns(sql) == sql


BASE = ("SELECT {sel} FROM Graph_KG.nodes n0 JOIN JSON_TABLE(Graph_KG.CY_VLP_PATHS(n0.node_id, '[]', 'out', 1, 2, 1, "
        "'-', 'Graph_KG.rdf_edges'), '$[*]' COLUMNS(t VARCHAR(512) PATH '$.t', l INTEGER PATH '$.l', "
        "n VARCHAR(32000) PATH '$.n', y VARCHAR(32000) PATH '$.y', r VARCHAR(32000) PATH '$.r', "
        "k VARCHAR(32000) PATH '$.k')) vlp1 ON 1=1")


@pytest.mark.parametrize(
    "sel, read",
    [
        ("vlp1.k", "k"),
        ("Graph_KG.CY_VLP_HAS(vlp1.k, 'a', 'b', 'c')", "k"),
        ("vlp1.y, vlp1.n", "ny"),
        ("JSON_VALUE(vlp1.r, '$[0].type')", "r"),
    ],
)
def test_every_column_read_is_kept(sel, read):
    out = _prune_vlp_columns(BASE.format(sel=sel))
    mask = re.search(r"'Graph_KG\.rdf_edges', '(\w+)'\)", out).group(1)
    assert mask == "".join(c for c in "nyrk" if c in read)
    for c in read:
        assert f"{c} VARCHAR(32000) PATH '$.{c}'" in out
    for c in set("nyrk") - set(read):
        assert f"PATH '$.{c}'" not in out


@pytest.mark.parametrize(
    "sel",
    [
        "vlp1.*",  # every column of the alias
        "vlp1",  # the alias on its own
        "*",  # a star over the joined row
    ],
)
def test_an_unseen_reading_keeps_every_column(sel):
    sql = BASE.format(sel=sel)
    assert _prune_vlp_columns(sql) == sql


def test_a_call_inside_another_function_is_left_alone():
    sql = BASE.format(sel="vlp1.r").replace("JSON_TABLE(Graph_KG.CY_VLP_PATHS(", "JSON_TABLE(Graph_KG.CY_VLP_OPT(Graph_KG.CY_VLP_PATHS(")
    sql = sql.replace("'Graph_KG.rdf_edges'), '$[*]'", "'Graph_KG.rdf_edges'), 0, '', '[]', 'x'), '$[*]'")
    assert _prune_vlp_columns(sql) == sql


def test_two_paths_in_one_query_are_each_pruned():
    sql = sql_of(f"MATCH {START}-[r*1..2]->(b)-[s*1..2]->(c) WHERE all(x IN s WHERE x.w > 1) RETURN count(r)")
    # `r` for the count and the test, `k` for the check that the two hops' trails share no edge (CY_VLP_DISJOINT)
    masks = re.findall(r"CY_VLP_PATHS\([^)]*'Graph_KG\.rdf_edges'(?:, '(\w+)')?\)", sql)
    assert masks == ["rk", "rk"], sql


class TestOldFunctionKeepsEveryColumn:
    """A namespace set up by an earlier IVG has CY_VLP_PATHS with eight arguments until `initialize_schema()` runs
    again; a call with the ninth is refused there, so the translator does not make it."""

    Q = f"MATCH {START}-[r*1..5]->(b) RETURN count(r)"

    def test_translation_for_an_engine_whose_function_takes_no_cols(self):
        from types import SimpleNamespace

        old = SimpleNamespace(_schema_prefix="Graph_KG", _vlp_cols_deployed=False, get_table_mapping=lambda *a, **k: None)
        t = translate_to_sql(parse_query(self.Q), {}, engine=old)
        sql = t.sql if isinstance(t.sql, str) else "\n".join(t.sql)
        args, cols, _alias = call_and_columns(sql)
        assert args.endswith("'Graph_KG.rdf_edges'"), args
        assert cols == ["t", "l", "n", "y", "r", "k"]

    def test_the_engine_asks_the_function_once_and_takes_a_refusal_as_no(self):
        from unittest.mock import MagicMock

        from iris_vector_graph import IRISGraphEngine

        conn = MagicMock()
        eng = IRISGraphEngine(conn, embedding_dimension=4, native_cypher=False)

        def asked():
            return [c.args[0] for c in conn.cursor.return_value.execute.call_args_list if "CY_VLP_PATHS" in c.args[0]]

        assert eng._vlp_cols_deployed is True
        assert eng._vlp_cols_deployed is True
        assert len(asked()) == 1 and asked()[0].count(",") == 8, asked()  # one probe, nine arguments

        refusing = MagicMock()
        refusing.cursor.return_value.execute.side_effect = RuntimeError("SQLCODE: <-359>: too many arguments")
        assert IRISGraphEngine(refusing, embedding_dimension=4, native_cypher=False)._vlp_cols_deployed is False
