"""Source scan: the traversal readers no longer read the stored degree counters.

`^KG("deg", g, s)` and `^KG("degp", g, s, p)` are advisory at best (CONTEXT.md,
Degree: never stored, because a stored counter cannot be kept exact inside a
transaction). The readers in the five classes below must answer from the `out`
entries instead: a counter that drifted must not drop a node or a predicate from a
traversal, and a counter that drifted high must not change a degree.

The scan is over ObjectScript source with comments removed. It flags any
`^KG("deg"` or `^KG("degp"` (not `"deg2p"`, which is a different store).

Spec 248 removed the counters' last writers, so `ALLOWED_WRITER_LINES` is empty: any
reference to `^KG("deg"` or `^KG("degp"` in these classes is a failure.
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

#: (file, stripped source line) -> why it may stay. EMPTY since spec 248: the rebuild no
#: longer writes the counters (BuildKG goes through Graph.KG.Adjacency and leaves the
#: degree cache warm), and BackfillDegp warms the cache instead of rebuilding counters.
#: Changed on purpose: these lines were the counter writers of TraversalBuild, and the
#: counters are gone. A line that reappears is a reader or writer of the removed store.
ALLOWED_WRITER_LINES: dict[tuple[str, str], str] = {}


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


def test_allow_list_is_empty():
    """No file is exempt: the stored counters are written nowhere (spec 248)."""
    assert ALLOWED_WRITER_LINES == {}


def test_deg2p_is_not_the_counter():
    """`^KG("deg2p"` is the two-hop statistic, a different store: the pattern must not match it."""
    assert STORED_COUNTER.search('Set x = $Get(^KG("deg2p", g, s, p))') is None
    assert STORED_COUNTER.search('Set x = $Get(^KG("degp", g, s, p))') is not None
    assert STORED_COUNTER.search('Set x = $Get(^KG("deg", g, s))') is not None
