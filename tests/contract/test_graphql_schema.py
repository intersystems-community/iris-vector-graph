"""
Contract Tests for GraphQL Schema

These tests validate that the GraphQL schema matches the contract specification.
They MUST FAIL until the schema is implemented (T007-T014).

Per TDD principles: Write tests first, watch them fail, then implement.
"""

import pytest
from pathlib import Path


class TestGraphQLSchemaContract:
    """Test schema introspection matches contract"""

    def test_schema_module_not_implemented_yet(self) -> None:
        """This test ensures schema doesn't exist yet (TDD gate)"""
        with pytest.raises(ImportError):
            from api.graphql.schema import schema  # noqa: F401

    def test_types_module_not_implemented_yet(self) -> None:
        """This test ensures types don't exist yet (TDD gate)"""
        with pytest.raises(ImportError):
            from api.graphql.types import Node, Protein  # noqa: F401


class TestNodeInterfaceContract:
    """Test Node interface introspection"""

    def test_node_interface_not_implemented_yet(self) -> None:
        """This test ensures Node interface doesn't exist yet (TDD gate)"""
        with pytest.raises(ImportError):
            from api.graphql.types import Node  # noqa: F401

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_node_interface_has_required_fields(self) -> None:
        """Node interface must have: id, labels, properties, createdAt"""
        from api.graphql.schema import schema

        introspection_query = """
        {
            __type(name: "Node") {
                kind
                fields {
                    name
                    type {
                        name
                        kind
                    }
                }
            }
        }
        """

        # This will fail until schema is implemented
        result = schema.execute_sync(introspection_query)
        assert result.errors is None
        assert result.data is not None

        node_type = result.data["__type"]
        assert node_type["kind"] == "INTERFACE"

        field_names = [f["name"] for f in node_type["fields"]]
        assert "id" in field_names
        assert "labels" in field_names
        assert "properties" in field_names
        assert "createdAt" in field_names

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_protein_implements_node_interface(self) -> None:
        """Protein type must implement Node interface"""
        from api.graphql.schema import schema

        introspection_query = """
        {
            __type(name: "Protein") {
                name
                kind
                interfaces {
                    name
                }
                fields {
                    name
                }
            }
        }
        """

        result = schema.execute_sync(introspection_query)
        assert result.errors is None
        assert result.data is not None

        protein_type = result.data["__type"]
        assert protein_type["kind"] == "OBJECT"

        interface_names = [i["name"] for i in protein_type["interfaces"]]
        assert "Node" in interface_names

        # Verify Node fields are present
        field_names = [f["name"] for f in protein_type["fields"]]
        assert "id" in field_names
        assert "labels" in field_names
        assert "properties" in field_names
        assert "createdAt" in field_names

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_all_entities_implement_node_interface(self) -> None:
        """Protein, Gene, Pathway, Variant must all implement Node"""
        from api.graphql.schema import schema

        entity_types = ["Protein", "Gene", "Pathway", "Variant"]

        for entity_type in entity_types:
            introspection_query = f"""
            {{
                __type(name: "{entity_type}") {{
                    name
                    interfaces {{
                        name
                    }}
                }}
            }}
            """

            result = schema.execute_sync(introspection_query)
            assert result.errors is None
            assert result.data is not None

            type_info = result.data["__type"]
            interface_names = [i["name"] for i in type_info["interfaces"]]
            assert "Node" in interface_names, f"{entity_type} must implement Node interface"


