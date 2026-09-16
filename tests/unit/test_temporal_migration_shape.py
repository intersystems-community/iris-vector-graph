"""`MigrateToGraphScoped` reads the layout, and either finishes or does nothing.

Source-level companion to tests/integration/test_temporal_migration.py, which
proves the behaviour against a container. Two properties are cheap to assert
here and expensive to lose:

1. The migration tells a flat subscript from a graph key by the *depth* of the
   tree below it, not by whether the subscript looks like a Unix timestamp. The
   heuristic version stopped its walk at the integer 0 that keys the default
   graph, because 0 collates before every timestamp, and reported the same 0 an
   already-migrated database reports.

2. A half-migrated temporal index has readers looking at one layout and data at
   two, so the migration runs in a transaction it rolls back on failure rather
   than committing in batches.
"""

from __future__ import annotations

import pathlib
import re

SOURCE = pathlib.Path("iris_src/src/Graph/KG/TemporalIndex.cls")

# The migration is `MigrateToGraphScoped` plus the private helpers it is built
# from. They sit together in the class, so the whole thing can be read as one
# block — and the properties below are properties of the migration, not of
# whichever method happens to hold a given line.
MIGRATION_METHODS = (
    "MigrateToGraphScoped",
    "MigrateRaw",
    "MigrateOrphans",
    "MigrateDerived",
    "RebuildBucket",
    "FlatDepth",
    "LeafDepth",
)


def _entry_point_body() -> str:
    """`MigrateToGraphScoped` itself, where the transaction is opened."""
    lines = SOURCE.read_text().splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("ClassMethod MigrateToGraphScoped(")
    )
    rest = lines[start + 1 :]
    end = next(
        (i for i, line in enumerate(rest) if line.startswith("ClassMethod ")), len(rest)
    )
    return "\n".join(rest[:end])


def _migration_source() -> str:
    """The entry point and every helper it delegates to."""
    lines = SOURCE.read_text().splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("ClassMethod MigrateToGraphScoped(")
    )
    rest = lines[start:]

    def _leaves_the_migration(line: str) -> bool:
        if not line.startswith("ClassMethod "):
            return False
        name = line[len("ClassMethod ") :].split("(")[0]
        return name not in MIGRATION_METHODS

    end = next(
        (i for i, line in enumerate(rest) if i and _leaves_the_migration(line)),
        len(rest),
    )
    return "\n".join(rest[:end])


def test_the_migration_does_not_guess_from_subscript_values():
    body = _migration_source()

    assert "1000000" not in body, (
        "the migration is still deciding what is flat by whether a subscript "
        "looks like a Unix timestamp; the layout is a matter of tree depth"
    )
    assert "$IsValidNum" not in body, (
        "the migration is still filtering entries by whether a node id is "
        "numeric, which silently skips every integer-keyed node"
    )


def test_the_migration_is_one_transaction_it_rolls_back():
    body = _entry_point_body()

    assert re.search(r"If \$TLevel > 0 \{ TROLLBACK \}", body, re.IGNORECASE), (
        "a failed migration leaves half the temporal index at each layout with "
        "no rollback"
    )
    assert not re.search(r"TCOMMIT\s+TSTART", body, re.IGNORECASE), (
        "the migration commits in batches, so a failure part-way leaves readers "
        "looking at one layout and data at two"
    )


def test_the_migration_moves_the_derived_trees_too():
    body = _migration_source()

    for store in ('^KG("bucket"', '^KG("tagg"'):
        assert store in body, (
            f"the migration never touches {store}), so a migrated edge has no "
            "bucket or aggregate a window query can find"
        )
