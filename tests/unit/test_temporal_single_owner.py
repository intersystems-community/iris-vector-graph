"""Graph.KG.TemporalIndex is the only writer of the temporal globals (ADR-0002).

`^KG("tout")`, `^KG("tin")`, `^KG("bucket")`, `^KG("tagg")` and `^KG("edgeprop")` are
the temporal index. Their subscript layout is TemporalIndex's, and ADR-0002 says every
other class goes through its classmethods. Before this package, Graph.KG.Eraser killed
those subtrees itself, so the layout was written down in two places.

The guard is source-level: it scans every `.cls` under `iris_src/src` for a Set, Kill,
Merge or `$Increment` whose target is one of those globals, ignoring comments. It runs
without a container. The behaviour is locked in `tests/e2e/test_temporal_erase_owner.py`.

`ALLOWED_WRITERS` is the explicit exception list. It is empty: no class besides
TemporalIndex writes the temporal globals. An entry needs a reason next to it.
"""

from pathlib import Path
import re

IRIS_SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
KG = IRIS_SRC / "Graph" / "KG"
TEMPORAL_INDEX = KG / "TemporalIndex.cls"
ERASER = KG / "Eraser.cls"

TEMPORAL_GLOBALS = ("tout", "tin", "bucket", "tagg", "edgeprop")

# Relative path (from iris_src/src) -> why that class may write the temporal globals.
ALLOWED_WRITERS: dict[str, str] = {}

_NAMES = "|".join(TEMPORAL_GLOBALS)
_TARGET = r'\^KG\(\s*"(?:' + _NAMES + r')"'

# A write whose target is a temporal subtree. Set and Kill are matched only when the
# target is the lvalue: a `^KG(...)` after `=` is a read (`Set x = $Order(^KG(...))`).
# Abbreviations (s, k, m, $i) are legal ObjectScript, so they are matched too.
_WRITE = re.compile(
    r"(?:"
    r"(?<![\w$])(?i:set|s)(?::[^\s]*)?\s+[^=\n]*?"
    r"|(?<![\w$])(?i:kill|k)(?::[^\s]*)?\s+(?:\([^)]*\)\s*)?"
    r"|(?<![\w$])(?i:merge|m)(?::[^\s]*)?\s+"
    r"|\$(?i:increment|i)\s*\("
    r")" + _TARGET
)


def _strip_comment(line: str) -> str:
    """Drop a `//` comment from one line, leaving `//` inside a string literal alone."""
    in_string = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if in_string and line[i + 1 : i + 2] == '"':
                i += 2  # doubled quote inside a string
                continue
            in_string = not in_string
        elif not in_string and line.startswith("//", i):
            return line[:i]
        i += 1
    return line


def code_only(text: str) -> str:
    """The text with comments removed, so a commented-out Kill is not a write."""
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith(";"):
            lines.append("")
            continue
        lines.append(_strip_comment(line))
    return "\n".join(lines)


def temporal_writes(text: str) -> list[str]:
    return [m.group(0) for m in _WRITE.finditer(code_only(text))]


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _method_body(source: str, name: str) -> str:
    """The text of one ClassMethod, from its signature to the next one."""
    start = source.index(f"ClassMethod {name}(")
    rest = source[start + 1 :]
    end = rest.find("\nClassMethod ")
    return rest if end == -1 else rest[:end]


# --- the guard itself --------------------------------------------------------


def test_the_guard_recognises_every_kind_of_temporal_write():
    """Guard the guard: a regex that matches nothing would pass the scan below."""
    writes = [
        'Kill ^KG("tout", tKey)',
        '\tSet ^KG("tin", pGKey, pTs, pO, pP, pS) = pW',
        'Set tmp = $Increment(^KG("bucket", pGKey, tBucket, pS))',
        'Set tmp = $Increment(^KG("tagg", pGKey, b, s, p, "sum"), pW)',
        'Merge ^KG("tagg", 0, tBucket) = ^KG("tagg", tBucket)',
        'Kill ^KG("edgeprop")',
        'set ^KG("tagg", g, b, s, p, "min") = w',
        'Kill:tFlag ^KG("bucket", g)',
        'Set ^KG("edgeprop", g, ts, s, p, o, key) = val',
    ]
    for line in writes:
        assert temporal_writes(line), f"guard missed a write: {line!r}"


