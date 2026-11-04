"""4.6.0 R106: `ledger.enable()` names the engine's IVG Schema to `Graph.KG.Ledger.Enable`.

`Enable(pStrict, pMaxOps, pReconBound, pSchema = "Graph_KG")` takes the schema whose tables the genesis reads; the client
sent three arguments, so the schema came from the compiled default. Live that default is right, because the deploy
rewrites `"Graph_KG"` in the class source to the namespace's IVG Schema (tests/e2e/test_460_ledger_alt_schema_e2e.py
shows the genesis reading IVGALT's tables), but the client relied on it, unlike `commit`, which sends `schema` in its
meta. This pins the argument; the live test pins the answer.
"""

from __future__ import annotations

import json

from iris_vector_graph.ledger.client import GraphLedger


class _Engine:
    def __init__(self, schema):
        self._schema_prefix = schema


def _enable_args(schema):
    calls = []
    ledger = GraphLedger(_Engine(schema))

    def call(method, *args, cls=None):
        calls.append((method, args))
        return json.dumps({"ok": True, "adoption_needed": False, "revision": {"seq": 1, "revision_id": "r1"}})

    ledger._call = call
    ledger.enable()
    return calls


def test_enable_names_a_non_default_schema():
    assert _enable_args("IVGALT") == [("Enable", (0, 50000, 250000, "IVGALT"))]


def test_enable_names_the_default_schema():
    assert _enable_args(None) == [("Enable", (0, 50000, 250000, "Graph_KG"))]
