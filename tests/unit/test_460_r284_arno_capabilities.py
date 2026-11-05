"""4.6.0 R284: capabilities() says which algorithms Arno answers, from the list that says so.

`Graph.KG.NKGAccelLoader.Capabilities` listed "ppr" among NKGAccel's own `algorithms`, though R276 removed NKGAccel's
PPR; personalized PageRank through Arno is `ArnoAccel.PPRNativeJson`, the Rust callout. `capabilities()["ppr_arno"]`
read that list, so it said True wherever the classes were loaded, and `wcc_arno` read the same list, which never holds
"wcc" (a Rust algorithm), so it said False wherever Arno answers WCC. Both now read `rust_algorithms`, where
Capabilities lists "ppr" only when the callout is there.
"""

from __future__ import annotations

import re
from pathlib import Path

LOADER = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "NKGAccelLoader.cls"


def _store(caps):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    st = IRISGraphStore.__new__(IRISGraphStore)
    st._arno_available = True
    st._arno_capabilities = caps
    st._detect_arno = lambda: True
    return st


def test_ppr_and_wcc_through_arno_come_from_the_rust_list():
    caps = _store({"algorithms": ["khop", "random_walk", "export_adjacency"],
                   "rust_algorithms": ["pagerank", "wcc", "cdlp", "bfs", "ppr"]}).capabilities()
    assert caps["ppr_arno"] is True and caps["wcc_arno"] is True


def test_without_the_callout_neither_is_claimed():
    caps = _store({"algorithms": ["khop", "random_walk", "export_adjacency"], "rust_algorithms": []}).capabilities()
    assert caps["ppr_arno"] is False and caps["wcc_arno"] is False


def test_the_loader_lists_ppr_only_with_the_callout():
    src = LOADER.read_text(encoding="utf-8")
    body = src[src.index("ClassMethod Capabilities()"):]
    body = body[:body.index("\n}")]
    algos = re.search(r"Set algos = \[([^\]]*)\]", body).group(1)
    assert '"ppr"' not in algos
    rust = body[body.index("If hasRust {"):body.index("rust_algorithms")]
    assert 'Do rustAlgos.%Push("ppr")' in rust