class TestCustomScalarsContract:
    """Test custom scalar types (JSON, DateTime)"""

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_json_scalar_registered(self) -> None:
        """JSON scalar must be registered in schema"""
        from api.graphql.schema import schema

        introspection_query = """
        {
            __type(name: "JSON") {
                name
                kind
            }
        }
        """

        result = schema.execute_sync(introspection_query)
        assert result.errors is None
        assert result.data is not None

        json_scalar = result.data["__type"]
        assert json_scalar["name"] == "JSON"
        assert json_scalar["kind"] == "SCALAR"

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_datetime_scalar_registered(self) -> None:
        """DateTime scalar must be registered in schema"""
        from api.graphql.schema import schema

        introspection_query = """
        {
            __type(name: "DateTime") {
                name
                kind
            }
        }
        """

        result = schema.execute_sync(introspection_query)
        assert result.errors is None
        assert result.data is not None

        datetime_scalar = result.data["__type"]
        assert datetime_scalar["name"] == "DateTime"
        assert datetime_scalar["kind"] == "SCALAR"


class TestRootTypesContract:
    """Test Query, Mutation, Subscription root types"""

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_query_root_defined(self) -> None:
        """Query root type must be defined"""
        from api.graphql.schema import schema

        introspection_query = """
        {
            __schema {
                queryType {
                    name
                    fields {
                        name
                    }
                }
            }
        }
        """

        result = schema.execute_sync(introspection_query)
        assert result.errors is None
        assert result.data is not None

        query_type = result.data["__schema"]["queryType"]
        assert query_type["name"] == "Query"

        field_names = [f["name"] for f in query_type["fields"]]
        assert "protein" in field_names
        assert "gene" in field_names
        assert "pathway" in field_names
        assert "graphStats" in field_names

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_mutation_root_defined(self) -> None:
        """Mutation root type must be defined"""
        from api.graphql.schema import schema

        introspection_query = """
        {
            __schema {
                mutationType {
                    name
                    fields {
                        name
                    }
                }
            }
        }
        """

        result = schema.execute_sync(introspection_query)
        assert result.errors is None
        assert result.data is not None

        mutation_type = result.data["__schema"]["mutationType"]
        assert mutation_type["name"] == "Mutation"

        field_names = [f["name"] for f in mutation_type["fields"]]
        assert "createProtein" in field_names
        assert "updateProtein" in field_names
        assert "deleteProtein" in field_names

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_subscription_root_defined(self) -> None:
        """Subscription root type must be defined"""
        from api.graphql.schema import schema

        introspection_query = """
        {
            __schema {
                subscriptionType {
                    name
                    fields {
                        name
                    }
                }
            }
        }
        """

        result = schema.execute_sync(introspection_query)
        assert result.errors is None
        assert result.data is not None

        subscription_type = result.data["__schema"]["subscriptionType"]
        assert subscription_type["name"] == "Subscription"

        field_names = [f["name"] for f in subscription_type["fields"]]
        assert "proteinCreated" in field_names
        assert "proteinUpdated" in field_names
        assert "interactionCreated" in field_names


class TestFieldSignaturesContract:
    """Test field signatures match contract"""

    @pytest.mark.skip(reason="Will be unskipped when schema is implemented")
    def test_protein_query_signature(self) -> None:
        """protein(id: ID!): Protein signature must match contract"""
        from api.graphql.schema import schema

        introspection_query = """
        {
            __type(name: "Query") {
                fields {
                    name
                    args {
                        name
                        type {
                            kind
                            name
                            ofType {
                                kind
                                name
                            }
                        }
                    }
                    type {
                        kind
                        name
                    }
                }
            }
        }
        """

        result = schema.execute_sync(introspection_query)
        assert result.errors is None
        assert result.data is not None

        query_type = result.data["__type"]
        protein_field = next(f for f in query_type["fields"] if f["name"] == "protein")

        # Verify argument: id: ID!
        assert len(protein_field["args"]) == 1
        id_arg = protein_field["args"][0]
        assert id_arg["name"] == "id"
        assert id_arg["type"]["kind"] == "NON_NULL"
        assert id_arg["type"]["ofType"]["name"] == "ID"

        # Verify return type: Protein (nullable)
        assert protein_field["type"]["kind"] == "OBJECT"
        assert protein_field["type"]["name"] == "Protein"
