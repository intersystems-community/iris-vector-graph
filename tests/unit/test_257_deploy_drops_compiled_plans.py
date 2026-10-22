"""Spec 257: a deploy drops the native plans compiled against the classes it replaced (Graph.KG.Cypher.Gen.DropAll).

A stale entry is never run (its stamp holds the executor's and the generator's build times), but its class stays in
the namespace until its shape is built again; the deploy removes them all. An unchanged source set deploys nothing and
drops nothing.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine.class_deploy import DeployResult
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.schema import GraphSchema


def _drops(result):
    with patch.object(GraphSchema, "stage_class_owned_rdf_edges", return_value=None), patch.object(
        GraphSchema, "restore_rescued_rdf_edges", return_value=None
    ), patch("iris_vector_graph._engine.class_deploy.deploy_packaged_classes", return_value=result), patch(
        "iris_vector_graph.schema._call_classmethod"
    ) as cm, patch.object(GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()):
        GraphSchema.deploy_objectscript_classes(MagicMock(), Path("/tmp/iris_src"), conn=MagicMock())
    return [c for c in cm.call_args_list if c.args[1:3] == ("Graph.KG.Cypher.Gen", "DropAll")]


@pytest.mark.parametrize("deployed,unchanged,drops", [(True, False, 1), (False, True, 0)])
def test_a_deploy_drops_the_compiled_plans(deployed, unchanged, drops):
    assert len(_drops(DeployResult(deployed=deployed, unchanged=unchanged))) == drops


def test_a_failed_drop_does_not_fail_the_deploy():
    result = DeployResult(deployed=True)
    with patch.object(GraphSchema, "stage_class_owned_rdf_edges", return_value=None), patch.object(
        GraphSchema, "restore_rescued_rdf_edges", return_value=None
    ), patch("iris_vector_graph._engine.class_deploy.deploy_packaged_classes", return_value=result), patch(
        "iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("no such class")
    ), patch.object(GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities(objectscript_deployed=True)):
        caps = GraphSchema.deploy_objectscript_classes(MagicMock(), Path("/tmp/iris_src"), conn=MagicMock())
    assert caps.objectscript_deployed
