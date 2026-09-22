"""Contract tests for the GraphQL schema.

Checked against `specs/archive/003-add-graphql-endpoint/contracts/`.

These were TDD gates for spec 003 and had rotted into decoration. Every test either
asserted `from api.graphql.schema import schema` raises `ImportError` — which it does
forever, because the shipped module is `api.gql`, so the gate passed while the schema it
was gating shipped underneath it — or carried
`@pytest.mark.skip("Will be unskipped when schema is implemented")`, and nobody came back.
Nineteen of the suite's skips lived in this file and its neighbour.

They now run against `api.gql.schema`, which reveals two places where the shipped schema
and the archived contract disagree. Both are asserted as they actually are, with the
divergence named, because a test that skips tells you nothing and one that asserts a wish
fails for the wrong reason:

- the contract's `graphStats` ships as `stats`
- there is no `Subscription` root type at all
"""

import pytest

from api.gql.schema import schema

# The contract's entity types, all of which must implement Node.
ENTITY_TYPES = ["Protein", "Gene", "Pathway", "Variant"]


def _type(name: str) -> dict:
    result = schema.execute_sync(
        """
        query TypeInfo($name: String!) {
            __type(name: $name) {
                name
                kind
                interfaces { name }
                fields { name type { kind name ofType { kind name } } }
            }
        }
        """,
        variable_values={"name": name},
    )
    assert result.errors is None, result.errors
    assert result.data is not None
    return result.data["__type"]


def _root_fields(root: str) -> list:
    result = schema.execute_sync(
        """
        {
            __schema {
                queryType { name fields { name } }
                mutationType { name fields { name } }
                subscriptionType { name }
            }
        }
        """
    )
    assert result.errors is None, result.errors
    return result.data["__schema"][root]


class TestNodeInterfaceContract:
    def test_node_is_an_interface_with_the_contract_fields(self):
        node = _type("Node")
        assert node["kind"] == "INTERFACE"
        field_names = [f["name"] for f in node["fields"]]
        for required in ("id", "labels", "properties", "createdAt"):
            assert required in field_names

    @pytest.mark.parametrize("entity_type", ENTITY_TYPES)
    def test_entity_implements_node(self, entity_type):
        info = _type(entity_type)
        assert info["kind"] == "OBJECT"
        assert "Node" in [i["name"] for i in info["interfaces"]]
        field_names = [f["name"] for f in info["fields"]]
        for required in ("id", "labels", "properties", "createdAt"):
            assert required in field_names, f"{entity_type} is missing {required}"


class TestCustomScalarsContract:
    def test_json_scalar_registered(self):
        json_scalar = _type("JSON")
        assert json_scalar is not None, "JSON scalar is not in the schema"
        assert json_scalar["kind"] == "SCALAR"

    def test_datetime_scalar_is_named_datetime(self):
        """The scalar behind `createdAt` must be `DateTime`, not `datetime`.

        `strawberry.scalar(datetime, ...)` takes its SDL name from the Python class when
        `name=` is omitted, so the schema published a lowercase `datetime` scalar. Client
        codegen names its types after the SDL, and a lowercase scalar is both off-contract
        and unconventional for GraphQL.
        """
        assert _type("DateTime") is not None, "no DateTime scalar in the schema"
        assert _type("datetime") is None, "the scalar is still published as `datetime`"

        created_at = next(f for f in _type("Node")["fields"] if f["name"] == "createdAt")
        assert created_at["type"]["ofType"]["name"] == "DateTime"


