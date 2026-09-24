"""Spec 232 US3: the link report says what each canonical and json-link reference
resolved to (FR-018, data-model "Report row").

Stage 1 of the knowledge fixture, under the default json links, gives the quickstart
table. The report is read-only: it recomputes each status from the definitions.
"""

from __future__ import annotations

import json

import pytest

from iris_vector_graph.exceptions import FHIRGraphError
from tests.e2e.fhir_conftest import GRAPH, FhirLoader

pytestmark = [pytest.mark.e2e]

DEP = "depends-on"
LIB = "library"
IC = "instantiates-canonical"
CQF = "extension:http://hl7.org/fhir/StructureDefinition/cqf-library"
PD_LIB = "PlanDefinition.library"


def k(ld, rtype, name):
    return f"{rtype}/{ld.id(name)}"


@pytest.fixture(scope="module")
def rp(fhir_conn, fhir_engine):
    """Stage 1 loaded and synced, into a graph with the default json links."""
    out = fhir_engine.fhir_graph_register(denylist=[])
    if not out.get("rebuilt"):
        fhir_engine.fhir_graph_rebuild(GRAPH)
    ld = FhirLoader(fhir_conn)
    ld.knowledge("stage1")
    fhir_engine.fhir_graph_sync(GRAPH)
    return ld


def _rows(report):
    return {
        (r["param"], r["url"], r["version"], r["origin"], r["kind"], r["status"]) for r in report["rows"]
    }


def _source(engine, key):
    return _rows(engine.fhir_link_report(GRAPH, source=key))


def test_pd1_rows(fhir_engine, rp):
    assert _source(fhir_engine, k(rp, "PlanDefinition", "pd1")) == {
        (LIB, rp.url("Library/L"), "1.0.0", PD_LIB, "canonical", k(rp, "Library", "l1")),
        (DEP, rp.url("Library/B"), None, "index", "canonical", k(rp, "Library", "b")),
        ("cqf-library", rp.url("Library/U1"), None, CQF, "canonical", k(rp, "Library", "m")),
    }


def test_pd2_rows_carry_each_reason(fhir_engine, rp):
    L = rp.url("Library/L")
    assert _source(fhir_engine, k(rp, "PlanDefinition", "pd2")) == {
        (DEP, L, "2.0.0", "index", "canonical", k(rp, "Library", "l2")),
        (DEP, L, "3.0.0", "index", "canonical", "version-not-found"),
        (DEP, L, None, "index", "canonical", "ambiguous"),
        (DEP, "http://hl7.org/fhir/Library/x", None, "index", "canonical", "no-definition"),
    }


def test_pd3_ad1_cp1_rows(fhir_engine, rp):
    b = k(rp, "Library", "b")
    B = rp.url("Library/B")
    assert _source(fhir_engine, k(rp, "PlanDefinition", "pd3")) == {
        (LIB, B, None, PD_LIB, "canonical", b),
        (DEP, B, None, "index", "canonical", b),
    }
    assert _source(fhir_engine, k(rp, "ActivityDefinition", "ad1")) == {
        (DEP, rp.url("Library/U2"), None, "index", "canonical", "no-definition"),
    }
    assert _source(fhir_engine, k(rp, "CarePlan", "cp1")) == {
        (IC, rp.url("PlanDefinition/pd1"), None, "index", "canonical", k(rp, "PlanDefinition", "pd1")),
    }


def test_source_filter_returns_only_that_source(fhir_engine, rp):
    pd1 = k(rp, "PlanDefinition", "pd1")
    report = fhir_engine.fhir_link_report(GRAPH, source=pd1)
    assert report["source"] == pd1
    assert {r["source"] for r in report["rows"]} == {pd1}


def test_whole_graph(fhir_engine, rp):
    report = fhir_engine.fhir_link_report(GRAPH)
    assert report["graph"] == GRAPH and report["source"] is None
    sources = {r["source"] for r in report["rows"]}
    for name, rtype in (("pd1", "PlanDefinition"), ("pd2", "PlanDefinition"), ("cp1", "CarePlan")):
        assert k(rp, rtype, name) in sources
    assert all(r["status"] for r in report["rows"])


def test_totals_agree_with_rows(fhir_conn, fhir_engine, rp):
    report = fhir_engine.fhir_link_report(GRAPH)
    by_param: dict = {}
    by_link: dict = {}
    for r in report["rows"]:
        outcome = "resolved" if "/" in r["status"] and "://" not in r["status"] else r["status"]
        by_param.setdefault(r["param"], {}).setdefault(outcome, 0)
        by_param[r["param"]][outcome] += 1
        if r["origin"] != "index":
            by_link[r["origin"]] = by_link.get(r["origin"], 0) + 1
    assert report["totals"]["by_param"] == by_param
    cur = fhir_conn.cursor()
    cur.execute("SELECT json_links FROM Graph_KG.fhir_graphs WHERE graph_id = ?", [GRAPH])
    stored = json.loads(cur.fetchone()[0])
    cur.close()
    # Every configured entry, 0 included.
    assert list(report["totals"]["by_link"]) == stored
    for entry, n in report["totals"]["by_link"].items():
        assert n == by_link.get(entry, 0), entry
    assert report["totals"]["by_link"]["Measure.library"] == 0
    assert report["totals"]["by_link"][PD_LIB] >= 2


def test_report_matches_status_counts(fhir_engine, rp):
    assert fhir_engine.fhir_link_report(GRAPH)["totals"]["by_link"] == fhir_engine.fhir_graph_status(GRAPH)["json_links"]


def test_unregistered_graph_errors(fhir_engine, rp):
    with pytest.raises(FHIRGraphError, match="not registered"):
        fhir_engine.fhir_link_report("fhir:IVGFHIR:NOPE232")


def test_cli_json_equals_engine(fhir_engine, rp, monkeypatch):
    from click.testing import CliRunner

    from iris_vector_graph import cli as cli_module

    pd1 = k(rp, "PlanDefinition", "pd1")
    monkeypatch.setattr(cli_module, "_engine_from_env", lambda **kw: fhir_engine)
    out = CliRunner().invoke(cli_module.cli, ["fhir", "links", GRAPH, "--source", pd1, "--format", "json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output) == fhir_engine.fhir_link_report(GRAPH, source=pd1)
    table = CliRunner().invoke(cli_module.cli, ["fhir", "links", GRAPH, "--source", pd1])
    assert table.exit_code == 0, table.output
    assert table.output.splitlines()[0].split() == ["SOURCE", "PARAM", "URL", "VERSION", "ORIGIN", "STATUS"]
