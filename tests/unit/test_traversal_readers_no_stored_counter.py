"""Source scan: the traversal readers no longer read the stored degree counters.

`^KG("deg", g, s)` and `^KG("degp", g, s, p)` are advisory at best (CONTEXT.md,
Degree: never stored, because a stored counter cannot be kept exact inside a
transaction). The readers in the five classes below must answer from the `out`
entries instead: a counter that drifted must not drop a node or a predicate from a
traversal, and a counter that drifted high must not change a degree.

The scan is over ObjectScript source with comments removed. It flags any
`^KG("deg"` or `^KG("degp"` (not `"deg2p"`, which is a different store).

Writes are not readers. `TraversalBuild` still writes the counters while it
rebuilds, and the writer package owns those lines, so they are listed in
`ALLOWED_WRITER_LINES` with the reason. The list is exact: a line that moves or
changes fails the test, and so does an entry that no longer exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
KG_DIR = REPO / "iris_src" / "src" / "Graph" / "KG"

READER_CLASSES = (
    "TraversalBFS.cls",
    "TraversalKHop.cls",
    "TraversalBuild.cls",
    "ArnoAccel.cls",
    "NKGAccelAdjacency.cls",
)

STORED_COUNTER = re.compile(r'\^KG\(\s*"degp?"')

WRITER_FILE = "TraversalBuild.cls"
BUILD_KG_WRITES = "BuildKG edge loop writes the advisory counter; writer package owns it"
WRITE_KEYED_TRIPLE_WRITES = "WriteKeyedTriple writes the advisory counter; writer package owns it"
BACKFILL_REBUILDS = "BackfillDegp rebuilds the advisory counter from scratch"
BACKFILL_WRITES = "BackfillDegp writes the advisory counter; writer package owns it"

#: (file, stripped source line) -> why it may stay. These are the counter writers
#: and the derived-store Kill list of the rebuild, not readers.
ALLOWED_WRITER_LINES = {
    (WRITER_FILE, 'Kill ^KG("label"), ^KG("prop"), ^KG("out"), ^KG("in"), ^KG("deg"), ^KG("degp")'): (
        "rebuild Kill of the derived stores before a rebuild (the existing Kill list)"
    ),
    (WRITER_FILE, 'Set tmp = $Increment(^KG("deg", g, s))'): BUILD_KG_WRITES,
    (WRITER_FILE, 'Set tmp = $Increment(^KG("degp", g, s, p))'): BUILD_KG_WRITES,
    (WRITER_FILE, 'Set tmp = $Increment(^KG("deg", g, s), pN)'): WRITE_KEYED_TRIPLE_WRITES,
    (WRITER_FILE, 'Set tmp = $Increment(^KG("degp", g, s, p), pN)'): WRITE_KEYED_TRIPLE_WRITES,
    (WRITER_FILE, 'Kill ^KG("deg")'): BACKFILL_REBUILDS,
    (WRITER_FILE, 'Kill ^KG("degp")'): BACKFILL_REBUILDS,
    (WRITER_FILE, 'Set tmp = $Increment(^KG("deg", g, src), deg)'): BACKFILL_WRITES,
    (WRITER_FILE, 'Set ^KG("degp", g, src, pred) = deg'): BACKFILL_WRITES,
}


def _stored_counter_hits(name: str) -> list[tuple[int, str]]:
    """(line number, stripped source without `//` comments) for each stored-counter line."""
    hits = []
    for number, raw in enumerate((KG_DIR / name).read_text().splitlines(), start=1):
        code = re.sub(r"//.*$", "", raw).strip()
        if STORED_COUNTER.search(code):
            hits.append((number, code))
    return hits


@pytest.mark.parametrize("name", READER_CLASSES)
def test_reader_class_exists(name):
    assert (KG_DIR / name).is_file(), f"{name} is missing from {KG_DIR}"


@pytest.mark.parametrize("name", READER_CLASSES)
def test_no_stored_counter_read(name):
    """Every stored-counter reference in the class is an allow-listed writer line."""
    readers = [
        (number, code)
        for number, code in _stored_counter_hits(name)
        if (name, code) not in ALLOWED_WRITER_LINES
    ]
    assert readers == [], (
        f'{name} reads ^KG("deg")/^KG("degp") (line, source): {readers}. '
        "Count from the out entries (Graph.KG.Adj) or from the rebuild loop instead."
    )


def test_allow_list_has_no_stale_entries():
    """An allow-listed line that no longer exists in its file is removed, not kept."""
    present = {
        (name, code)
        for name in READER_CLASSES
        for _, code in _stored_counter_hits(name)
    }
    stale = sorted(key for key in ALLOWED_WRITER_LINES if key not in present)
    assert stale == [], f"allow-list entries no longer in the source: {stale}"


def test_allow_list_is_only_writers_in_traversal_build():
    """The exemption is for the rebuild writers only; no reader file is allow-listed."""
    assert {file for file, _ in ALLOWED_WRITER_LINES} == {"TraversalBuild.cls"}


def test_deg2p_is_not_the_counter():
    """`^KG("deg2p"` is the two-hop statistic, a different store: the pattern must not match it."""
    assert STORED_COUNTER.search('Set x = $Get(^KG("deg2p", g, s, p))') is None
    assert STORED_COUNTER.search('Set x = $Get(^KG("degp", g, s, p))') is not None
    assert STORED_COUNTER.search('Set x = $Get(^KG("deg", g, s))') is not None