class TestRootTypesContract:
    def test_query_root_carries_the_entity_lookups(self):
        query_type = _root_fields("queryType")
        assert query_type["name"] == "Query"
        field_names = [f["name"] for f in query_type["fields"]]
        for required in ("protein", "gene", "pathway"):
            assert required in field_names

    def test_graph_stats_ships_as_stats(self):
        """Contract says `graphStats`; the resolver is `stats`.

        Asserted as shipped rather than as specified — renaming a published field is a
        breaking API change and belongs in a release note, not in a test's expectations.
        Recorded in docs/KNOWN_ISSUES.md.
        """
        field_names = [f["name"] for f in _root_fields("queryType")["fields"]]
        assert "stats" in field_names
        assert "graphStats" not in field_names

    def test_mutation_root_carries_the_protein_mutations(self):
        mutation_type = _root_fields("mutationType")
        assert mutation_type is not None, (
            "no Mutation root — api.gql.schema passes mutation=None unless "
            "BIOMEDICAL_AVAILABLE, so the biomedical domain module failed to import"
        )
        assert mutation_type["name"] == "Mutation"
        field_names = [f["name"] for f in mutation_type["fields"]]
        for required in ("createProtein", "updateProtein", "deleteProtein"):
            assert required in field_names

    def test_there_is_no_subscription_root(self):
        """The contract specifies one; nothing implements it.

        `proteinCreated`, `proteinUpdated` and `interactionCreated` do not exist, and no
        transport is wired for them. Asserting the absence keeps the gap visible: this test
        fails the day a Subscription type appears, which is when the contract's field names
        need checking.
        """
        assert _root_fields("subscriptionType") is None


class TestStatsAndNodesShapeContract:
    """The field names `tests/e2e/test_stress_api.py` asked for, pinned to what ships.

    Two stress tests queried an API that has never existed in any release or contract:
    `stats { nodeCount edgeCount labelCount }` and `nodes(label: "X")`. GraphQL rejects
    both at validation, so they failed on the spelling and never reached the resolver
    they were written to exercise. `nodeCount`/`edgeCount` are real names, but they
    belong to the admin REST payload (`docs/ADMIN_API.md:22`) and to `^NKG("$meta")`,
    not to `GraphStats`. Pinning the shipped names here means the next such test fails
    against a document instead of against a guess.
    """

    def test_graph_stats_publishes_totals_not_counts(self):
        field_names = {f["name"] for f in _type("GraphStats")["fields"]}
        assert field_names == {"totalNodes", "totalEdges", "nodesByLabel", "edgesByType"}

    def test_generic_node_is_in_the_schema(self):
        """`node`/`nodes` return it for every label no domain resolver claims.

        Nothing in the query graph names `GenericNode` — the fields are typed as the
        `Node` interface — so Strawberry left it out of the schema and GraphQL refused
        every result built from it:

            Abstract type 'Node' was resolved to a type 'GenericNode' that does not
            exist inside the schema

        It is registered explicitly via `types=[GenericNode]`.
        """
        generic = _type("GenericNode")
        assert generic is not None, "GenericNode is not registered in the schema"
        assert generic["kind"] == "OBJECT"
        assert "Node" in [i["name"] for i in generic["interfaces"]]

    def test_nodes_filters_on_labels_plural(self):
        result = schema.execute_sync(
            """
            {
                __type(name: "Query") {
                    fields { name args { name type { kind name ofType { kind name } } } }
                }
            }
            """
        )
        assert result.errors is None, result.errors
        nodes = next(f for f in result.data["__type"]["fields"] if f["name"] == "nodes")
        arg_names = {a["name"] for a in nodes["args"]}
        assert "labels" in arg_names, (
            "CoreQuery.nodes takes a list of labels; a singular `label` argument would "
            "be a second spelling of the same filter"
        )
        assert "label" not in arg_names
        labels = next(a for a in nodes["args"] if a["name"] == "labels")
        assert labels["type"]["kind"] == "LIST", labels["type"]


class TestFieldSignaturesContract:
    def test_protein_query_signature(self):
        """`protein(id: ID!): Protein`"""
        result = schema.execute_sync(
            """
            {
                __type(name: "Query") {
                    fields {
                        name
                        args { name type { kind name ofType { kind name } } }
                        type { kind name }
                    }
                }
            }
            """
        )
        assert result.errors is None, result.errors
        protein = next(
            f for f in result.data["__type"]["fields"] if f["name"] == "protein"
        )

        assert len(protein["args"]) == 1
        id_arg = protein["args"][0]
        assert id_arg["name"] == "id"
        assert id_arg["type"]["kind"] == "NON_NULL"
        assert id_arg["type"]["ofType"]["name"] == "ID"

        assert protein["type"]["kind"] == "OBJECT"
        assert protein["type"]["name"] == "Protein"
