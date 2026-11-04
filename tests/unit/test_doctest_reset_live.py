"""R066: a document's IVG settings do not outlive it (tests/docs/harness.py, live).

USER_GUIDE turns ledger strict mode on; after a doc run the scratch namespace stayed strict, and 84 unit tests run
there afterwards failed. The reset must put every ^IVG setting a document can change back to its install default,
whatever the baseline was taken with, and the namespace must be left that way after a document, not only before the
next one.

Install defaults, read from the code: the ledger is "never-enabled" (Graph.KG.Ledger.State with no ^IVG.Ledger); no
graph is a multigraph (Graph.KG.GraphMode: absent means off); no predicate is temporal-only (Graph.KG.TemporalPolicy);
the Cypher plan mode is the built-in "job" (Graph.KG.Cypher.Gen.Effective with no ^IVG.CypherGen); no FHIR fault is
injected (Graph.KG.FHIRGraph.RunBatch).

The test deletes and retakes the baseline of the namespace it runs in, so it runs only when IVG_DOCTEST_NAMESPACE
names a scratch namespace of the runner's own, never the shared IVG460DOC by default.
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import requires_running_container
from tests.docs import harness

_NS = os.environ.get("IVG_DOCTEST_NAMESPACE")

pytestmark = [
    pytest.mark.requires_iris,
    requires_running_container(os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")),
    pytest.mark.skipif(not _NS, reason="IVG_DOCTEST_NAMESPACE unset: this test retakes that namespace's baseline"),
]

TURN_ON = [
    "set r=##class(Graph.KG.Ledger).Enable(1,50000,250000)",
    'set sc=##class(Graph.KG.GraphMode).Set("",1)',
    'set sc=##class(Graph.KG.TemporalPolicy).Set("","KNOWS",0)',
    'set ^IVG.CypherGen("mode")="off"',
    'set ^IVG.FHIRGraphFault(1)="beforecommit"',
]

PROBE = [
    'write "LEDGER=",##class(Graph.KG.Ledger).State(),!',
    'write "STRICT=",$g(^IVG.Ledger("meta","strict")),!',
    'write "MULTI=",##class(Graph.KG.GraphMode).IsMulti(""),!',
    'write "TEMPORAL=",$d(^IVG.TemporalStructural),!',
    'write "GENMODE=",##class(Graph.KG.Cypher.Gen).Effective(),!',
    'write "FAULT=",$d(^IVG.FHIRGraphFault),!',
]

DEFAULTS = {"LEDGER": "never-enabled", "STRICT": "", "MULTI": "0", "TEMPORAL": "0", "GENMODE": "job", "FAULT": "0"}
TURNED_ON = {"LEDGER": "enabled", "STRICT": "1", "MULTI": "1", "TEMPORAL": "10", "GENMODE": "off", "FAULT": "10"}


def _settings(cfg) -> dict:
    out = harness.session_output(harness.run_iris_session(PROBE, cfg, 60), cfg.namespace)
    found = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in DEFAULTS:
            found[key.strip()] = value.strip()
    assert set(found) == set(DEFAULTS), out
    return found


@pytest.fixture
def cfg():
    config = harness.load_config()
    assert config.namespace == _NS and config.namespace.startswith(harness.SCRATCH_PREFIX)
    harness.reset_namespace(config)
    yield config
    harness.reset_namespace(config)


def test_the_probe_sees_the_settings_turned_on(cfg):
    harness.run_iris_session(TURN_ON, cfg, 120)
    assert _settings(cfg) == TURNED_ON


def test_a_baseline_taken_while_settings_are_on_still_resets_to_install_defaults(cfg):
    harness.run_iris_session(TURN_ON, cfg, 120)
    _, baseline = harness._baseline_files(cfg.namespace)
    harness.run_iris_session([f"do ##class(%File).Delete({baseline})"], cfg, 60)
    harness.reset_namespace(cfg)  # no baseline: it snapshots the namespace as it is, settings on, then reloads it
    assert _settings(cfg) == DEFAULTS


def test_a_document_leaves_no_ivg_settings_behind(cfg, tmp_path):
    text = "Turn strict mode on, as USER_GUIDE does.\n\n```objectscript\n" + "\n".join(TURN_ON) + "\n```\n"
    results, _ = harness.run_in_scratch(harness.parse_document(text), workdir=tmp_path, config=cfg, label="r066")
    assert [r.status for r in results] == ["ok"], results
    assert _settings(cfg) == DEFAULTS


# ── R072: ^NKG goes through the engine's drop, so its version only rises ─────
#
# A raw kill of ^NKG followed by the baseline's reload put ^NKG("$meta","version") back to the baseline's value.
# kg_betweenness_global_v caches its answer per IRIS process keyed by that version, and ^ArnoKG("KG","nkg_adj_ver")
# compares against it, so a process that answered before the reset (a unit test in the same namespace) could answer
# the next graph with the previous one's (memory: Arno callout defects; tests/unit/test_411_nkg_version_monotonic.py).
# After the reset ^NKG holds nothing but its version, the version is above every version the namespace had before,
# no Arno adjacency cache survives, and ^KG("__version"), which ^ArnoKG's graph JSON is keyed by, has risen too.

NKG_PROBE = [
    'set n="^NKG",c=0,only="" for  set n=$query(@n) quit:n=""  set c=c+1,only=n',
    'write "NKGNODES=",c,!,"NKGONLY=",only,!',
    'write "NKGV=",+$g(^NKG("$meta","version")),!,"KGV=",+$g(^KG("__version")),!',
    'write "ADJVER=",$d(^ArnoKG("KG","nkg_adj_ver")),!,"ADJ=",$d(^ArnoKG("KG","nkg_adj")),!',
]
NKG_KEYS = {"NKGNODES", "NKGONLY", "NKGV", "KGV", "ADJVER", "ADJ"}


def _nkg_state(cfg) -> dict:
    out = harness.session_output(harness.run_iris_session(NKG_PROBE, cfg, 60), cfg.namespace)
    found = {}
    for line in out.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key in NKG_KEYS:
            found[key] = value
    assert set(found) == NKG_KEYS, out
    return found


def test_the_reset_drops_nkg_through_the_engine_and_its_version_keeps_rising(cfg):
    # what a document that built and read an adjacency leaves: interned nodes, a version well past the baseline's, an
    # Arno adjacency cache stamped with that version, a raised ^KG("__version")
    harness.run_iris_session([
        'set v=$i(^NKG("$meta","version"),1000),k=$i(^KG("__version"),1000)',
        'set ^NKG("$NI","r072_a")=900001,^NKG("$ND",900001)="r072_a",^NKG(-1,900001,-1,900001)=1',
        'set ^ArnoKG("KG","nkg_adj_ver")=v,^ArnoKG("KG","nkg_adj")=1',
    ], cfg, 60)
    before = _nkg_state(cfg)
    assert before["ADJVER"] == "1" and int(before["NKGNODES"]) >= 4, before

    harness.reset_namespace(cfg)

    after = _nkg_state(cfg)
    assert after["NKGNODES"] == "1" and after["NKGONLY"] == '^NKG("$meta","version")', after
    assert int(after["NKGV"]) > int(before["NKGV"]), (before, after)
    assert int(after["KGV"]) > int(before["KGV"]), (before, after)
    assert (after["ADJVER"], after["ADJ"]) == ("0", "0"), after


# ── R075: the reset never lowers a row-ID counter ────────────────────────────
#
# tests/unit/test_edge_embeddings.py::test_embed_edges_all_default failed in a full tests/unit run with
# <ILLEGAL VALUE>%SQLInsert+27^Graph.KG.nodes.1 and passed alone. Bisected: test_cypher_var_length.py (inserts nodes
# through the session's connection), then this file (resets the namespace), then test_edge_embeddings (whose setup
# runs initialize_schema, which ends in TUNE TABLE, then inserts). The reset killed the nodes table's ID counter and
# reloaded the baseline's, lower than the IDs the session's process had already taken with $SEQUENCE; after a
# TUNE TABLE in that process its next $SEQUENCE on the counter is <ILLEGAL VALUE>, once. A row ID counter must only
# rise, as in any IRIS namespace.


def _id_counter(cfg) -> int:
    probe = [
        'set s=##class(%Dictionary.CompiledStorage).%OpenId("Graph.KG.nodes||Default")',
        'write "IDLOC=",$s($isobject(s):s.IdLocation,1:""),!',
    ]
    out = harness.session_output(harness.run_iris_session(probe, cfg, 60), cfg.namespace)
    loc = next(line.partition("=")[2].strip() for line in out.splitlines() if line.startswith("IDLOC="))
    assert loc.startswith("^"), out
    out = harness.session_output(harness.run_iris_session([f'write "CTR=",+$g({loc}),!'], cfg, 60), cfg.namespace)
    return int(next(line.partition("=")[2] for line in out.splitlines() if line.startswith("CTR=")))


def test_the_reset_never_lowers_a_row_id_counter(cfg):
    import iris

    conn = iris.connect(cfg.host, cfg.port, cfg.namespace, cfg.username, cfg.password)
    try:
        cur = conn.cursor()

        def insert(node_id):
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [node_id])
            cur.execute("SELECT %ID FROM Graph_KG.nodes WHERE node_id = ?", [node_id])
            row_id = int(cur.fetchone()[0])
            conn.commit()
            return row_id

        first = insert("r075_first")
        before = _id_counter(cfg)
        assert before >= first

        harness.reset_namespace(cfg)

        assert _id_counter(cfg) >= before
        cur.execute("TUNE TABLE Graph_KG.nodes")  # what initialize_schema ends with
        second = insert("r075_second")  # red: <ILLEGAL VALUE>%SQLInsert+27^Graph.KG.nodes.1
        assert second > first
    finally:
        conn.close()
