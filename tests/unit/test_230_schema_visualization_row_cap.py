"""Spec 230 US5 — `get_schema_visualization` must cap every query it fetches once.

The method walks labels and relationship types, and for each one runs a query and
reads a single row with `fetchone()`, reusing one cursor throughout. Every such
query caps itself with `TOP` except the relationship-endpoint lookup, which ran

    SELECT s, o_id FROM Graph_KG.rdf_edges WHERE p = ?

unbounded, took `fetchone()`, and then issued the next `execute()` on the same
cursor with the rest of the result set still pending. That desynchronizes the IRIS
wire protocol:

    <COMMUNICATION ERROR> Message out of order; Details: Invalid Message Sequence
    Number: expected: 293 got: 291

and the connection is closed behind it, so the *caller's* connection dies — every
later statement on it raises `<COMMUNICATION LINK ERROR> Connection closed`,
including work that has nothing to do with this method.

It hid for as long as it did because a small fixture's whole result set arrives in
one buffer, leaving nothing pending to desynchronize. It appears once some
predicate has more edges than one fetch returns, which is any real graph.

`tests/e2e/test_untested_methods.py` carried a `pytest.skip` for "Message out of
order" — the symptom was known and read as transient. It is neither transient nor
a driver fault.
"""

from __future__ import annotations

import inspect
import re

from iris_vector_graph._engine.schema import SchemaMixin

_SELECTS = re.compile(r"""["']\s*(SELECT\b[^"']*)["']""", re.IGNORECASE)


def _visualization_selects() -> list[str]:
    """The SELECT text literals in `get_schema_visualization`'s body.

    Read from the source rather than from a recording mock: the defect is that one
    statement among several lacks a cap, and a mock would have to know which call
    index to look at, which is the thing under test.
    """
    src = inspect.getsource(SchemaMixin.get_schema_visualization)
    # Statements are built from adjacent literals in places, so join continuations
    # before matching: a cap may sit in the second fragment.
    joined = re.sub(r"['\"]\s*\n\s*['\"]", "", src)
    return [m.group(1).strip() for m in _SELECTS.finditer(joined)]


def test_the_scan_finds_the_selects_it_is_meant_to_check():
    selects = _visualization_selects()
    assert len(selects) >= 5, f"expected several SELECTs, found {selects}"


def test_every_select_in_the_visualization_is_capped():
    """One uncapped SELECT plus `fetchone()` on a shared cursor kills the connection."""
    uncapped = [
        s
        for s in _visualization_selects()
        if not re.search(r"\bTOP\s+\d+\b", s, re.IGNORECASE)
        and not re.search(r"\bDISTINCT\b", s, re.IGNORECASE)
    ]
    assert not uncapped, (
        "these SELECTs are read with fetchone() on a shared cursor and must cap "
        "themselves with TOP, or the undrained rows desynchronize the connection:\n"
        + "\n".join(f"  {s}" for s in uncapped)
    )


def test_the_relationship_endpoint_lookup_reads_one_row():
    """The specific statement that carried the defect, named so a regression is legible."""
    endpoint = [s for s in _visualization_selects() if "o_id" in s.lower()]
    assert endpoint, "the relationship-endpoint lookup is gone; update this test"
    for sql in endpoint:
        assert re.search(r"\bTOP\s+1\b", sql, re.IGNORECASE), (
            f"the endpoint lookup takes one row via fetchone() but asks for all of them: {sql}"
        )
