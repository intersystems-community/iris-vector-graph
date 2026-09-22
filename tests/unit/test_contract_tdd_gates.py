"""The contract suite's own TDD gates must open once the thing they gate exists.

`tests/contract/test_cypher_api.py` and `test_cypher_api_errors.py` each compute an
`APP_EXISTS` flag and hang `@pytest.mark.skipif(not APP_EXISTS)` off it. The probe read

    any("protein" in f.name.lower() for f in _mutation_type.fields.values())

and a `graphql.GraphQLField` has no `name` — the field name is the dict key. So the
expression raised `AttributeError`, the surrounding `except (ImportError, AttributeError,
Exception)` swallowed it, `APP_EXISTS` stayed `False`, and 12 contract tests reported
"FastAPI app not implemented yet" for an endpoint that ships. A gate that can only ever
be shut is indistinguishable from a passing suite, which is why this is asserted from the
unit suite rather than left to the gated tests to notice.
"""

from __future__ import annotations

import importlib

import pytest

GATED_MODULES = [
    "tests.contract.test_cypher_api",
    "tests.contract.test_cypher_api_errors",
]


@pytest.mark.parametrize("module_name", GATED_MODULES)
def test_the_gate_is_open_now_that_the_protein_mutations_exist(module_name):
    mod = importlib.import_module(module_name)
    assert mod.APP_EXISTS is True, (
        f"{module_name}.APP_EXISTS is False, so its contract tests are skipped; "
        "api.gql.schema does expose createProtein/updateProtein/deleteProtein"
    )
    assert mod.app is not None


def test_the_probe_reads_field_names_from_the_mapping_keys():
    """`fields` is a `dict[str, GraphQLField]`, and only the key carries the name."""
    from graphql import GraphQLField

    from api.gql.schema import schema

    mutation_type = schema.graphql_schema.mutation_type
    assert mutation_type is not None
    field = next(iter(mutation_type.fields.values()))
    assert isinstance(field, GraphQLField)
    assert not hasattr(field, "name"), (
        "graphql-core grew a GraphQLField.name; the probe's original spelling would "
        "work again and this test should be revisited rather than deleted"
    )
    assert "createProtein" in mutation_type.fields
