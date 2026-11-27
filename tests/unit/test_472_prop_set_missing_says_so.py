"""4.7.2: a namespace without Prop_Set (classes older than 4.7.0) writes a SET as 4.6.x did, and says so once.

4.7.0 did it without a word: on such a namespace `SET n.p = toInteger(n.p)` over the string '30' kept the string and
answered success; `CALL ivg.ppr`'s older kg_PPR was already said once, with the remedy.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from iris_vector_graph.cypher import translator
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

SET = "MATCH (n:P) SET n.p = toInteger(n.p)"


def _engine(deployed):
    return SimpleNamespace(_schema_prefix="Graph_KG", _prop_set_deployed=deployed, get_table_mapping=lambda *a, **k: None)


def _said(caplog):
    return [r.getMessage() for r in caplog.records if "Prop_Set" in r.getMessage()]


def test_a_set_without_prop_set_says_once_what_is_missing_and_the_remedy(caplog):
    translator._warned_prop_set_missing = False
    with caplog.at_level(logging.WARNING):
        translate_to_sql(parse_query(SET), {}, engine=_engine(False))
        translate_to_sql(parse_query(SET), {}, engine=_engine(False))
    said = _said(caplog)
    assert len(said) == 1, said
    # it names the call that writes the fix, and what the namespace keeps doing until then
    assert "initialize_schema()" in said[0] and "deploys" in said[0]
    assert "string" in said[0]


def test_a_namespace_with_prop_set_says_nothing(caplog):
    translator._warned_prop_set_missing = False
    with caplog.at_level(logging.WARNING):
        translate_to_sql(parse_query(SET), {}, engine=_engine(True))
        translate_to_sql(parse_query(SET), {}, engine=None)
    assert _said(caplog) == []


def test_a_query_that_sets_nothing_says_nothing(caplog):
    translator._warned_prop_set_missing = False
    with caplog.at_level(logging.WARNING):
        translate_to_sql(parse_query("MATCH (n:P) RETURN n.p"), {}, engine=_engine(False))
    assert _said(caplog) == []
