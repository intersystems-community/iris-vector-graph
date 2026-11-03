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
