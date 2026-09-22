"""Spec 227 T009 — routed embedding table names (FR-012, research R4).

A route name is a hash, not a concatenation, and the reason is measured rather
than stylistic. IRIS derives a class name from a table name by stripping
underscores and de-duplicating collisions with a numeric suffix, and the derived
class name has a 220-character ceiling. A readable `kg_emb_<graph>_<model>` name
breaks on both: two different pairs can derive the same class, and a long graph ID
overruns the ceiling.

These tests pin the name function itself. They say nothing about *finding* an
existing route — that is a registry read, never a recomputation.
"""

import hashlib
import subprocess
import sys

import pytest

from iris_vector_graph.constants import ROUTE_TABLE_PREFIX
from iris_vector_graph.routing import route_table_name


def test_name_is_prefix_plus_16_hex():
    name = route_table_name("g", "m")
    assert name.startswith(ROUTE_TABLE_PREFIX)
    suffix = name[len(ROUTE_TABLE_PREFIX) :]
    assert len(suffix) == 16, f"expected 16 hex characters, got {suffix!r}"
    assert all(c in "0123456789abcdef" for c in suffix), suffix


def test_name_matches_the_documented_hash():
    """The contract states the algorithm, so the test states it independently.

    Written out rather than calling the implementation twice: a test that reuses
    the production expression passes for any expression.
    """
    graph, model = "ivg227-A", "ivg227-model-a"
    expected = (
        ROUTE_TABLE_PREFIX
        + hashlib.sha256(f"{graph}\0{model}".encode("utf-8")).hexdigest()[:16]
    )
    assert route_table_name(graph, model) == expected


def test_none_model_key_is_the_empty_model():
    """Spec 226's undeclared identity is `None`, and it hashes as the empty string."""
    assert route_table_name("g", None) == route_table_name("g", "")


def test_separator_prevents_boundary_collision():
    """("a", "bc") and ("ab", "c") must not land on the same table.

    Without the NUL separator both concatenate to "abc" and two graphs would
    share one physical table at two declared widths.
    """
    assert route_table_name("a", "bc") != route_table_name("ab", "c")


def test_default_graph_is_a_route_like_any_other():
    """The default graph is `""`, which is a value, not an absence."""
    assert route_table_name("", "m") != route_table_name("g", "m")
    assert route_table_name("", None) == route_table_name("", "")


@pytest.mark.parametrize(
    "graph",
    [
        "",
        "simple",
        "with space",
        "with-dash_and_underscore",
        "with.dots.and:colons",
        "with/slash\\backslash",
        'quote"and\'apostrophe',
        "unicode-ü-漢字-🜚",
        "x" * 4096,
        "\n\t\r",
        "graph_id",  # a name that looks like a column
        "DROP TABLE Graph_KG.nodes",  # a name that looks like SQL
    ],
)
def test_name_is_a_legal_identifier_for_any_graph_id(graph):
    name = route_table_name(graph, "model/with:punctuation")
    assert name.isidentifier(), f"{name!r} is not a legal identifier"
    # The derived class name is `Graph.KG.` + the name with underscores stripped.
    derived = "Graph.KG." + name.replace("_", "")
    assert len(derived) <= 220, f"derived class name is {len(derived)} chars"


def test_name_is_stable_across_processes():
    """`hash()` is salted per process; `sha256` is not.

    A route name computed in one worker has to name the same table in the next,
    so this runs the function in a fresh interpreter and compares.
    """
    here = route_table_name("ivg227-A", "ivg227-model-a")
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from iris_vector_graph.routing import route_table_name;"
            "print(route_table_name('ivg227-A', 'ivg227-model-a'))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == here


def test_name_is_case_sensitive_in_the_graph_id():
    """`graph_id` is `%EXACT`, so two spellings are two graphs."""
    assert route_table_name("Graph", "m") != route_table_name("graph", "m")
