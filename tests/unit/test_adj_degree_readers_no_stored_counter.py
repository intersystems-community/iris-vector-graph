"""The four migrated readers no longer read the stored degree counters (ADR-0010).

`^KG("deg")` and `^KG("degp")` are being removed: a counter kept with `$Increment`
drifts when a transaction rolls back. Their readers now go through
`Graph.KG.Adj` (Degree, NextSource, HasEdges). This is a source scan, so it runs
without a container; the behaviour is locked in tests/e2e/test_adj_degree_live.py.

Comments are excluded: the docs may still name the counters while explaining
what was removed.
"""

from pathlib import Path
import re

IRIS_SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
KG_DIR = IRIS_SRC / "Graph" / "KG"

MIGRATED = ("Algorithms.cls", "Centrality.cls", "PageRank.cls", "Subgraph.cls")

STORED_COUNTER = re.compile(r'\^KG\(\s*"degp?"')


def _code_lines(path: Path) -> list[str]:
    """The source lines with ObjectScript comments removed.

    Whole-line `//` and `///` comments are dropped, and an inline `//` comment is
    cut from the end of its line. The four classes have no `//` inside a string
    literal, so the cut is safe here.
    """
    kept = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        code = line.split("//", 1)[0]
        kept.append(code)
    return kept


def test_scan_pattern_matches_the_counter_spelling():
    # The guard is only as good as its pattern: it must catch both spellings and
    # not the graph-key-less Adj helper names.
    assert STORED_COUNTER.search('Set deg = +$Get(^KG("deg", pGraph, node))')
    assert STORED_COUNTER.search('Set deg = +$Get(^KG("degp", pGraph, node, predicate))')
    assert not STORED_COUNTER.search('Set deg = ##class(Graph.KG.Adj).Degree(pGraph, node)')
    assert not STORED_COUNTER.search('Set x = ^KG("out", pGraph, node)')


def test_migrated_classes_exist():
    for name in MIGRATED:
        assert (KG_DIR / name).is_file(), f"missing {name}"


def test_no_migrated_reader_reads_the_stored_counters():
    offenders = []
    for name in MIGRATED:
        for lineno, code in enumerate(_code_lines(KG_DIR / name), start=1):
            if STORED_COUNTER.search(code):
                offenders.append(f"{name}:{lineno}: {code.strip()}")
    assert not offenders, "stored degree counter still read:\n" + "\n".join(offenders)
