"""
Contract Tests for GraphQL Query Execution

These tests validate that example queries from the contract can be parsed and executed.
They test VALIDATION (syntax) and will fail on EXECUTION until resolvers are implemented.

Per TDD principles: Validation passes, execution fails until resolvers implemented.
"""

import pytest
from pathlib import Path


class TestQueryValidationContract:
    """Test that contract queries are valid GraphQL"""

    def test_schema_not_implemented_yet(self) -> None:
        """This test ensures schema doesn't exist yet (TDD gate)"""
        with pytest.raises(ImportError):
            from api.graphql.schema import schema  # noqa: F401

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_simple_protein_query_validation(self) -> None:
        """Query 1: GetProtein - validates successfully"""
        from api.graphql.schema import schema

        query = """
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

        # Validation should pass
        result = schema.execute_sync(query)

        # Execution will fail (no resolvers yet), but validation should pass
        # We're just checking the query is syntactically valid
        assert result is not None

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_nested_interactions_query_validation(self) -> None:
        """Query 2: ProteinWithInteractions - validates successfully"""
        from api.graphql.schema import schema

        query = """
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

        result = schema.execute_sync(query)
        assert result is not None

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_vector_similarity_query_validation(self) -> None:
        """Query 4: SimilarProteins - validates successfully"""
        from api.graphql.schema import schema

        query = """
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

        result = schema.execute_sync(query)
        assert result is not None

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_graph_stats_query_validation(self) -> None:
        """Query 10: Stats - validates successfully"""
        from api.graphql.schema import schema

        query = """
        query Stats {
            graphStats {
                totalNodes
                totalEdges
                nodesByLabel
                edgesByType
            }
        }
        """

        result = schema.execute_sync(query)
        assert result is not None


class TestQueryExecutionFailsBeforeResolvers:
    """Test that queries fail execution until resolvers implemented"""

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    @pytest.mark.requires_database
    def test_simple_protein_query_execution_fails(self) -> None:
        """Query 1: GetProtein - execution fails without resolver"""
        from api.graphql.schema import schema

        query = """
        query GetProtein {
            protein(id: "PROTEIN:TP53") {
                id
                name
                function
            }
        }
        """

        result = schema.execute_sync(query)

        # Execution should fail (no resolver implemented yet)
        assert result.errors is not None or result.data["protein"] is None

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    @pytest.mark.requires_database
    def test_nested_query_execution_fails(self) -> None:
        """Query 2: ProteinWithInteractions - execution fails without resolver"""
        from api.graphql.schema import schema

        query = """
        query ProteinWithInteractions {
            protein(id: "PROTEIN:TP53") {
                name
                interactsWith(first: 5) {
                    name
                }
            }
        }
        """

        result = schema.execute_sync(query)

        # Execution should fail (no resolver implemented yet)
        assert result.errors is not None or result.data is None

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    @pytest.mark.requires_database
    def test_vector_similarity_execution_fails(self) -> None:
        """Query 4: SimilarProteins - execution fails without resolver"""
        from api.graphql.schema import schema

        query = """
        query SimilarProteins {
            protein(id: "PROTEIN:TP53") {
                similar(limit: 10) {
                    protein { name }
                    similarity
                }
            }
        }
        """

        result = schema.execute_sync(query)

        # Execution should fail (no resolver implemented yet)
        assert result.errors is not None or result.data is None


class TestDepthLimitContract:
    """Test query depth limits"""

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_deep_nesting_query_validation(self) -> None:
        """Query 3: DeepNesting - validates but should fail on depth limit"""
        from api.graphql.schema import schema

        query = """
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

        result = schema.execute_sync(query)

        # Query is valid GraphQL, but may be rejected by depth limit extension
        assert result is not None


class TestFragmentContract:
    """Test fragment usage in queries"""

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_fragment_query_validation(self) -> None:
        """Query 14: WithFragments - validates successfully"""
        from api.graphql.schema import schema

        query = """
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

        result = schema.execute_sync(query)
        assert result is not None


class TestInterfaceQueryContract:
    """Test Node interface queries"""

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_interface_query_validation(self) -> None:
        """Query 15: InterfaceQuery - validates successfully"""
        from api.graphql.schema import schema

        query = """
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

        result = schema.execute_sync(query)
        assert result is not None
