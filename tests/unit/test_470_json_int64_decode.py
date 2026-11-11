"""4.7.0 (full E2E gate on ea5ced01): a "J" scalar past openCypher's 64-bit integers reads as a float.

test_460_r307_value_matrix_e2e::test_reader[rel-unwind-*]: a relationship property SET to 1e20 (stored by IRIS as the
JSON number 100000000000000000000) read back through `collect(r.p)` + UNWIND as int 10**20 where 4.6.5 read float
1e+20 (CY_JSON_TYPED marked it a double). Since rows 21/22 the UNWIND hands the element on as CY_LIST_KINDS's JSON
text, `\\x01J...`, and json.loads made it a Python int. openCypher's integers are 64-bit: outside [-2**63, 2**63 - 1]
the value is a float (tests/oracle/spec.py, R310).
"""

from __future__ import annotations

import pytest

from iris_vector_graph.prop_values import decode_typed_text


@pytest.mark.parametrize("text, want", [
    (str(2**63 - 1), 2**63 - 1),
    (str(-2**63), -2**63),
    (str(2**63), float(2**63)),
    (str(-2**63 - 1), float(-2**63 - 1)),
    ("100000000000000000000", 1e20),
    ("7", 7),
    ("2.5", 2.5),
    ("1e20", 1e20),
])
def test_a_json_scalar_keeps_its_cypher_type(text, want):
    got = decode_typed_text("\x01J" + text)
    assert got == want and type(got) is type(want), (text, got)


def test_other_json_scalars_are_unchanged():
    assert decode_typed_text('\x01J"a"') == "a"
    assert decode_typed_text("\x01Jtrue") is True
    assert decode_typed_text("\x01Jnull") is None