def test_the_guard_ignores_reads_comments_and_other_globals():
    reads = [
        'Set tTS = $Order(^KG("tout", tGKey, tTS))',
        'Set x = ^KG("tagg", tGKey, b, source, predicate, "min")',
        'If $Data(^KG("tout", tGKey, tTS, source)) { Quit 0 }',
        'Set hll = $Get(^KG("tagg", graphKey, bucket, source, predicate, "hll"), ..HLLEmpty())',
        '// Kill ^KG("tout") was here once',
        '; Set ^KG("tout", 0) = 1',
        'Kill ^KG("out", tKey)',
        'Kill ^KG("labelset", hex)',
        'Set ^KG("tagg2", 1) = 2',
        'Do tStores.%Push(..Entry("^KG(""tout"")", "global", "subscript 2", 1))',
    ]
    for line in reads:
        assert not temporal_writes(line), f"guard flagged a non-write: {line!r}"


def test_a_double_slash_inside_a_string_does_not_hide_a_write():
    line = 'Set u = "http://example" Kill ^KG("tout")'
    assert temporal_writes(line), "the comment stripper cut the line at the URL's //"


# --- the single-owner rule ---------------------------------------------------


def test_only_temporal_index_writes_the_temporal_globals():
    offenders = {}
    for path in sorted(IRIS_SRC.rglob("*.cls")):
        rel = path.relative_to(IRIS_SRC).as_posix()
        if path == TEMPORAL_INDEX or rel in ALLOWED_WRITERS:
            continue
        hits = temporal_writes(_source(path))
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "These classes write a temporal global directly. ADR-0002: only "
        "Graph.KG.TemporalIndex may. Call its classmethods instead, or add the "
        f"class to ALLOWED_WRITERS with a reason: {offenders}"
    )


def test_every_allow_list_entry_still_exists_and_still_writes():
    """An exception that no longer applies is a hole waiting to be reused."""
    for rel, reason in ALLOWED_WRITERS.items():
        assert reason.strip(), f"{rel} is allowed without a reason"
        path = IRIS_SRC / rel
        assert path.exists(), f"{rel} is in ALLOWED_WRITERS but does not exist"
        assert temporal_writes(_source(path)), f"{rel} no longer writes; remove its entry"


# --- TemporalIndex has the entry points the Eraser needs ---------------------


def test_temporal_index_declares_the_erase_entry_points():
    src = _source(TEMPORAL_INDEX)
    assert re.search(r"ClassMethod EraseGraph\(pGraph As %String\) As %Status", src)
    assert re.search(r"ClassMethod EraseAll\(\) As %Status", src)
    assert re.search(r"ClassMethod EraseNodes\(pGraph As %String, ByRef pNodeIds\) As %Status", src)


def test_erase_graph_maps_the_key_and_kills_all_five_for_that_key_only():
    body = _method_body(_source(TEMPORAL_INDEX), "EraseGraph")
    assert "##class(Graph.KG.GraphKey).ForIndex(pGraph)" in body
    for name in TEMPORAL_GLOBALS:
        assert f'Kill ^KG("{name}", tGKey)' in body, f"EraseGraph does not kill {name} for the graph key"
    assert "Kill ^KG(\"tout\")" not in body, "EraseGraph kills an unscoped subtree"


def test_erase_all_kills_all_five_unscoped():
    body = _method_body(_source(TEMPORAL_INDEX), "EraseAll")
    for name in TEMPORAL_GLOBALS:
        assert f'Kill ^KG("{name}")' in body, f"EraseAll does not empty {name}"


def test_erase_nodes_checks_both_ends_of_each_temporal_edge():
    body = _method_body(_source(TEMPORAL_INDEX), "EraseNodes")
    assert "$Data(pNodeIds(tS))" in body, "EraseNodes ignores edges whose source is erased"
    assert "$Data(pNodeIds(tO))" in body, "EraseNodes ignores edges whose target is erased"


def test_erase_nodes_does_not_touch_structural_adjacency():
    """The Eraser removes ^KG out/in and the degree counters per node. A second
    DeleteAdjacency here would decrement them twice."""
    body = _method_body(_source(TEMPORAL_INDEX), "EraseNodes")
    assert "DeleteAdjacency" not in body


def test_delete_temporal_edge_shares_the_row_removal():
    body = _method_body(_source(TEMPORAL_INDEX), "DeleteTemporalEdge")
    assert "RemoveTemporalRow(" in body


# --- the Eraser delegates; it no longer owns the layout ----------------------


def test_eraser_erases_temporal_data_only_through_temporal_index():
    src = _source(ERASER)
    assert "##class(Graph.KG.TemporalIndex).EraseGraph(pGraph)" in src
    assert "##class(Graph.KG.TemporalIndex).EraseAll()" in src
    assert "##class(Graph.KG.TemporalIndex).EraseNodes(" in src


def test_eraser_node_erase_passes_the_whole_id_set_in_one_call():
    body = _method_body(_source(ERASER), "EraseIdSet")
    assert "EraseNodes(" in body, "EraseIdSet never erases temporal edges for its nodes"
    assert "tNodeIds(tId)" in body, "EraseIdSet does not collect the id set for one walk"
