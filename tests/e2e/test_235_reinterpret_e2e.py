"""Spec 235 US7: the `interp_version` marker. A NULL or old marker makes the next sync
re-derive the interpretation (compartment edges, `category`/`meta_profile`, category
labels) of every live resource once; `fhir_reinterpret` does it on demand.

"pre-235 output" is simulated by deleting the run's interpretation rows and their
`^KG` entries, except in `test_410_to_42`, which deploys the pre-235 classes."""

from __future__ import annotations

import json
import os
import subprocess
import uuid

import pytest

from tests.e2e import interp_fixture as ifx
from tests.e2e.fhir_conftest import (
    GRAPH,
    FhirLoader,
    _sync,
    deploy,
    load_run,
    prefix_resources,
    teardown_run,
)
from tests.e2e.test_235_compartment_e2e import PRED, _gk, _rows, _state

pytestmark = [pytest.mark.e2e]
os.environ.setdefault("SKIP_IRIS_TESTS", "false")

INTERP_PROPS = ("category", "meta_profile")
BASE_410 = "2d92da2dbea89f26645c965ed6556d6931d0cb19"  # baseline_410.json "commit"


def _exec(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        conn.commit()
    finally:
        cur.close()


def _marker(conn, graph=GRAPH):
    rows = _rows(conn, "SELECT interp_version FROM Graph_KG.fhir_graphs WHERE graph_id = ?", graph)
    return rows[0][0] if rows else "absent"


def _set_marker(conn, value, graph=GRAPH):
    _exec(conn, "UPDATE Graph_KG.fhir_graphs SET interp_version = ? WHERE graph_id = ?", value, graph)


def _strip_interpretation(conn, keys) -> int:
    """Remove what pre-235 code never wrote for `keys`, through SQL and `^KG` together.
    Returns the number of compartment edges removed."""
    import iris

    irisobj = iris.createIRIS(conn)
    gk = _gk(conn)
    edges = [
        (e, s, o)
        for e, s, o in _rows(conn, "SELECT edge_id, s, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = ?", GRAPH, PRED)
        if s in keys
    ]
    for e, s, o in edges:
        _exec(conn, "DELETE FROM Graph_KG.rdf_edges WHERE edge_id = ?", e)
        irisobj.classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", s, PRED, o, gk)
    for s, k in _rows(conn, 'SELECT s, "key" FROM Graph_KG.rdf_props WHERE graph_id = ?', GRAPH):
        if s in keys and k in INTERP_PROPS:
            _exec(conn, 'DELETE FROM Graph_KG.rdf_props WHERE graph_id = ? AND s = ? AND "key" = ?', GRAPH, s, k)
            irisobj.kill("^KG", "prop", gk, s, k)
    for s, lab in _rows(conn, "SELECT s, label FROM Graph_KG.rdf_labels WHERE graph_id = ?", GRAPH):
        if s in keys and lab != s.split("/", 1)[0]:
            _exec(conn, "DELETE FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ? AND label = ?", GRAPH, s, lab)
            irisobj.kill("^KG", "label", gk, lab, s)
    return len(edges)


def _globals_agree(conn, keys) -> list:
    """Interpretation rows whose `^KG` entry is missing, for `keys`."""
    import iris

    irisobj = iris.createIRIS(conn)
    gk = _gk(conn)
    bad = []
    for s, o in _rows(conn, "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = ?", GRAPH, PRED):
        if s in keys and not (irisobj.isDefined("^KG", "out", gk, s, PRED, o) and irisobj.isDefined("^KG", "in", gk, o, PRED, s)):
            bad.append(("edge", s, o))
    for s, k in _rows(conn, 'SELECT s, "key" FROM Graph_KG.rdf_props WHERE graph_id = ?', GRAPH):
        if s in keys and k in INTERP_PROPS and not irisobj.isDefined("^KG", "prop", gk, s, k):
            bad.append(("prop", s, k))
    for s, lab in _rows(conn, "SELECT s, label FROM Graph_KG.rdf_labels WHERE graph_id = ?", GRAPH):
        if s in keys and not irisobj.isDefined("^KG", "label", gk, lab, s):
            bad.append(("label", s, lab))
    return bad


@pytest.fixture(scope="module")
def run(fhir_conn_required, fhir_engine, synthea_loaded):
    prefix, resources = synthea_loaded
    cur = fhir_conn_required.cursor()
    try:
        fhir_engine._ensure_fhir_graph_columns(cur)
        fhir_conn_required.commit()
    finally:
        cur.close()
    _sync(fhir_conn_required)
    keys = {f"{r['resourceType']}/{r['id']}" for r in resources}
    return {"prefix": prefix, "resources": resources, "keys": keys}


@pytest.fixture
def clear_fault(fhir_conn_required):
    import iris

    irisobj = iris.createIRIS(fhir_conn_required)
    yield irisobj
    irisobj.kill("^IVG.FHIRGraphFault", GRAPH)


def _interp(reply):
    assert reply.get("status") == "ok", reply
    return reply["interpretation"]


def test_null_marker_rederives(fhir_conn_required, fhir_engine, run):
    """Scenario 1 (SC-008): a graph as pre-235 code left it gets its interpretation back
    from one sync, in SQL and in `^KG`."""
    before = _state(fhir_conn_required, run["keys"])
    assert any(p == PRED for _, p, _, _ in before[0])
    removed = _strip_interpretation(fhir_conn_required, run["keys"])
    assert removed > 0
    assert _state(fhir_conn_required, run["keys"]) != before
    _set_marker(fhir_conn_required, None)
    interp = _interp(fhir_engine.fhir_graph_sync(GRAPH))
    print(f"\nUS7 rederive: {interp}")
    assert interp["full_rederivation"] is True
    assert interp["interpretation_version"] == 1
    assert interp["compartment_edges_added"] == removed
    assert interp["compartment_edges_removed"] == 0
    assert interp["category_set"] > 0 and interp["meta_profile_set"] > 0
    assert _marker(fhir_conn_required) == 1
    after = _state(fhir_conn_required, run["keys"])
    for name, b, a in zip(("edges", "props", "labels"), before, after):
        assert b == a, (name, sorted(b - a)[:5], sorted(a - b)[:5])
    assert _globals_agree(fhir_conn_required, run["keys"]) == []


def test_current_marker_no_rederive(fhir_engine, run):
    """Scenario 2: a current marker leaves the next sync incremental."""
    interp = _interp(fhir_engine.fhir_graph_sync(GRAPH))
    assert interp["full_rederivation"] is False
    assert interp["compartment_edges_added"] == 0
    assert interp["interpretation_version"] == 1


def test_reinterpret(fhir_conn_required, fhir_engine, run):
    """Scenario 3: `fhir_reinterpret` re-derives every live resource and sets the
    marker; nothing changes on a current graph."""
    _set_marker(fhir_conn_required, None)
    before = _state(fhir_conn_required, run["keys"])
    rep = fhir_engine.fhir_reinterpret(GRAPH)
    assert rep["status"] == "ok" and rep["graph"] == GRAPH
    rsrc = _rows(fhir_conn_required, "SELECT rsrc_schema FROM Graph_KG.fhir_graphs WHERE graph_id = ?", GRAPH)[0][0]
    (live,) = _rows(fhir_conn_required, f"SELECT COUNT(*) FROM {rsrc}.Rsrc WHERE Deleted IS NULL OR Deleted = 0")[0]
    assert rep["resources"] == live
    interp = rep["interpretation"]
    assert interp["full_rederivation"] is True and interp["interpretation_version"] == 1
    assert interp["compartment_edges_added"] == interp["compartment_edges_removed"] == 0
    assert _marker(fhir_conn_required) == 1
    assert _state(fhir_conn_required, run["keys"]) == before


def test_stale_flag(fhir_conn_required, fhir_engine, run):
    """Scenario 4: status and coverage both flag a NULL marker; one sync clears it."""
    _set_marker(fhir_conn_required, None)
    try:
        st = fhir_engine.fhir_graph_status(GRAPH)
        assert st["interpretation_version"] is None and st["interpretation_stale"] is True
        cov = fhir_engine.fhir_coverage_report(GRAPH, id_prefix=f"{run['prefix']}-")
        assert cov["stale"] is True and cov["interpretation_version"] is None
    finally:
        _sync(fhir_conn_required)
    st = fhir_engine.fhir_graph_status(GRAPH)
    assert st["interpretation_version"] == 1 and st["interpretation_stale"] is False


def test_interrupted_leaves_marker(fhir_conn_required, fhir_engine, run, clear_fault):
    """Edge case: a failure in the second re-derivation batch rolls that batch back
    and leaves the marker NULL; the next sync re-derives and sets it. The hook is
    the existing `^IVG.FHIRGraphFault(graph)`, with value "rederive:<batch>"."""
    from iris_vector_graph.exceptions import FHIRGraphError

    _set_marker(fhir_conn_required, None)
    clear_fault.set("rederive:2", "^IVG.FHIRGraphFault", GRAPH)
    with pytest.raises(FHIRGraphError, match="injected fault"):
        fhir_engine.fhir_graph_sync(GRAPH)
    assert _marker(fhir_conn_required) is None
    clear_fault.kill("^IVG.FHIRGraphFault", GRAPH)
    interp = _interp(fhir_engine.fhir_graph_sync(GRAPH))
    assert interp["full_rederivation"] is True
    assert _marker(fhir_conn_required) == 1


@pytest.fixture
def scratch_graph(fhir_conn_required, run):
    """A second registration over the same repository, copied from GRAPH's row."""
    scratch = f"fhir:IVGFHIR:t235ri{uuid.uuid4().hex[:6]}"
    cols = [
        r[0]
        for r in _rows(
            fhir_conn_required,
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'Graph_KG' "
            "AND TABLE_NAME = 'fhir_graphs' ORDER BY ORDINAL_POSITION",
        )
    ]
    skip = ("graph_id", "wm_rsrc", "wm_ver", "task_id", "last_sync", "last_rebuild", "last_error", "last_counts")
    copy = [c for c in cols if c not in skip]
    (vals,) = _rows(fhir_conn_required, f"SELECT {', '.join(copy)} FROM Graph_KG.fhir_graphs WHERE graph_id = ?", GRAPH)
    rschema = _rows(fhir_conn_required, "SELECT rsrc_schema FROM Graph_KG.fhir_graphs WHERE graph_id = ?", GRAPH)[0][0]
    (lo,) = _rows(fhir_conn_required, f"SELECT MIN(ID) FROM {rschema}.Rsrc WHERE Key LIKE ?", f"%/{run['prefix']}-%")[0]
    vschema = _rows(fhir_conn_required, "SELECT ver_schema FROM Graph_KG.fhir_graphs WHERE graph_id = ?", GRAPH)[0][0]
    (maxv,) = _rows(fhir_conn_required, f"SELECT MAX(ID) FROM {vschema}.RsrcVer")[0]
    _exec(
        fhir_conn_required,
        f"INSERT INTO Graph_KG.fhir_graphs (graph_id, wm_rsrc, wm_ver, {', '.join(copy)}) "
        f"VALUES ({', '.join(['?'] * (3 + len(copy)))})",
        scratch,
        lo - 1,
        maxv or 0,
        *vals,
    )
    try:
        yield scratch
    finally:
        _exec(fhir_conn_required, "DELETE FROM Graph_KG.fhir_graphs WHERE graph_id = ?", scratch)


def test_erase_nulls_marker(fhir_conn_required, fhir_engine, scratch_graph):
    _interp(fhir_engine.fhir_graph_sync(scratch_graph))
    assert _marker(fhir_conn_required, scratch_graph) == 1
    fhir_engine.erase_graph(scratch_graph)
    assert _marker(fhir_conn_required, scratch_graph) is None


# ------------------------------------------------------------------ pre-235 -> 235


def _archive_410() -> str:
    """The pre-235 ObjectScript sources, extracted once from git."""
    out = os.path.expanduser(f"~/.cache/ivg-235/src_{BASE_410[:12]}")
    if not os.path.isdir(os.path.join(out, "iris_src", "src")):
        os.makedirs(out, exist_ok=True)
        tar = subprocess.run(["git", "archive", BASE_410, "iris_src/src"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", out], input=tar, check=True)
    return os.path.join(out, "iris_src", "src")


def _deploy_from(conn, src: str) -> list:
    """`deploy()` with the Graph.KG classes taken from `src`."""
    from tests.e2e import fhir_conftest

    old = fhir_conftest._ROOT
    fhir_conftest._ROOT = os.path.dirname(os.path.dirname(src))
    try:
        return deploy(conn)
    finally:
        fhir_conftest._ROOT = old


def _one_patient(resources: list[dict]) -> list[dict]:
    """The first patient, every resource naming it, and the shared support resources."""
    patient = next(r for r in resources if r["resourceType"] == "Patient")
    ref = f"Patient/{patient['id']}"
    support = {"Organization", "Practitioner", "PractitionerRole", "Location", "Medication"}
    return [r for r in resources if r is patient or r["resourceType"] in support or ref in json.dumps(r)]


def _modulo_prefix(state, prefix):
    return tuple({tuple(str(x).replace(prefix, "<P>") for x in row) for row in part} for part in state)


@pytest.mark.slow
def test_410_to_42(fhir_conn_required, fhir_engine, run):
    """SC-008 (authoritative): a graph synced by the pre-235 classes, then once by this
    branch, equals a fresh sync of the same resources under another prefix."""
    subset = ifx.load_order(_one_patient(ifx.load_synthea()))
    old_loader, new_loader = FhirLoader(fhir_conn_required), FhirLoader(fhir_conn_required)
    old = new = None
    errors = _deploy_from(fhir_conn_required, _archive_410())
    try:
        assert not errors, errors
        old = load_run(fhir_conn_required, old_loader, prefix_resources(subset, old_loader.prefix))
    finally:
        errors = deploy(fhir_conn_required)
        assert not errors, errors
    try:
        _set_marker(fhir_conn_required, None)  # pre-235 had no column
        old_keys = {f"{r['resourceType']}/{r['id']}" for r in old}
        assert not any(p == PRED for _, p, _, _ in _state(fhir_conn_required, old_keys)[0])
        interp = _interp(fhir_engine.fhir_graph_sync(GRAPH))
        assert interp["full_rederivation"] is True
        new = load_run(fhir_conn_required, new_loader, prefix_resources(subset, new_loader.prefix))
        new_keys = {f"{r['resourceType']}/{r['id']}" for r in new}
        a = _modulo_prefix(_state(fhir_conn_required, old_keys), old_loader.prefix)
        b = _modulo_prefix(_state(fhir_conn_required, new_keys), new_loader.prefix)
        assert any(p == PRED for _, p, _, _ in b[0])
        for name, x, y in zip(("edges", "props", "labels"), a, b):
            assert x == y, (name, sorted(x - y)[:5], sorted(y - x)[:5])
    finally:
        for loader, rs in ((new_loader, new), (old_loader, old)):
            if rs:
                teardown_run(fhir_conn_required, loader, rs)
