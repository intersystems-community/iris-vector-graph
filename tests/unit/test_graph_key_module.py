"""Graph.KG.GraphKey is the only place either graph key is derived.

Two stores key graph content differently, and both derivations were copied
inline across the codebase:

    index  ^KG / ^NKG                 default graph -> integer 0
    ledger ^IVG.Ledger("tuple", ...)  default graph -> $Char(1)

Sixteen copies of the index derivation lived in `TemporalIndex.cls` and
`TemporalIndexMS.cls`, a seventeenth variant in `TraversalBuild.cls`, and the
ledger derivation in `Ledger.GKey()`. A copy is a place the rule can drift, and
one already had: `Ledger.GKey()` never called `$ZStrip`, so a graph name arriving
from IRIS SQL with `$Char(0)` padding was keyed under a distinct ledger subscript
from the same name arriving from Python.

`Graph.KG.GraphKey` owns both derivations and the validation that goes with them.
See ADR-0003.
"""

from pathlib import Path
import re

import pytest

IRIS_SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
GRAPH_KEY = IRIS_SRC / "Graph" / "KG" / "GraphKey.cls"
LEDGER = IRIS_SRC / "Graph" / "KG" / "Ledger.cls"

_ALL_CLASSES = sorted(IRIS_SRC.rglob("*.cls"))

# `$Select(<something> = "": 0, 1: $ZStrip(<something>, "*C"))` — the index
# derivation, in any of its whitespace variants.
_INLINE_INDEX_KEY = re.compile(
    r"\$Select\(\s*\w+\s*=\s*\"\"\s*:\s*0\s*,\s*1\s*:\s*\$ZStrip\(", re.I
)

# `$Select(<something>="":0, 1:<something>)` — the TraversalBuild variant, which
# strips on the preceding line instead of inside the $Select.
_INLINE_INDEX_KEY_BARE = re.compile(
    r"\$Select\(\s*\w+\s*=\s*\"\"\s*:\s*0\s*,\s*1\s*:\s*\w+\s*\)", re.I
)

# `$Select(<something> = "": $Char(1), 1: <something>)` — the ledger derivation.
_INLINE_LEDGER_KEY = re.compile(
    r"\$Select\(\s*\w+\s*=\s*\"\"\s*:\s*\$Char\(1\)", re.I
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_graph_key_class_exists():
    assert GRAPH_KEY.is_file(), f"{GRAPH_KEY} does not exist"


@pytest.mark.parametrize("method", ["ForIndex", "ForLedger", "Validate"])
def test_graph_key_declares_its_interface(method):
    """The three entry points every caller uses."""
    assert f"ClassMethod {method}(" in _source(GRAPH_KEY), (
        f"Graph.KG.GraphKey does not declare {method}()."
    )


@pytest.mark.parametrize(
    "cls_path",
    [p for p in _ALL_CLASSES if p.name != "GraphKey.cls"],
    ids=lambda p: p.name,
)
def test_no_class_derives_the_index_key_inline(cls_path):
    """Only GraphKey may turn a graph name into a ^KG subscript."""
    source = _source(cls_path)
    for pattern in (_INLINE_INDEX_KEY, _INLINE_INDEX_KEY_BARE):
        match = pattern.search(source)
        assert match is None, (
            f"{cls_path.name} derives the ^KG graph key inline "
            f"({match.group(0).strip() if match else ''}). "
            "Call ##class(Graph.KG.GraphKey).ForIndex(graphId) instead."
        )


@pytest.mark.parametrize(
    "cls_path",
    [p for p in _ALL_CLASSES if p.name != "GraphKey.cls"],
    ids=lambda p: p.name,
)
def test_no_class_derives_the_ledger_key_inline(cls_path):
    """Only GraphKey may turn a graph name into a ^IVG.Ledger subscript."""
    match = _INLINE_LEDGER_KEY.search(_source(cls_path))
    assert match is None, (
        f"{cls_path.name} derives the ledger graph key inline "
        f"({match.group(0).strip() if match else ''}). "
        "Call ##class(Graph.KG.GraphKey).ForLedger(graph) instead."
    )


def test_ledger_gkey_delegates_to_graph_key():
    """`Ledger.GKey()` survives as a name but must not hold the rule.

    It is called from `Ledger.cls` itself and reads better in place; what it may
    not do is carry its own copy of the derivation.
    """
    source = _source(LEDGER)
    body = re.search(
        r"ClassMethod GKey\([^)]*\)[^\{]*\{(.*?)\n\}", source, re.S
    )
    assert body is not None, "Cannot find Ledger.GKey() in Ledger.cls"
    assert "Graph.KG.GraphKey" in body.group(1), (
        "Ledger.GKey() does not delegate to Graph.KG.GraphKey.ForLedger(). "
        f"Body was:{body.group(1)}"
    )


def test_temporal_index_calls_for_index():
    """The fourteen former copies in this class are now calls."""
    source = _source(IRIS_SRC / "Graph" / "KG" / "TemporalIndex.cls")
    assert source.count("Graph.KG.GraphKey).ForIndex(") >= 14, (
        "TemporalIndex.cls should call GraphKey.ForIndex() once per method that "
        "used to derive the key inline; found "
        f"{source.count('Graph.KG.GraphKey).ForIndex(')}."
    )
