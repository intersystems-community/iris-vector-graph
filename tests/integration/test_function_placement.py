"""Spec 262 T016: every function the translator can emit is in the IVG Schema, and answers there (FR-001, FR-007).

The names come from the translator's own `_fn(...)` call sites, so a function it starts calling is checked without an
edit here. Runs in IVG262ALT (`IVG_NAMESPACE`), initialized by this module with the default schema.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.integration, ns262.requires_container]

NS = os.environ.get("IVG_NAMESPACE", "IVG262ALT")
TRANSLATOR = Path(__file__).resolve().parents[2] / "iris_vector_graph" / "cypher" / "translator.py"
#: `_fn(...)` sites whose name is built, not literal.
BUILT = {"Percentile_PDISC", "Percentile_PCONT", "CY_VLP_ENDS", "CY_VLP_PATHS"}


def emitted() -> set:
    text = TRANSLATOR.read_text(encoding="utf-8")
    return {n for n in re.findall(r"""_fn\(\s*['"](\w+)['"]""", text) if not n.endswith("_")} | BUILT


@pytest.fixture(scope="module")
def setup():
    if not NS.startswith("IVG262"):
        pytest.skip("runs in an IVG262* scratch namespace (IVG_NAMESPACE)")
    conn = ns262.connect(NS)
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS)
    engine.initialize_schema()
    yield conn, engine._schema_prefix
    conn.close()


def test_the_translator_emits_its_helpers_through_fn():
    assert len(emitted()) >= 30


def test_every_emitted_function_is_in_the_ivg_schema(setup):
    conn, schema = setup
    assert emitted() - ns262.routines(conn, schema) == set()


CALLS = [
    ("JSON_ARRAYLENGTH('[1,2,3]')", 3),
    ("JSON_ARRAYGET('[10,20,30]', 1)", "20"),
    ("JSON_VALUE('{\"a\":\"x\"}', '$.a')", "x"),
    ("LIST_TAIL('[7,8,9]')", "[8,9]"),
    ("LIST_CONCAT('[1]', '[2]')", "[1,2]"),
    ("STR_SPLIT('a,b', ',')", '["a","b"]'),
    ("REGEX_MATCH('abc', 'a.c')", 1),
    ("CY_REVERSE('abc')", "cba"),
    ("JSON_KEYS('{\"a\":1}')", '["a"]'),
    ("CypherFn_IVGRANGE(1, 3, 1)", "[1,2,3]"),
    ("Percentile_PDISC('[1,2,3]', 0.5)", "2"),
]


@pytest.mark.parametrize("call, expected", CALLS, ids=[c.split("(")[0] for c, _ in CALLS])
def test_a_direct_call_in_the_ivg_schema_answers(setup, call, expected):
    conn, schema = setup
    value = ns262.rows(conn, f"SELECT {schema}.{call}")[0][0]
    assert (int(value) if isinstance(expected, int) else str(value)) == expected


def test_rand_is_a_fraction(setup):
    conn, schema = setup
    value = float(ns262.rows(conn, f"SELECT {schema}.RAND()")[0][0])
    assert 0.0 <= value < 1.0


def test_every_direct_call_is_a_function_the_translator_emits():
    assert {c.split("(")[0] for c, _ in CALLS} - emitted() == set()
