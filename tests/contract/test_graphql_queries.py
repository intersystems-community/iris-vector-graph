"""Contract tests for the GraphQL example queries.

Every query text below is copied from
`specs/archive/003-add-graphql-endpoint/contracts/example_queries.graphql`. The point of
the file is that the *published* schema accepts them.

They were TDD gates that never got unskipped: one asserted
`from api.graphql.schema import schema` raises `ImportError` — which it does forever,
since the module is `api.gql` — and the rest carried
`@pytest.mark.skip("Will be unskipped when schema is implemented")` while the schema
shipped underneath them. The docstring even said "will fail on EXECUTION until resolvers
are implemented"; the resolvers landed in spec 003 and nobody came back.

Validation, not execution: these run `graphql.validate` against the schema rather than
`execute_sync`, so they assert the contract's shape without needing a database. The
exception is the depth limit, which strawberry enforces during the validation phase and
which therefore answers without touching a resolver.
"""

import graphql
import pytest

from api.gql.schema import schema


def _validation_errors(query: str) -> list:
    return list(graphql.validate(schema._schema, graphql.parse(query)))


def _assert_valid(query: str) -> None:
    errors = _validation_errors(query)
    assert errors == [], f"contract query rejected by the shipped schema: {errors}"


class TestQueryValidationContract:
    def test_simple_protein_query(self):
        """Query 1: GetProtein"""
        _assert_valid(
            """
            query GetProtein {
                protein(id: "PROTEIN:TP53") {
                    id
                    name
                    function
                    organism
                    confidence
                }
            }
            """
        )

    def test_nested_interactions_query(self):
        """Query 2: ProteinWithInteractions"""
        _assert_valid(
            """
            query ProteinWithInteractions {
                protein(id: "PROTEIN:TP53") {
                    id
                    name
                    function
                    interactsWith(first: 5) {
                        id
                        name
                        function
                    }
                }
            }
            """
        )

    def test_vector_similarity_query(self):
        """Query 4: SimilarProteins"""
        _assert_valid(
            """
            query SimilarProteins {
                protein(id: "PROTEIN:TP53") {
                    name
                    similar(limit: 10, threshold: 0.8) {
                        protein {
                            id
                            name
                            function
                        }
                        similarity
                        distance
                    }
                }
            }
            """
        )

    def test_graph_stats_query_ships_as_stats(self):
        """Query 10: Stats — the contract's `graphStats` is spelled `stats`.

        The `GraphStats` type itself matches the contract field for field; only the root
        field name diverges. Asserted as shipped, because renaming a published field is a
        breaking API change and belongs in a release note. Recorded in
        docs/KNOWN_ISSUES.md.
        """
        body = """
            {
                %s {
                    totalNodes
                    totalEdges
                    nodesByLabel
                    edgesByType
                }
            }
        """
        assert _validation_errors(body % "graphStats") != []
        _assert_valid(body % "stats")


class TestFragmentContract:
    def test_fragment_query(self):
        """Query 14: WithFragments"""
        _assert_valid(
            """
            fragment ProteinDetails on Protein {
                id
                name
                function
                organism
                confidence
            }

            query WithFragments {
                protein(id: "PROTEIN:TP53") {
                    ...ProteinDetails
                    interactsWith(first: 5) {
                        ...ProteinDetails
                    }
                }
            }
            """
        )


class TestInterfaceQueryContract:
    def test_interface_query(self):
        """Query 15: InterfaceQuery — inline fragment on the Node interface."""
        _assert_valid(
            """
            query InterfaceQuery {
                node(id: "PROTEIN:TP53") {
                    id
                    labels
                    createdAt
                    ... on Protein {
                        name
                        function
                    }
                }
            }
            """
        )


class TestDepthLimitContract:
    """The contract's 10-level depth limit, which shipped unenforced.

    `specs/archive/003-add-graphql-endpoint/tasks.md:586` specifies it, quickstart.md:631
    ticks it off as done, and `api/gql/schema.py` passed
    `extensions=[DatabaseConnectionExtension]` — nothing else. On a graph API that is not
    cosmetic: `interactsWith` is recursive, so an unauthenticated client can nest it as
    far as it likes and make one request fan out into an unbounded number of round trips.
    Same class of hole as the missing hop cap on `/api/cypher`.
    """

    def test_contract_depth_is_allowed(self):
        """Query 3: DeepNesting — three levels, well inside the limit."""
        result = schema.execute_sync(
            """
            query DeepNesting {
                protein(id: "PROTEIN:TP53") {
                    name
                    interactsWith(first: 3) {
                        name
                        interactsWith(first: 3) {
                            name
                            interactsWith(first: 3) {
                                name
                            }
                        }
                    }
                }
            }
            """
        )
        depth_errors = [e for e in (result.errors or []) if "depth" in str(e).lower()]
        assert depth_errors == [], f"a 4-deep query must not be rejected: {depth_errors}"

    def test_excessive_depth_is_rejected(self):
        """A query past the limit is refused during validation, before any resolver runs."""
        nesting = "interactsWith(first: 2) { name "
        query = (
            "query TooDeep { protein(id: \"PROTEIN:TP53\") { name "
            + nesting * 12
            + "}" * 13
            + "}"
        )

        result = schema.execute_sync(query)

        assert result.errors, "a 13-deep query must be rejected"
        assert any("depth" in str(e).lower() for e in result.errors), (
            f"rejection must name the depth limit; got {result.errors}"
        )

    def test_limit_is_configurable(self):
        """`IVG_GRAPHQL_MAX_DEPTH` sets the cap, as `IVG_CYPHER_MAX_HOPS` does for Cypher."""
        from api.gql.schema import _max_query_depth

        assert _max_query_depth({}) == 10
        assert _max_query_depth({"IVG_GRAPHQL_MAX_DEPTH": "4"}) == 4

    @pytest.mark.parametrize("bad", ["0", "-1", "", "ten"])
    def test_unusable_limit_falls_back_to_the_default(self, bad):
        """A depth cap is a safety limit; a typo in the env must not disable it."""
        from api.gql.schema import _max_query_depth

        assert _max_query_depth({"IVG_GRAPHQL_MAX_DEPTH": bad}) == 10
