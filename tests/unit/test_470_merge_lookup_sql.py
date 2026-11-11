"""4.7.0 (lead, after row 2): the SQL a MERGE ... SET writes with, checked without IRIS.

The MERGE's lookup read every row of the key (idx_props_key, EXPLAIN in a new namespace); a string value now also
compares `val_key` (the first 128 characters of `val`, indexed with the key), and the lookup's FROM ignores
idx_props_key and idx_labels_label so that index drives it. (`%INSUB` on the SET's `IN (<lookup>)` was tried and
dropped: on 2026.3 it answered 1 row of a 50-row subquery.)
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


class _Engine:
    """What the translator reads off an engine that has the value-key column. One graph: the SET's statements read
    the lookup as an `IN` subquery; several (`MULTI`): the ids are captured first (`__capture_ids__`)."""

    _props_value_key = True
    _schema_prefix = None
    MULTI = False

    def get_table_mapping(self, label):
        return None

    def _more_than_one_graph(self):
        return self.MULTI


class _MultiGraph(_Engine):
    MULTI = True


ENGINES = pytest.mark.parametrize("engine", [_Engine(), _MultiGraph()], ids=["one-graph", "several-graphs"])


def _sql(query, params=None, engine=None):
    r = translate_to_sql(parse_query(query), params or {}, engine=engine if engine is not None else _Engine())
    return [re.sub(r"\s+", " ", s) for s in (r.sql if isinstance(r.sql, list) else [r.sql])]


def _lookups(stmts):
    """The MERGE's lookups: each `IN (SELECT n0 ...)` subquery and each captured SELECT."""
    out = [s for s in stmts if s.startswith("__capture_ids__")]
    for s in stmts:
        out += [s[m.start():] for m in re.finditer(r" IN \(SELECT n0", s)]
    return out


HINT = "%IGNOREINDEX Graph_KG.rdf_props.idx_props_key, Graph_KG.rdf_labels.idx_labels_label Graph_KG.nodes"


@ENGINES
def test_a_string_id_lookup_reads_the_value_key(engine):
    stmts = _sql("MERGE (n:TM {id: $id}) SET n.v = $v", {"id": "m1", "v": 1}, engine)
    reads = _lookups(stmts)
    assert reads, stmts
    for s in reads:
        assert ".val = ? AND p2.val_key = 'm1'" in s, s
        assert f" FROM {HINT} n0 " in s, s



def test_a_long_id_compares_its_first_128_characters():
    long_id = "x" * 128 + "a'b"
    stmts = _sql("MERGE (n:TM {id: $id}) SET n.v = $v", {"id": long_id, "v": 1})
    assert any(f"p2.val_key = '{'x' * 128}'" in s for s in stmts), stmts


def test_a_quote_in_the_value_is_escaped():
    stmts = _sql("MERGE (n:TM {id: $id}) SET n.v = 1", {"id": "o'k"})
    assert any("p2.val_key = 'o''k'" in s for s in stmts), stmts


@ENGINES
def test_a_numeric_id_has_no_value_key_and_no_hint(engine):
    """A number matches every stored spelling (4.6.0 R168), which a text prefix cannot say."""
    stmts = _sql("MERGE (n:TM {id: 42}) SET n.v = 1", engine=engine)
    assert _lookups(stmts), stmts
    assert not any("val_key" in s or "%IGNOREINDEX Graph_KG.rdf_props.idx_props_key, Graph_KG.rdf_labels" in s
                   for s in stmts), stmts


def test_without_the_column_nothing_names_it():
    class Old(_Engine):
        _props_value_key = False

    stmts = _sql("MERGE (n:TM {id: $id}) SET n.v = $v", {"id": "m1", "v": 1}, engine=Old())
    assert not any("val_key" in s or HINT in s for s in stmts), stmts


@ENGINES
def test_the_create_halfs_existence_check_reads_the_value_key_too(engine):
    """`NOT EXISTS (<pattern>)` on the node insert is the same lookup (its aliases _ml0 / _mp0)."""
    stmts = _sql("MERGE (n:TM {name: $n})", {"n": "zed"}, engine)
    node_insert = [s for s in stmts if s.startswith("INSERT INTO Graph_KG.nodes ")]
    assert node_insert, stmts
    assert "_mp0.val = ? AND _mp0.val_key = 'zed'" in node_insert[0], node_insert[0]
    assert f"NOT EXISTS (SELECT 1 FROM {HINT} _ml0 " in node_insert[0], node_insert[0]


def test_a_subquery_with_no_value_key_of_its_own_is_left_alone():
    """The hint goes only where the (key, val_key) index can drive: a label-only MERGE keeps its plan."""
    stmts = _sql("MERGE (n:TM) SET n.v = 1")
    assert not any("%IGNOREINDEX Graph_KG.rdf_props.idx_props_key, Graph_KG.rdf_labels" in s for s in stmts), stmts
