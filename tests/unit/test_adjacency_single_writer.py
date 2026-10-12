"""One module writes the adjacency stores `^KG("out")` and `^KG("in")` (spec 245).

Each edge is mirrored in `out` and `in` under a derived graph key, and the degree cache
(`^KG("gen")`, `^KG("degc")`) must move with it. When two classes each wrote that pair,
they drifted (see test_adjacency_counter_ownership.py). `Graph.KG.Adjacency` is the one
writer; EdgeScan keeps the historical names Python and tests call as thin delegations;
ALLOW_LIST is empty (spec 248).

Source-level, so it runs without a container. The behaviour is locked by
tests/e2e/test_adjacency_owner_live.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

IRIS_SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
ADJACENCY = IRIS_SRC / "Graph" / "KG" / "Adjacency.cls"
EDGE_SCAN = IRIS_SRC / "Graph" / "KG" / "EdgeScan.cls"

#: Files allowed to Set, Kill, Merge or `$Increment` `^KG("out"`/`^KG("in"` besides
#: Adjacency.cls. EMPTY since spec 248: Eraser reaches the stores through
#: Adjacency.RemoveGraph / RemoveAll / RemoveNode, and TraversalBuild.BuildKG through
#: Adjacency.BeginRebuild / FillEdge / FillTriple / EndRebuild. The single owner is what
#: lets one place bump the degree-cache generation and hold the writer lock. An entry
#: that stops writing fails the scan, so this list cannot go stale; a new entry needs a
#: reason that says why it may bypass the generation and the lock.
ALLOW_LIST: dict[str, str] = {}

REF = r'\^KG\(\s*"(?:out|in)"'
# Command words are case-insensitive in ObjectScript; the subscript string is not.
WRITE_PATTERNS = (
    # Set / Kill / Merge with the store as the first target, abbreviations included.
    re.compile(r"(?<![\w$.])(?i:set|s|kill|k|merge|m)\s+" + REF),
    # $Increment(^KG("out", ...)) and its $I abbreviation.
    re.compile(r"\$(?i:increment|i)\(\s*" + REF),
    # A later target in a Set or Kill list: Kill ^KG("label"), ^KG("out") ...
    # The prefix may hold one level of parentheses; a function argument such as
    # $Get(x, ^KG("out")) has no Set/Kill keyword in front of it and does not match.
    re.compile(r"(?<![\w$.])(?i:set|s|kill|k|merge|m)\s+(?:[^,()]|\([^()]*\))*,\s*" + REF),
)


def _strip_comments(text: str) -> list[str]:
    """Source lines with ObjectScript comments removed.

    Handles `/* ... */` blocks, `//` line comments (including `///` doc lines), and
    `//` after code. String literals are kept: the subscript `"out"` is one.
    """
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    lines = []
    for raw in text.splitlines():
        end = len(raw)
        in_string = False
        for i, ch in enumerate(raw):
            if ch == '"':
                in_string = not in_string
            elif not in_string and raw.startswith("//", i):
                end = i
                break
        lines.append(raw[:end])
    return lines


def _writes_in(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every write to the out/in stores in one file's text."""
    return [
        (number, line.strip())
        for number, line in enumerate(_strip_comments(text), start=1)
        if any(p.search(line) for p in WRITE_PATTERNS)
    ]


def _writers() -> dict[str, list[tuple[int, str]]]:
    """Every .cls under iris_src/src that writes out/in, keyed by file name."""
    writers = {}
    for path in sorted(IRIS_SRC.rglob("*.cls")):
        hits = _writes_in(path.read_text(encoding="utf-8", errors="replace"))
        if hits:
            writers[path.name] = hits
    return writers


# --- the scanner itself ------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'Set ^KG("out", g, s, p, o) = 1',
        'Set ^KG("in",g,o,p,s)=w',
        'Kill ^KG("in")',
        'k ^KG("out", g, s)',
        'Merge ^KG("out", 0) = ^KG("out", 9)',
        'Set tmp = $Increment(^KG("out", g, s, p, o, "#"))',
        'Set tmp = $I(^KG("in", g, o, p, s), -1)',
        'Kill ^KG("label"), ^KG("out")',
        'Set a = 1, ^KG("in", g, o) = 2',
    ],
)
def test_scanner_flags_a_write(line):
    assert _writes_in(line), f"scanner missed a write: {line}"


@pytest.mark.parametrize(
    "line",
    [
        'Set x = $Get(^KG("out", g, s, p, o), 1.0)',
        'Set o = $Order(^KG("out", g, s, p, o))',
        'Set t = $Data(^KG("in", g, o))',
        'Set x = $Get(y, ^KG("out", 0))',
        'Set ^KG("tout", g, ts) = 1',
        'Kill ^KG("tin", g)',
        '// Set ^KG("out", g, s) = 1 is prose',
        '/// Kill ^KG("in") in a doc line',
        'Set s = "^KG(""out"") is text"',
    ],
)
def test_scanner_ignores_reads_comments_and_other_stores(line):
    assert not _writes_in(line), f"scanner flagged a non-write: {line}"


