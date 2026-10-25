"""The premise behind EraseIdSet keeping ^NKG, pinned against the source.

`Graph.KG.Eraser:EraseIdSet` leaves ^NKG alone when no id it erased (and no removed
edge with both ends) is interned in ^NKG. That is sound only while

  1. every writer of an ^NKG edge interns BOTH ends before writing it, and
  2. the set of writers is the one this was checked against.

Neither can be run offline, so both are pinned here as source facts: a new writer of
^NKG(-1) / ^NKG(-2) / ^NKG(-3), or a writer that stopped interning an end, fails
this file and sends whoever added it back to the argument in
`Graph.KG.Eraser:NKGMayHoldAny`. The behaviour itself is proven against the server in
tests/e2e/test_erase_nkg_kept_e2e.py.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"
PKG = Path(__file__).resolve().parents[2] / "iris_vector_graph"

#: Where an ^NKG edge, degree or sketch may be written, and why each was checked.
#: Adjacency.cls  Put: interns sIdx and oIdx first.
#: TraversalBuild BuildNKG: interns src and dst first.
#: GraphIndex     DeleteIndex only removes (Kill, $Increment -1) and returns early unless
#:                both ends are interned.
#: The Rust kg_build_nkg (arno-callout) is outside this repo; it builds node_map from the
#: same ^KG("out") walk it writes edges from, so it interns each end it writes.
NKG_EDGE_WRITERS = {"Adjacency.cls", "TraversalBuild.cls", "GraphIndex.cls"}

_WRITE = re.compile(
    r"(Set\s+\^NKG\(\s*-[123]\b|\$Increment\(\s*\^NKG\(\s*-[123]\b|Merge\s+\^NKG\b|Kill\s+\^NKG\(\s*-[123]\b)"
)


@pytest.fixture(scope="module")
def eraser():
    return (SRC / "Eraser.cls").read_text()


def _strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def _method(source: str, name: str) -> str:
    m = re.search(rf"ClassMethod {name}\(.*?(?=\n(?:/// |ClassMethod )|\n\}}\s*$)", source, re.DOTALL)
    assert m, f"no ClassMethod {name}"
    return m.group(0)


def test_only_the_checked_classes_write_nkg_edges():
    offenders = {}
    for path in sorted(SRC.glob("*.cls")):
        code = _strip_comments(path.read_text())
        hits = [ln.strip() for ln in code.splitlines() if _WRITE.search(ln)]
        if hits and path.name not in NKG_EDGE_WRITERS:
            offenders[path.name] = hits
    assert not offenders, (
        "a class writes ^NKG edges that EraseIdSet's keep-^NKG rule was not checked against: "
        f"{offenders}. Check that it interns BOTH ends, then add it to NKG_EDGE_WRITERS."
    )


def test_python_does_not_write_nkg_edges():
    """The package writes ^NKG only to kill the `q` bucket index and to raise the version
    (snapshot restore); no edge, degree, interning entry or sketch."""
    for path in sorted(PKG.rglob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if '"^NKG"' in line and '"$meta", "version"' not in line and re.search(r"\.(set|setString|increment|merge)\(", line):
                pytest.fail(f"{path.name}:{n} writes ^NKG from Python: {line.strip()}")


@pytest.mark.parametrize(
    "cls,method,ends",
    [
        ("Adjacency.cls", "Put", ("InternNode(pSrc)", "InternNode(pDst)")),
        ("TraversalBuild.cls", "BuildNKG", ("InternNode(src)", "InternNode(dst)")),
    ],
)
def test_edge_writers_intern_both_ends_before_writing(cls, method, ends):
    body = _strip_comments(_method((SRC / cls).read_text(), method))
    write = body.index("Set ^NKG(-1,")
    for end in ends:
        assert end in body, f"{cls}:{method} no longer calls {end}"
        assert body.index(end) < write, f"{cls}:{method} writes an ^NKG edge before {end}"


def test_delete_index_ignores_an_edge_with_an_uninterned_end():
    body = _strip_comments(_method((SRC / "GraphIndex.cls").read_text(), "DeleteIndex"))
    assert '(sIdx = "") || (oIdx = "")' in body


def test_edge_row_erase_keeps_nkg_only_through_the_interning_check(eraser):
    code = _strip_comments(_method(eraser, "EraseIdSet"))
    assert "InvalidateDerived(tHadEdges && tNkgAtRisk)" in code
    assert "InvalidateDerived(tHadEdges)" not in code
    # Both checks are there: the row's own ends, and the erased ids.
    assert "NodeInterned($ListGet(tEdge(tEid), 1)) && ..NodeInterned($ListGet(tEdge(tEid), 3))" in code
    assert "NKGMayHoldAny()" in code
    # The ids are looked at after the removal, not before it.
    assert code.index("Adjacency).RemoveNode") < code.index("NKGMayHoldAny()")


def test_every_other_erase_still_drops_unconditionally(eraser):
    """Narrowing EraseIdSet must not have narrowed anything else."""
    calls = {
        "EraseGraph": "InvalidateDerived(tHadEdges)",
        "EraseEdges": "InvalidateDerived(1)",
        "EraseAll": "InvalidateDerived(1)",
    }
    for name, call in calls.items():
        assert call in _strip_comments(_method(eraser, name)), f"{name} no longer calls {call}"


def test_version_bump_and_cache_purge_do_not_depend_on_the_drop(eraser):
    body = _strip_comments(_method(eraser, "InvalidateDerived"))
    guarded = re.search(r"If pTouchedStructural \{(.*?)\n\t\}", body, re.DOTALL).group(1)
    assert "DropNKG" in guarded
    assert "BumpVersion" not in guarded and "AlgoCache" not in guarded
    assert "ArnoAccel).BumpVersion()" in body
    assert "Kill ^IVG.AlgoCache" in body


def test_unreadable_subscript_counts_as_interned(eraser):
    """The check may over-drop; it may never keep a stale index."""
    body = _strip_comments(_method(eraser, "NodeInterned"))
    assert "Set tInterned = 1" in body
    assert 'Set tInterned = ($Data(^NKG("$NI", pId)) > 0)' in body
    assert "Catch" in body
