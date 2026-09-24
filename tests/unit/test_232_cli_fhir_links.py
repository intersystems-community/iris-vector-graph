"""Spec 232: `ivg fhir register --link/--no-links` and `ivg fhir links` (contracts/python.md)."""

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
CQF = "extension:http://hl7.org/fhir/StructureDefinition/cqf-library"


class FakeEngine:
    def __init__(self, result=None, error=None):
        self.result = result if result is not None else {"status": "ok"}
        self.error = error
        self.calls = []

    def __getattr__(self, name):
        if not name.startswith("fhir_"):
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


# --- register ---------------------------------------------------------------------


def test_register_link_repeatable(invoke):
    out, engine = invoke(["register", "--link", "PlanDefinition.library", "--link", CQF])
    assert out.exit_code == 0, out.output
    assert engine.calls[0][2]["json_links"] == ["PlanDefinition.library", CQF]


def test_register_no_links_passes_empty(invoke):
    out, engine = invoke(["register", "--no-links"])
    assert out.exit_code == 0, out.output
    assert engine.calls[0][2]["json_links"] == []


def test_register_neither_passes_none(invoke):
    out, engine = invoke(["register"])
    assert out.exit_code == 0, out.output
    assert engine.calls[0][2]["json_links"] is None


def test_register_both_is_a_usage_error(invoke):
    out, engine = invoke(["register", "--no-links", "--link", "PlanDefinition.library"])
    assert out.exit_code == 2
    assert "--no-links" in out.output
    assert engine.calls == []


# --- links ------------------------------------------------------------------------

REPORT = {
    "graph": G,
    "source": None,
    "rows": [
        {
            "source": "PlanDefinition/pd1",
            "param": "library",
            "url": "http://ex.org/Library/L",
            "version": "1.0.0",
            "origin": "PlanDefinition.library",
            "kind": "canonical",
            "status": "Library/l1",
        },
        {
            "source": "PlanDefinition/pd2",
            "param": "depends-on",
            "url": "http://hl7.org/fhir/Library/x",
            "version": None,
            "origin": "index",
            "kind": "canonical",
            "status": "no-definition",
        },
    ],
    "totals": {
        "by_param": {"library": {"resolved": 1}, "depends-on": {"no-definition": 1}},
        "by_link": {"PlanDefinition.library": 1, CQF: 0},
    },
}


def test_links_table(invoke):
    out, engine = invoke(["links", G], result=REPORT)
    assert out.exit_code == 0, out.output
    assert engine.calls == [("fhir_link_report", (G,), {"source": None})]
    lines = out.output.splitlines()
    assert lines[0].split() == ["SOURCE", "PARAM", "URL", "VERSION", "ORIGIN", "STATUS"]
    row1 = next(line for line in lines if "PlanDefinition/pd1" in line).split()
    assert row1 == ["PlanDefinition/pd1", "library", "http://ex.org/Library/L", "1.0.0", "PlanDefinition.library", "Library/l1"]
    row2 = next(line for line in lines if "PlanDefinition/pd2" in line).split()
    assert row2[3] == "-"
    text = out.output
    assert "param" in text and "link" in text
    # Totals blocks: every param outcome and every link entry, 0 included.
    assert any(line.split() == ["library", "resolved", "1"] for line in lines)
    assert any(line.split() == ["depends-on", "no-definition", "1"] for line in lines)
    assert any(line.split() == [CQF, "0"] for line in lines)


def test_links_json(invoke):
    out, engine = invoke(["links", G, "--format", "json"], result=REPORT)
    assert out.exit_code == 0, out.output
    assert json.loads(out.output) == REPORT


def test_links_source(invoke):
    out, engine = invoke(["links", G, "--source", "PlanDefinition/pd1", "--format", "json"], result=REPORT)
    assert out.exit_code == 0, out.output
    assert engine.calls == [("fhir_link_report", (G,), {"source": "PlanDefinition/pd1"})]


def test_links_error_exits_1(invoke):
    out, _ = invoke(["links", G], error=FHIRGraphError("LinkReport", "not registered"))
    assert out.exit_code == 1
