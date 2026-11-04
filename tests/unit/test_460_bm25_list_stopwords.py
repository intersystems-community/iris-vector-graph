"""4.6.0 R165: `Graph.KG.BM25Index.List()` answers index names, not the stopword list's node.

The stopwords live under `^BM25Idx("__stop__")`, beside one subscript per index, and `List()` walked every first
subscript: after any BM25 build, `status()` and SHOW INDEXES listed a BM25 index called `__stop__`. The class cannot
run offline, so this reads its source; the live answer is in `tests/e2e/test_460_bm25_list_stopwords_e2e.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "BM25Index.cls"


def _method(name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", CLS.read_text(), re.M | re.S)
    assert m, name
    return m.group(0)


def test_list_skips_the_stopword_node():
    body = _method("List")
    assert 'Continue:name="__stop__"' in body, body


def test_the_stopword_node_is_the_only_fixed_subscript():
    """Any other fixed first subscript of ^BM25Idx would need the same skip."""
    fixed = set(re.findall(r'\^BM25Idx\("([^"]+)"', CLS.read_text()))
    assert fixed == {"__stop__"}, fixed
