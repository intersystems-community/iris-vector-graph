"""The published GraphQL contract must describe the schema that ships (spec 230, FR-023).

`specs/archive/003-add-graphql-endpoint/contracts/` is the contract a client reads before
writing a query. Two of its statements are false against `api.gql`:

- it names the stats root field `graphStats`; `CoreQuery` serves `stats`
  (`api/gql/core/resolvers.py:275`), so a client following the contract gets a validation
  error on a field the server has;
- it declares a `Subscription` root with six events and wires `subscription: Subscription`
  into the schema definition. Nothing implements them and no transport carries them —
  `strawberry.Schema(...)` is constructed with no `subscription=` argument.

`tests/contract/test_graphql_schema.py` already asserts the shipped side of both (`stats`
present, `graphStats` absent, no Subscription root). It cannot fix the contract, because a
contract is a document: the divergence closes by editing the document. These tests read
the document.

The direction of the fix is not symmetric. `stats` is renamed *in the contract*, because
renaming a published field breaks every client that already calls it. The Subscription
root stays in the contract and is marked unimplemented, because deleting it would erase
the record of what was specified — and a client needs to know the difference between "not
specified" and "specified, not built".

Unit-only: reading text off disk, no server and no schema import.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "specs" / "archive" / "003-add-graphql-endpoint" / "contracts"

#: What a reader must be able to find to know a field is specified but not served. One
#: spelling, checked everywhere, so the marker can be grepped.
UNIMPLEMENTED = "UNIMPLEMENTED"


def _text(name: str) -> str:
    path = CONTRACTS / name
    assert path.exists(), f"contract file missing: {path}"
    return path.read_text()


def _block(text: str, header: str) -> str:
    """The lines from `header` to the closing brace of its block, inclusive.

    Crude on purpose: the contract is plain SDL with one type per block and a closing
    brace in column one, so anything cleverer would be a GraphQL parser this test does
    not need.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(header):
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("}"):
                    return "\n".join(lines[i : j + 1])
    pytest.fail(f"no {header!r} block in the contract")


# ---------------------------------------------------------------------------
# The stats field
# ---------------------------------------------------------------------------


def test_the_contract_names_the_stats_field_the_server_serves():
    schema = _text("schema.graphql")
    assert "stats: GraphStats!" in _block(schema, "type Query {")
    assert "graphStats" not in schema


def test_the_example_query_calls_stats():
    """An example a client can paste must be one the server accepts."""
    queries = _text("example_queries.graphql")
    assert "graphStats" not in queries
    assert "stats {" in queries


def test_the_contract_field_is_the_resolvers_name():
    """Tied to the code, so a future rename of either side fails here."""
    resolvers = (REPO_ROOT / "api" / "gql" / "core" / "resolvers.py").read_text()
    assert "async def stats(" in resolvers
    assert "graphStats" not in resolvers


# ---------------------------------------------------------------------------
# The Subscription root
# ---------------------------------------------------------------------------


def test_the_subscription_root_is_marked_unimplemented():
    block = _block(_text("schema.graphql"), "type Subscription {")
    assert UNIMPLEMENTED in block


def test_the_schema_definitions_subscription_line_is_marked_unimplemented():
    """The `schema { ... }` block is what a codegen tool reads."""
    block = _block(_text("schema.graphql"), "schema {")
    line = next(
        (line for line in block.splitlines() if "subscription:" in line),
        None,
    )
    assert line is not None, "the schema definition no longer names a subscription root"
    assert UNIMPLEMENTED in line


def test_the_subscription_examples_are_marked_unimplemented_before_the_first_example():
    """A reader who stops at the first `subscription` keyword still sees the marker."""
    text = _text("example_subscriptions.graphql")
    first_example = text.index("subscription ")
    assert UNIMPLEMENTED in text[:first_example]


def test_the_marker_says_where_the_absence_is_asserted():
    """The marker points at the test that fails the day a Subscription root appears."""
    for name in ("schema.graphql", "example_subscriptions.graphql"):
        text = _text(name)
        assert "test_there_is_no_subscription_root" in text, name


# ---------------------------------------------------------------------------
# Nothing else in the contract claims a field the server does not serve
# ---------------------------------------------------------------------------


def test_the_mutation_and_query_roots_still_declare_their_served_fields():
    """A guard on the fix: renaming one field must not disturb the rest of the block."""
    schema = _text("schema.graphql")
    query = _block(schema, "type Query {")
    for field in ("protein(", "gene(", "pathway(", "similarProteins("):
        assert field in query, field
    mutation = _block(schema, "type Mutation {")
    for field in ("createProtein(", "updateProtein(", "deleteProtein("):
        assert field in mutation, field