# --- the rule ----------------------------------------------------------------


def test_only_adjacency_and_the_allow_list_write_the_out_and_in_stores():
    writers = _writers()
    allowed = {"Adjacency.cls"} | set(ALLOW_LIST)
    offenders = {name: hits for name, hits in writers.items() if name not in allowed}
    assert not offenders, (
        "These files write ^KG(\"out\")/^KG(\"in\") but are neither Graph.KG.Adjacency nor "
        "on ALLOW_LIST. Route the write through Graph.KG.Adjacency:\n"
        + "\n".join(
            f"  {name}:{number}: {line}"
            for name, hits in sorted(offenders.items())
            for number, line in hits
        )
    )


def test_adjacency_is_the_writer():
    assert ADJACENCY.exists(), "iris_src/src/Graph/KG/Adjacency.cls is missing"
    assert "Adjacency.cls" in _writers(), "Adjacency.cls writes no out/in store"


def test_every_allow_list_entry_still_writes_and_has_a_reason():
    writers = _writers()
    for name, reason in ALLOW_LIST.items():
        assert reason.strip(), f"{name} is on ALLOW_LIST without a reason"
        assert name in writers, (
            f"{name} is on ALLOW_LIST but no longer writes out/in. Remove the entry."
        )


# --- EdgeScan delegates -------------------------------------------------------


def _method_body(source: str, name: str) -> str:
    start = source.index(f"ClassMethod {name}(")
    rest = source[start + 1 :]
    end = rest.find("\nClassMethod ")
    return rest if end == -1 else rest[:end]


@pytest.mark.parametrize(
    "name",
    ["WriteAdjacency", "WriteAdjacencyKeyed", "DeleteAdjacency", "DeleteAdjacencyKeyed"],
)
def test_edgescan_primitive_delegates_to_adjacency(name):
    body = _method_body(EDGE_SCAN.read_text(encoding="utf-8"), name)
    assert "##class(Graph.KG.Adjacency)." in body, (
        f"EdgeScan.{name} does not delegate to Graph.KG.Adjacency"
    )
    assert not _writes_in(body), f"EdgeScan.{name} still writes out/in itself"


def test_edgescan_no_longer_holds_the_moved_private_helpers():
    source = EDGE_SCAN.read_text(encoding="utf-8")
    for name in ("SetLeafFromLowest", "OtherEdgeKey"):
        assert f"ClassMethod {name}(" not in source, (
            f"EdgeScan still defines {name}; it moved to Graph.KG.Adjacency."
        )


# --- Adjacency's public shape -------------------------------------------------

#: name -> parameter names in order, and whether it returns %Status.
SIGNATURES = {
    "Put": (["pGraph", "pSrc", "pPred", "pDst", "pWeight"], True),
    "PutShadow": (["pGraph", "pSrc", "pPred", "pDst", "pWeight"], True),
    "PutKeyed": (["pGraph", "pSrc", "pPred", "pDst", "pEkey", "pWeight"], True),
    "Remove": (["pGraph", "pSrc", "pPred", "pDst"], True),
    "RemoveKeyed": (["pGraph", "pSrc", "pPred", "pDst", "pEkey"], True),
    "Reweight": (["pGraph", "pSrc", "pPred", "pDst", "pWeight"], True),
    # spec 248: the erase and rebuild entry points that replaced the allow-list
    "RemoveSource": (["pGraph", "pSrc"], True),
    "RemoveNode": (["pGraph", "pNode"], True),
    "RemoveGraph": (["pGraph"], True),
    "RemoveAll": ([], True),
    "BeginRebuild": ([], True),
    "FillEdge": (["pGraph", "pSrc", "pPred", "pDst", "pWeight"], False),
    "EndRebuild": (["pWarm"], True),
}


@pytest.mark.parametrize("name", sorted(SIGNATURES))
def test_adjacency_declares_the_primitive(name):
    source = ADJACENCY.read_text(encoding="utf-8")
    expected_names, returns_status = SIGNATURES[name]
    match = re.search(rf"ClassMethod {name}\(([^)]*)\)\s*(As %Status)?", source)
    assert match, f"Adjacency has no ClassMethod {name}"
    names = [p.strip().split()[0].split("=")[0].strip() for p in match.group(1).split(",") if p.strip()]
    assert names == expected_names, f"Adjacency.{name} parameters are {names}"
    if returns_status:
        assert match.group(2) == "As %Status", f"Adjacency.{name} must return %Status"
