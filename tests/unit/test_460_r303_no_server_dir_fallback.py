"""4.6.0 R303: a failed packaged class deploy loads nothing else.

`GraphSchema.deploy_objectscript_classes` fell back to `$SYSTEM.OBJ.LoadDir` on `/tmp/src/`, then
`/irisdev/app/iris_src/src/`, then the client's own `iris_src/src`, whenever the packaged deploy raised, returned None
(no sources packaged) or did not deploy (R084's refusal of a class IVG did not make). It ignored that load's status and
compiled whatever the server's directory held: on the shared test container, pre-R084 sources, i.e. an older IVG; on
any server, files anyone who can write to `/tmp` put there, compiled as the user running setup. The sources it loaded
also name `Graph_KG` whatever the IVG Schema (spec 262).

The packaged sources ship in every install (the wheel force-includes `iris_src/src`; a source tree has it beside the
package), so the fallback is gone: the deploy's failure, and that nothing else was loaded, are in `last_deploy` and the
reply's `warnings` (R299).
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine.class_deploy import DeployResult
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

#: The two edge classes spec 227 removed (either owns `rdf_edges` if compiled), deleted.
STALE_DELETES = {("%SYSTEM.OBJ", "Delete", "Graph.KG.Edge"), ("%SYSTEM.OBJ", "Delete", "Graph.KG.TestEdge")}

REFUSED = DeployResult(
    compiled=["Graph.KG.PageRank"],
    errors=["Graph.KG.PageRank: a class of this name that IVG did not create is in package Graph.KG; nothing was "
            "loaded (choose another IVG Schema, or move that class)"],
    failed={"Graph.KG.PageRank": "a class of this name that IVG did not create is in package Graph.KG; nothing was "
                                 "loaded (choose another IVG Schema, or move that class)"},
)

#: How the packaged deploy can fail to deploy: `patch()` keywords for `deploy_packaged_classes`.
OUTCOMES = {
    "raises": {"side_effect": RuntimeError("<COMMUNICATION LINK ERROR>")},
    "nothing packaged": {"return_value": None},
    "R084 refusal": {"return_value": REFUSED},
}


@pytest.fixture(autouse=True)
def _nothing_to_rescue():
    with patch.object(GraphSchema, "stage_class_owned_rdf_edges", return_value=None), \
            patch.object(GraphSchema, "restore_rescued_rdf_edges", return_value=None):
        yield


def _deploy(outcome):
    """Run the real `deploy_objectscript_classes` with the packaged deploy failing as `outcome` says; return the
    `(class, method, first argument)` of every `_call_classmethod` it made."""
    with patch("iris_vector_graph._engine.class_deploy.deploy_packaged_classes", **OUTCOMES[outcome]), \
            patch("iris_vector_graph._engine.class_deploy.native_for", side_effect=TypeError("no Native API")), \
            patch("iris_vector_graph.schema._call_classmethod", return_value=1) as calls, \
            patch.object(GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()):
        # The client's own iris_src exists and holds classes: the fallback's third directory.
        GraphSchema.deploy_objectscript_classes(MagicMock(), Path(__file__).resolve().parents[2] / "iris_src")
    return [c.args[1:4] for c in calls.call_args_list]


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_no_server_directory_is_loaded_and_no_class_touched(outcome):
    # No LoadDir or Compile, no file deleted from a directory nothing reads, no class deleted
    assert _deploy(outcome) == []


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_the_stale_edge_classes_go_when_the_rescue_staged_their_rows(outcome):
    """A 3.2.0 `Graph.KG.Edge` owns `rdf_edges`; the rescue staged its rows to put them back into a DDL table, which
    the class's delete makes possible. The fallback did that delete on a failed deploy; it does not depend on a load."""
    with patch.object(GraphSchema, "stage_class_owned_rdf_edges", return_value=4):
        assert set(_deploy(outcome)) == STALE_DELETES


@pytest.mark.parametrize("outcome, why", [
    ("raises", "packaged class deploy failed: <COMMUNICATION LINK ERROR>"),
    ("nothing packaged", "no ObjectScript class sources are packaged with this install"),
    ("R084 refusal", "a class of this name that IVG did not create"),
])
def test_last_deploy_says_why_nothing_deployed(outcome, why):
    _deploy(outcome)
    deploy = GraphSchema.last_deploy
    assert deploy is not None and not deploy.deployed and not deploy.unchanged
    assert any(why in e for e in deploy.errors), deploy.errors


# ------------------------------------------------------------------------------------------------- the reply


class _Cursor:
    """SQL that succeeds and finds nothing."""

    description = None
    rowcount = 0

    def execute(self, sql, params=None):
        return None

    def executemany(self, sql, rows):
        return None

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


class _Native:
    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return "IVGFX"
        raise RuntimeError(f"<CLASS DOES NOT EXIST> *{cls}")

    def classMethodVoid(self, cls, method, *args):
        self.classMethodValue(cls, method, *args)

    def get(self, *subscripts):
        return None

    def set(self, value, *subscripts):
        pass


def _setup(outcome, caplog):
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = _Cursor
    with patch("iris.createIRIS", lambda c: _Native()), \
            patch("iris_vector_graph._engine.class_deploy.deploy_packaged_classes", **OUTCOMES[outcome]), \
            patch("iris_vector_graph._engine.class_deploy.native_for", side_effect=TypeError("no Native API")), \
            patch("iris_vector_graph.schema._call_classmethod", return_value=1) as calls, \
            patch.object(GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()), \
            patch.object(GraphSchema, "ensure_adjacency_index",
                         staticmethod(lambda cur: {"idx_edges_adj": "created", "idx_labels_adj": "created"})), \
            patch.object(GraphSchema, "bootstrap_kg_global",
                         staticmethod(lambda cur, conn=None, schema="Graph_KG": False)), \
            caplog.at_level(logging.INFO):
        status = IRISGraphEngine(conn, embedding_dimension=8).initialize_schema()
    return status, [c.args[2] for c in calls.call_args_list]


@pytest.mark.parametrize("outcome, why", [
    ("raises", "ObjectScript deploy error: packaged class deploy failed: <COMMUNICATION LINK ERROR>"),
    ("nothing packaged", "ObjectScript deploy error: no ObjectScript class sources are packaged with this install"),
    ("R084 refusal", "ObjectScript deploy error: Graph.KG.PageRank: a class of this name that IVG did not create"),
])
def test_initialize_schema_replies_why_and_loads_nothing(outcome, why, caplog):
    status, methods = _setup(outcome, caplog)
    assert any(w.startswith(why) for w in status["warnings"]), status["warnings"]
    assert "LoadDir" not in methods and not status["objectscript_deployed"]
    if outcome == "R084 refusal":
        assert list(status["objectscript_failed"]) == ["Graph.KG.PageRank"]
