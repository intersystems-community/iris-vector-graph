"""Spec 231 FR-018: `ivg fhir register|rebuild|sync --once|status|schedule`.

Like `embeddings inventory`, these talk to IRIS directly: the FHIR graph lives in the
FHIR namespace, and the operator running them is registering or repairing it, not
querying the server. Each command maps to one engine method and prints its JSON.
"""

import json

import pytest

from iris_vector_graph.exceptions import FHIRGraphError

try:
    from click.testing import CliRunner

    _HAS_CLICK = True
except ImportError:  # pragma: no cover - click is an extra
    _HAS_CLICK = False

pytestmark = pytest.mark.skipif(not _HAS_CLICK, reason="click not installed")

G = "fhir:IVGFHIR:X0001"


class FakeEngine:
    def __init__(self, result=None, error=None):
        self.result = result if result is not None else {"status": "ok"}
        self.error = error
        self.calls = []

    def __getattr__(self, name):
        if not name.startswith("fhir_graph_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.error is not None:
                raise self.error
            return self.result

        return call


@pytest.fixture
def invoke(monkeypatch):
    def run(args, *, result=None, error=None):
        from iris_vector_graph import cli as cli_module

        engine = FakeEngine(result, error)
        monkeypatch.setattr(cli_module, "_engine_from_env", lambda **kw: engine)
        out = CliRunner().invoke(cli_module.cli, ["fhir", *args])
        return out, engine

    return run


def test_register_passes_endpoint_denylist_and_interval(invoke):
    out, engine = invoke(
        [
            "register",
            "--endpoint",
            "/csp/healthshare/ivgfhir/fhir/r4",
            "--deny",
            "Observation.performer",
            "--deny",
            "Encounter.participant",
            "--interval",
            "120",
        ],
        result={"status": "registered", "graph_id": G},
    )
    assert out.exit_code == 0, out.output
    assert engine.calls == [
        (
            "fhir_graph_register",
            (),
            {
                "endpoint": "/csp/healthshare/ivgfhir/fhir/r4",
                "denylist": ["Observation.performer", "Encounter.participant"],
                "interval_s": 120,
                "json_links": None,
            },
        )
    ]
    assert json.loads(out.output)["graph_id"] == G


def test_register_defaults(invoke):
    out, engine = invoke(["register"])
    assert out.exit_code == 0, out.output
    assert engine.calls[0][2] == {"endpoint": "", "denylist": [], "interval_s": 60, "json_links": None}


def test_rebuild(invoke):
    out, engine = invoke(["rebuild", G], result={"status": "ok", "stale_dropped": 0})
    assert out.exit_code == 0, out.output
    assert engine.calls == [("fhir_graph_rebuild", (G,), {})]
    assert json.loads(out.output)["stale_dropped"] == 0


def test_sync_once(invoke):
    out, engine = invoke(["sync", G, "--once"], result={"status": "ok", "keys": 3})
    assert out.exit_code == 0, out.output
    assert engine.calls == [("fhir_graph_sync", (G,), {})]


def test_sync_without_once_points_at_schedule(invoke):
    """Periodic sync is the Task Manager's job; the CLI does not loop."""
    out, engine = invoke(["sync", G])
    assert out.exit_code != 0
    assert "schedule" in out.output
    assert engine.calls == []


def test_sync_busy_exits_2(invoke):
    out, _ = invoke(["sync", G, "--once"], result={"status": "busy"})
    assert out.exit_code == 2
    assert json.loads(out.output)["status"] == "busy"


def test_status(invoke):
    out, engine = invoke(["status", G], result={"graph_id": G, "pending": 0})
    assert out.exit_code == 0, out.output
    assert engine.calls == [("fhir_graph_status", (G,), {})]


def test_schedule_with_interval(invoke):
    out, engine = invoke(["schedule", G, "--interval", "90"], result={"task_id": 7, "minutes": 2})
    assert out.exit_code == 0, out.output
    assert engine.calls == [("fhir_graph_schedule", (G,), {"interval_s": 90})]


def test_schedule_keeps_registered_interval_by_default(invoke):
    out, engine = invoke(["schedule", G])
    assert out.exit_code == 0, out.output
    assert engine.calls == [("fhir_graph_schedule", (G,), {"interval_s": None})]


def test_schedule_remove(invoke):
    out, engine = invoke(["schedule", G, "--remove"], result={"removed": True})
    assert out.exit_code == 0, out.output
    assert engine.calls == [("fhir_graph_unschedule", (G,), {})]


def test_error_exits_1_with_message(invoke):
    out, _ = invoke(["rebuild", G], error=FHIRGraphError("Rebuild", "no FHIR repository"))
    assert out.exit_code == 1
    assert "no FHIR repository" in out.output


def test_bad_argument_exits_1(invoke):
    out, _ = invoke(["register", "--deny", "nodot"], error=ValueError("not 'Type.param'"))
    assert out.exit_code == 1
    assert "Type.param" in out.output
