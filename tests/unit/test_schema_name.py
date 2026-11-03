"""Spec 262 T004: the IVG Schema's name, resolved and validated before any database call (FR-003, FR-004, research R2)."""

import pytest

from iris_vector_graph.schema_name import resolve_schema_name, schema_package, validate_schema_name


def test_the_argument_wins_over_the_environment():
    assert resolve_schema_name("ARGNAME", {"IVG_SCHEMA_PREFIX": "ENVNAME"}) == "ARGNAME"


def test_the_environment_names_it_when_no_argument_does():
    assert resolve_schema_name(None, {"IVG_SCHEMA_PREFIX": "ENVNAME"}) == "ENVNAME"


def test_an_empty_environment_variable_is_unset():
    assert resolve_schema_name(None, {"IVG_SCHEMA_PREFIX": ""}) == "Graph_KG"


def test_iris_vector_rags_variable_is_never_read():
    assert resolve_schema_name(None, {"IRIS_SCHEMA_PREFIX": "RAG"}) == "Graph_KG"


def test_the_default_is_graph_kg():
    assert resolve_schema_name(None, {}) == "Graph_KG"


@pytest.mark.parametrize(
    "name, rule",
    [
        ("", "empty"),
        ("1abc", "letter"),
        ("a-b", "letters, digits and underscore"),
        ("SQLUser", "SQLUser"),
        ("sqluser", "SQLUser"),
        ("%X", "%"),
    ],
)
def test_an_invalid_name_raises_naming_the_rule(name, rule):
    with pytest.raises(ValueError) as exc:
        validate_schema_name(name)
    text = str(exc.value)
    assert f"schema_prefix {name!r} is not a valid IVG Schema name" in text
    assert rule in text


@pytest.mark.parametrize("name", ["IVGALT", "My_App", "Graph_KG", "RAG"])
def test_a_valid_name_is_accepted(name):
    assert validate_schema_name(name) == name


def test_graph_kg_in_another_case_is_graph_kg():
    assert validate_schema_name("graph_kg") == "Graph_KG"


def test_the_package_is_the_schema_with_underscores_as_dots():
    assert schema_package("MY_APP") == "MY.APP"
    assert schema_package("Graph_KG") == "Graph.KG"
    assert schema_package("IVGALT") == "IVGALT"


@pytest.mark.parametrize("name, package", [("IVG", "IVG"), ("IVGC", "IVGC"), ("IVG_ALT", "IVG.ALT"),
                                           ("Graph_KG_X", "Graph.KG.X"), ("IVGC_P", "IVGC.P")])
def test_a_name_whose_package_is_or_is_under_ivgs_code_packages_is_refused(name, package):
    with pytest.raises(ValueError) as exc:
        validate_schema_name(name)
    assert package in str(exc.value)


def test_resolve_validates():
    with pytest.raises(ValueError):
        resolve_schema_name(None, {"IVG_SCHEMA_PREFIX": "a-b"})
