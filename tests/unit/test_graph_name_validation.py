"""`validate_graph_name` mirrors Graph.KG.GraphKey:Validate on the Python side.

The ObjectScript check is the one that cannot be bypassed — every write to `^KG`
and `^IVG.Ledger` goes through `GraphKey`. But by the time it fires the caller is
several frames into a DBAPI round trip and gets a `RuntimeError` carrying an IRIS
`<THROW>`, which says nothing about which argument was wrong.

This is the same rule stated where the caller can act on it: a `ValueError` at
the seam, naming the graph.

The two rejected shapes and the reason each cannot be represented are documented
in `tests/integration/test_graph_key_behaviour.py` and ADR-0003.
"""

import pytest

from iris_vector_graph._validate import validate_graph_name


@pytest.mark.parametrize("graph", ["", None])
def test_the_default_graph_is_spelled_empty(graph):
    """Empty and absent both mean the default graph, and both are legal."""
    assert validate_graph_name(graph) == ""


def test_an_ordinary_name_passes_through_unchanged():
    assert validate_graph_name("acme") == "acme"


def test_nul_padding_is_stripped_not_rejected():
    """IRIS SQL returns $Char(0) for an empty VARCHAR — a transport artifact."""
    assert validate_graph_name("acme\x00") == "acme"
    assert validate_graph_name("\x00") == ""


def test_the_graph_named_zero_is_rejected():
    with pytest.raises(ValueError, match="reserved"):
        validate_graph_name("0")


def test_nul_padded_zero_is_also_rejected():
    """Stripping happens before the check, so padding cannot smuggle it past."""
    with pytest.raises(ValueError, match="reserved"):
        validate_graph_name("0\x00")


@pytest.mark.parametrize("graph", ["\x01", "ac\x01me", "acme\x1f"])
def test_a_control_character_in_a_name_is_rejected(graph):
    """$Char(1) is the ledger's default-graph sentinel; the rest cannot subscript."""
    with pytest.raises(ValueError, match="control character"):
        validate_graph_name(graph)


def test_the_error_names_the_offending_graph():
    """A caller with several graphs in flight needs to know which one failed."""
    with pytest.raises(ValueError) as excinfo:
        validate_graph_name("0")
    assert "0" in str(excinfo.value)


def test_a_non_string_is_rejected_rather_than_coerced():
    """`0` and `"0"` must not become the same request by accident."""
    with pytest.raises(ValueError):
        validate_graph_name(0)
