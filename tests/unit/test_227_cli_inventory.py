"""Spec 227 — `ivg embeddings inventory` (T057, FR-038).

Routing turns one embedding table into one per `(graph, model)`, and the number of them
is the thing an operator has to be able to watch: it grows with every new pair, each one
carries an index, and a namespace at the measured ceiling of 100 routes behaves nothing
like one with two. FR-038 says they can read that number without writing SQL, so this is
the command that prints it.

Read-only by construction: the command asks the engine for the inventory and formats it.
It creates no route, initializes no schema, and measures no recall — a status command
that changed the thing it reports on could not be run to find out whether to change it.
"""

import json
from dataclasses import replace

import pytest

try:
    from click.testing import CliRunner

    _HAS_CLICK = True
except ImportError:  # pragma: no cover - click is an extra
    _HAS_CLICK = False

pytestmark = pytest.mark.skipif(not _HAS_CLICK, reason="click not installed")

from iris_vector_graph._engine.schema import EmbeddingInventoryRow

ROUTED = EmbeddingInventoryRow(
    graph_id="graph-a",
    model_key="model-a",
    table_name="kg_emb_00000000000000aa",
    dimension=384,
    dtype="DOUBLE",
    row_count=12,
    index_name="idx_kg_emb_00000000000000aa_ann",
    index_state="present",
    index_error=None,
    recall_measured=0.97,
    recall_measured_at="2026-09-19 22:10:00",
)

REFUSED = EmbeddingInventoryRow(
    graph_id="graph-b",
    model_key="model-b",
    table_name="kg_emb_00000000000000bb",
    dimension=768,
    dtype="DOUBLE",
    row_count=3,
    index_name=None,
    index_state="refused",
    index_error="[SQLCODE: <-400>] HNSW index requires a licensed vector search feature",
    recall_measured=None,
    recall_measured_at=None,
)

UNROUTED = EmbeddingInventoryRow(
    graph_id="graph-quiet",
    model_key=None,
    table_name=None,
    dimension=None,
    dtype=None,
    row_count=0,
    index_name=None,
    index_state="absent",
    index_error=None,
    recall_measured=None,
    recall_measured_at=None,
)


class FakeEngine:
    """An engine that answers the inventory and records every call made on it."""

    def __init__(self, rows=(), error=None):
        self._rows = list(rows)
        self._error = error
        self.calls = []

    def embedding_inventory(self):
        self.calls.append("embedding_inventory")
        if self._error is not None:
            raise self._error
        return list(self._rows)

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)
            return None

        return record


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def invoke(runner, monkeypatch):
    """Run `embeddings inventory` against a fake engine, returning (result, engine)."""

    def run(rows=(), *, args=(), error=None, connect_error=None):
        from iris_vector_graph import cli as cli_module

        engine = FakeEngine(rows, error=error)

        def fake_engine_from_env(**kwargs):
            if connect_error is not None:
                raise connect_error
            return engine

        monkeypatch.setattr(cli_module, "_engine_from_env", fake_engine_from_env)
        result = runner.invoke(cli_module.cli, ["embeddings", "inventory", *args])
        return result, engine

    return run


# --- the report ---------------------------------------------------------------------


def test_every_route_is_printed_with_its_table_width_and_row_count(invoke):
    result, _engine = invoke([ROUTED, REFUSED])
    assert result.exit_code == 0, result.output
    assert "kg_emb_00000000000000aa" in result.output
    assert "graph-a" in result.output and "model-a" in result.output
    assert "384" in result.output
    assert "12" in result.output


def test_the_routed_table_count_is_stated(invoke):
    """FR-038's number. Not the report's length: a graph with no route is not a table."""
    result, _engine = invoke([ROUTED, REFUSED, UNROUTED])
    assert result.exit_code == 0, result.output
    assert "2 routed table" in result.output, (
        "the routed-table count is missing or counted the unrouted graph: " + result.output
    )


def test_a_graph_with_no_route_is_shown_rather_than_omitted(invoke):
    """US4-3. Omitting it makes "the vectors are elsewhere" and "there are none" one answer."""
    result, _engine = invoke([ROUTED, UNROUTED])
    assert "graph-quiet" in result.output


def test_a_refused_index_prints_the_reason_iris_gave(invoke):
    """US4-2: no index, and why. The wording is the only thing that says what to do next."""
    result, _engine = invoke([REFUSED])
    assert "refused" in result.output
    assert "licensed vector search feature" in result.output


def test_an_empty_namespace_reports_zero_rather_than_failing(invoke):
    """A 3.2.0 database routes nothing and `embedding_inventory` returns `[]` for it."""
    result, _engine = invoke([])
    assert result.exit_code == 0, result.output
    assert "0 routed table" in result.output


# --- machine-readable output --------------------------------------------------------


def test_json_output_carries_every_column(invoke):
    result, _engine = invoke([ROUTED, UNROUTED], args=["--json-output"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["routed_tables"] == 1
    first = payload["inventory"][0]
    for field in (
        "graph_id",
        "model_key",
        "table_name",
        "dimension",
        "dtype",
        "row_count",
        "index_name",
        "index_state",
        "index_error",
        "recall_measured",
        "recall_measured_at",
    ):
        assert field in first, f"{field} is in the report and not in the JSON"
    assert first["recall_measured"] == 0.97


def test_an_unmeasured_recall_is_null_not_a_number(invoke):
    """Recall nobody measured must not read as recall of zero (FR-021)."""
    result, _engine = invoke([REFUSED], args=["--json-output"])
    payload = json.loads(result.output)
    assert payload["inventory"][0]["recall_measured"] is None


# --- what it must not do -----------------------------------------------------------


def test_the_command_only_reads(invoke):
    result, engine = invoke([ROUTED])
    assert result.exit_code == 0, result.output
    assert engine.calls == ["embedding_inventory"], (
        f"a status command did more than report: {engine.calls}"
    )


def test_a_connection_failure_exits_nonzero_with_the_reason(invoke):
    result, _engine = invoke([], connect_error=RuntimeError("no IRIS_HOST set"))
    assert result.exit_code == 1
    assert "no IRIS_HOST set" in result.output


def test_an_unreadable_namespace_exits_nonzero(invoke):
    result, _engine = invoke([], error=RuntimeError("[SQLCODE: <-30>] nodes"))
    assert result.exit_code == 1
    assert "SQLCODE" in result.output


def test_two_routes_at_two_widths_are_both_reported(invoke):
    """The shape spec 227 exists for: one node ID, two graphs, two widths (SC-001)."""
    other = replace(ROUTED, graph_id="graph-b", model_key="model-b", dimension=768,
                    table_name="kg_emb_00000000000000bb")
    result, _engine = invoke([ROUTED, other])
    assert "384" in result.output and "768" in result.output
    assert "2 routed table" in result.output
