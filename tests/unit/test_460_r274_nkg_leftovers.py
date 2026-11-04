"""4.6.0 R274 (^NKG leftovers), pinned against the ObjectScript source (no container): a named graph's last edge
removed marks ^NKG stale, and EnsureNKG rebuilds a stale ^NKG before a reader reads it.

The answers are proven live in tests/e2e/test_460_r274_nkg_leftovers_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

KG = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"


def _method(cls: str, name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", (KG / cls).read_text(), re.S | re.M)
    assert m, name
    return "\n".join(ln for ln in m.group(0).splitlines() if not ln.lstrip().startswith("//"))


def test_mark_graph_gone_marks_only_a_named_graph_left_with_no_edge_while_nkg_is_built():
    body = _method("Adjacency.cls", "MarkGraphGone")
    assert "Quit:pGraph=0" in body
    assert 'Quit:$Data(^KG("out", pGraph))' in body
    assert 'Quit:\'$Data(^NKG("$meta", "nodeCount"))' in body
    # a graph ^NKG never held (no end interned) leaves it as it is (test_erase_graph_nkg_e2e)
    assert 'Quit:\'$Data(^NKG("$NI", pSrc))&&((pDst="")||\'$Data(^NKG("$NI", pDst)))' in body
    assert 'Set ^NKG("$meta", "stale") = 1' in body


@pytest.mark.parametrize("name,call", [("Remove", "Do:tHad ..MarkGraphGone(pGraph, pSrc, pDst)"),
                                       ("RemoveSource", "Do ..MarkGraphGone(pGraph, pSrc)")])
def test_each_edge_removal_asks(name, call):
    assert call in _method("Adjacency.cls", name)


def test_a_graph_s_erase_does_not():
    """RemoveGraph runs inside an erase, whose InvalidateDerived drops ^NKG when it held the graph (R268)."""
    assert "MarkGraphGone" not in _method("Adjacency.cls", "RemoveGraph")


def test_ensure_rebuilds_a_stale_nkg_under_the_build_lock():
    body = _method("TraversalBuild.cls", "EnsureNKG")
    assert 'Set tStale = +$Get(^NKG("$meta", "stale"))' in body
    assert body.index("Lock +^IVG.NKGBuild") < body.index('If $Get(^NKG("$meta", "stale")) ||')


def test_a_rebuild_pass_answers_the_mark_before_it_walks():
    body = _method("TraversalBuild.cls", "RewritePass")
    assert body.index('Kill ^NKG("$meta", "stale")') < body.index('Set g = $Order(^KG("out", g))')
