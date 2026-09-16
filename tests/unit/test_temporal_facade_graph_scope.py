"""Every temporal facade method reaches the graph it was asked for.

spec-223 made all five temporal ``^KG`` globals graph-scoped and threaded
``graphId`` through every `Graph.KG.TemporalIndex` classmethod. The Python facade
was only half-wired: ``create_edge_temporal`` and ``get_edges_in_window`` grew a
``graph=`` parameter, and the other nine readers kept passing a literal ``""``
with the comment ``# graphId: "" = default graph``.

The write side was scoped, so a named-graph temporal edge landed under its own
graph key — and then every aggregate reader looked under key 0 and reported that
it did not exist. Nothing raised. `get_temporal_aggregate` returned 0, and
`get_distinct_count` returned 0, for edges that were plainly there.

These are source-level and signature-level guards rather than behaviour tests
because the defect is *absence*: a reader that never asks for a graph cannot be
caught by any single-graph fixture, which is exactly why spec-223's own tests
passed. See tests/integration/test_temporal_facade_graph_scope.py for the
behaviour.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from iris_vector_graph._engine.temporal import TemporalMixin

TEMPORAL_PY = Path(__file__).resolve().parents[2] / "iris_vector_graph" / "_engine" / "temporal.py"

# Every TemporalIndex classmethod whose first parameter is graphId. Verified
# against the class: each one derives its key with GraphKey.ForIndex(graphId).
GRAPH_SCOPED_CLASSMETHODS = [
    "InsertEdge",
    "BulkInsert",
    "GetDistinctCount",
    "QueryWindow",
    "QueryWindowInbound",
    "QueryWindowSources",
    "GetVelocity",
    "FindBursts",
    "PurgeRawBefore",
    "PurgeBucketRange",
    "GetAggregate",
    "GetBucketGroups",
    "GetBucketGroupTargets",
    "GetEdgeAttrs",
]

# Facade methods that read or write graph-scoped temporal state. Each must let a
# caller name the graph; none may pick one on the caller's behalf.
GRAPH_SCOPED_FACADE_METHODS = [
    "create_edge_temporal",
    "bulk_create_edges_temporal",
    "get_edges_in_window",
    "get_edge_velocity",
    "find_burst_nodes",
    "get_edge_attrs",
    "get_temporal_aggregate",
    "get_bucket_groups",
    "get_bucket_group_targets",
    "get_window_sources",
    "get_distinct_count",
    "export_temporal_edges_ndjson",
    "purge_bucket_range",
    "purge_raw_before",
]


@pytest.fixture(scope="module")
def source() -> str:
    return TEMPORAL_PY.read_text()


@pytest.mark.parametrize("method_name", GRAPH_SCOPED_FACADE_METHODS)
def test_the_method_lets_the_caller_name_the_graph(method_name):
    method = getattr(TemporalMixin, method_name, None)
    assert method is not None, f"TemporalMixin has no {method_name}"

    params = inspect.signature(method).parameters
    assert "graph" in params, (
        f"{method_name} does not accept graph=, so it can only ever reach the "
        "default graph — named-graph temporal state is invisible to it"
    )


@pytest.mark.parametrize("method_name", GRAPH_SCOPED_FACADE_METHODS)
def test_the_graph_defaults_to_none_rather_than_a_key(method_name):
    """None means "the caller said nothing", which the validator maps to ''.

    A default of 0 or "" would put the derivation at the call site, which is what
    ADR-0003 gives GraphKey sole ownership of.
    """
    params = inspect.signature(getattr(TemporalMixin, method_name)).parameters
    assert params["graph"].default is None, (
        f"{method_name} defaults graph to {params['graph'].default!r}; it should "
        "default to None and let the derivation happen in one place"
    )


def test_no_call_site_hardcodes_the_default_graph():
    """The literal that made the readers graph-blind, banned by pattern.

    Kept as a source check because it is the cheapest possible statement of the
    rule: a reviewer adding the eleventh reader by copying the tenth trips it.
    """
    src = TEMPORAL_PY.read_text()
    hardcoded = re.findall(r'""\s*,\s*#\s*graphId', src)
    assert hardcoded == [], (
        f"temporal.py still hardcodes graphId at {len(hardcoded)} call site(s); "
        "each one silently reads the default graph whatever the caller asked for"
    )


@pytest.mark.parametrize("classmethod_name", GRAPH_SCOPED_CLASSMETHODS)
def test_every_graph_scoped_classmethod_is_called_with_a_derived_name(
    classmethod_name, source
):
    """If the facade names one of these, the argument must not be a bare ''.

    Checks the two lines after the classmethod name, which is where the graphId
    argument sits in every call in this file.
    """
    for match in re.finditer(rf'"{classmethod_name}"\s*,\s*\n?([^\n]*\n[^\n]*)', source):
        following = match.group(1)
        assert not re.match(r'\s*""\s*,', following), (
            f"the call to {classmethod_name} passes a literal empty graphId: "
            f"{following.strip()!r}"
        )


def test_the_facade_validates_the_graph_name_before_the_round_trip():
    """A bad name should raise ValueError here, not <THROW> from inside IRIS."""
    src = TEMPORAL_PY.read_text()
    assert "validate_graph_name" in src, (
        "temporal.py never canonicalizes the graph name, so a control character "
        "or the reserved name '0' reaches IRIS and comes back as a RuntimeError "
        "wrapping a <THROW> several frames down the DBAPI round trip"
    )
