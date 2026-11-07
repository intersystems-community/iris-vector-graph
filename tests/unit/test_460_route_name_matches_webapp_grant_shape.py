"""4.6.0 R265: every node route name `routing.route_table_name` makes is one `GrantWebAppSql` grants SELECT on.

`Graph.KG.Service.GrantWebAppSql` grants the /kg role SELECT on each node route in the registry whose name has the
route's own shape, `$Match(tShort, "(?i)kg_emb_[0-9a-f]{16}")`, so a registry row any writer inserts cannot name
another table. If the Python side's names changed shape (another prefix, a longer hash), every route would fail that
check and the role would read no named graph's vectors, with no error at grant time. The pattern is read from
Service.cls, so either side changing alone fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris_vector_graph.routing import edge_route_table_name, route_table_name

SERVICE_CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "Service.cls"


def _grant_pattern() -> re.Pattern:
    body = SERVICE_CLS.read_text()
    body = body[body.index("ClassMethod GrantWebAppSql("):]
    m = re.search(r"\$Match\(tShort,\s*\"([^\"]+)\"\)", body)
    assert m, "GrantWebAppSql no longer checks a route's shape with $Match(tShort, ...)"
    # ObjectScript's $Match is ICU and anchored to the whole string; (?i) reads the same in Python
    return re.compile(m.group(1))


def test_the_pattern_is_the_one_this_test_was_written_against():
    assert _grant_pattern().pattern == "(?i)kg_emb_[0-9a-f]{16}"


@pytest.mark.parametrize("graph_id, model_key", [
    ("", None), ("g1", None), ("acme", "minilm"), ("ünïcode graph", "m/2"), ("x" * 200, "k"),
])
def test_every_node_route_name_is_granted(graph_id, model_key):
    name = route_table_name(graph_id, model_key)
    assert _grant_pattern().fullmatch(name), name


def test_an_edge_route_name_is_not_a_node_route():
    # the registry's kind column separates them; the shape does too, so the grant never takes an edge route
    assert not _grant_pattern().fullmatch(edge_route_table_name("g1", None))


def test_a_name_that_is_not_a_route_is_refused():
    pattern = _grant_pattern()
    for name in ("kg_NodeEmbeddings", "kg_emb_0123456789abcde", "kg_emb_0123456789abcdef0", "kg_emb_0123456789abcdeg"):
        assert not pattern.fullmatch(name), name
