"""Spec 254: a canonical digest of every store one graph's content lives in, for rebuild and reference comparisons.

The digest is computed in IRIS by ``Graph.KG.GraphStores.Digest`` (a Python walk of the globals costs about 300 us an
entry over the Native API). It covers every graph-scoped store of ``GraphStores.Inventory()``; the graph key itself is
not part of an entry, so two graphs holding the same content digest the same. The tests that use it name the stores
they leave out and why; ``tests/e2e/test_254_rebuild_e2e.py`` proves the digest moves when one entry does.
"""

from __future__ import annotations

import json

#: Reader-filled caches (ADR-0012): "Kill it and every answer is still right", so not content.
CACHES = frozenset({'^KG("degc")', '^KG("degw")'})
#: The bucket aggregates, which spec 254's purge leaves in place by design.
AGGREGATES = frozenset({'^KG("tagg")', '^KG("bucket")'})


def digest(native, graph) -> dict:
    """{store: {"entries": n, "sha256": hex}} for one graph, plus "skipped" stores and the reason."""
    raw = native.classMethodValue("Graph.KG.GraphStores", "Digest", graph or "")
    return json.loads(str(raw))


def compare(a: dict, b: dict, *, exclude=frozenset()) -> list:
    """The stores whose digests differ, with both entry counts; [] when equal."""
    out = []
    for name in sorted(set(a["stores"]) | set(b["stores"])):
        if name in exclude:
            continue
        x, y = a["stores"].get(name), b["stores"].get(name)
        if x != y:
            out.append((name, (x or {}).get("entries"), (y or {}).get("entries")))
    return out
