"""`BuildKG` must not destroy the adjacency it is about to rebuild.

Source-level, because the ordering being asserted is a property of the method's
text: the `Kill` of the six structural `^KG` subtrees has to sit INSIDE the
transaction that rewrites them. It used to sit one line above `TStart`, so any
failure in the rebuild — a row whose `graph_id` cannot be a subscript, a
`<STORE>`, a killed process — left the adjacency deleted and unrebuilt, with
nothing for the `Catch`'s `TRollback` to restore. The rows were still there and
`^KG` was empty, which is the same drift the Eraser exists to end (ADR-0004),
arriving from the rebuild path instead of the delete path.

The behavioural half of this is
tests/integration/test_buildkg_atomicity.py — it fails the rebuild for real and
asserts the adjacency survived. This file is what fails fast on a re-ordering,
without a container.
"""

from __future__ import annotations

import pathlib
import re

SOURCE = pathlib.Path("iris_src/src/Graph/KG/TraversalBuild.cls")


def _build_kg_body() -> list[str]:
    """The lines of `BuildKG`, from its signature to the next ClassMethod."""
    lines = SOURCE.read_text().splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("ClassMethod BuildKG(")
    )
    rest = lines[start + 1 :]
    end = next(
        (i for i, line in enumerate(rest) if line.startswith("ClassMethod ")), len(rest)
    )
    return rest[:end]


def _index_of(body: list[str], pattern: str) -> int:
    matches = [i for i, line in enumerate(body) if re.search(pattern, line)]
    assert matches, f"no line matching {pattern!r} in BuildKG"
    return matches[0]


def test_the_structural_kill_is_inside_the_rebuild_transaction():
    body = _build_kg_body()

    tstart = _index_of(body, r"^\s*TStart\b")
    kill = _index_of(body, r'^\s*Kill \^KG\("')

    assert tstart < kill, (
        "BuildKG kills the structural ^KG subtrees before opening its "
        "transaction, so a failure mid-rebuild leaves the adjacency destroyed "
        "with nothing to roll back to"
    )


def test_the_rebuild_transaction_rolls_back_on_failure():
    """The `Kill` being inside a transaction only helps if the Catch rolls back."""
    body = "\n".join(_build_kg_body())

    assert re.search(r"If \$TLevel > 0 \{ TRollback \}", body), (
        "BuildKG has no rollback, so moving the Kill inside TStart changes nothing"
    )
