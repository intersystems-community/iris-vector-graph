"""4.6.0 R303, live: `initialize_schema()` whose packaged class deploy fails compiles no class from anywhere else.

Until R303 the failure fell back to `$SYSTEM.OBJ.LoadDir("/tmp/src/")` with the status ignored. The test container's
`/tmp/src` holds an older IVG (pre-R084: `iris.vector.graph.GraphOperators`, `User.Exec`, `PageRankEmbedded` and the
`Graph.KG` classes of that time), which that fallback compiled into the namespace being set up.

The namespace (IVG460FX52A, or IVG_R303_NAMESPACE, IVG460FX* only) is made anew with nothing in it. Setup runs with
the packaged deploy forced to raise; no class whose source is in the package or in the server's `/tmp/src` may be
compiled after it, and the reply's warnings say why. `/tmp/src` is only listed, never written: `%Library.File` calls
from the deploy are refused. A second, ordinary setup then compiles the classes, so their absence was not the
namespace's doing.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest

from tests.e2e import ns262, scratch_vars

pytestmark = [pytest.mark.e2e, ns262.requires_container, scratch_vars.requires("IVG_R303_NAMESPACE")]

NS = os.environ.get("IVG_R303_NAMESPACE", "")
SERVER_DIR = "/tmp/src"


def _server_dir_classes() -> set:
    """The classes whose sources are in the container's `/tmp/src`, by name (listed, not read or written)."""
    out = subprocess.run(["docker", "exec", ns262.CONTAINER, "find", SERVER_DIR, "-name", "*.cls"],
                         capture_output=True, text=True).stdout
    return {p[len(SERVER_DIR) + 1:-4].replace("/", ".") for p in out.split()}


def _packaged_classes() -> set:
    from iris_vector_graph._engine import class_deploy as cd

    return set(cd.read_class_sources(cd.packaged_class_dir()))


def _compiled(conn, names: set) -> set:
    found = {r[0] for r in ns262.rows(conn, "SELECT Name FROM %Dictionary.CompiledClass")}
    return names & found


@pytest.fixture(scope="module")
def conn():
    if not NS.startswith(("IVG460FX", "IVG470")):
        pytest.fail(f"refusing {NS}: R303's namespace is an IVG460FX* or IVG470* scratch namespace")
    ns262.recreate(NS)
    c = ns262.connect(NS)
    yield c
    c.close()


def test_a_failed_packaged_deploy_compiles_nothing_from_the_server_dir(conn):
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph import schema as schema_mod

    real = schema_mod._call_classmethod
    file_calls = []

    def no_file_writes(target, cls, method, *args):
        if cls == "%Library.File":  # the old fallback deleted stale sources from /tmp/src; it is shared
            file_calls.append((method, args))
            raise RuntimeError("refused by the R303 E2E: no file operations on the container")
        return real(target, cls, method, *args)

    with patch("iris_vector_graph._engine.class_deploy.deploy_packaged_classes",
               side_effect=RuntimeError("forced by the R303 E2E")), \
            patch.object(schema_mod, "_call_classmethod", no_file_writes):
        status = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS).initialize_schema()

    foreign = _server_dir_classes() - _packaged_classes()
    assert _compiled(conn, _packaged_classes() | _server_dir_classes()) == set(), foreign
    assert file_calls == []
    assert not status["objectscript_deployed"]
    assert "ObjectScript deploy error: packaged class deploy failed: forced by the R303 E2E" in status["warnings"]
    # setup ran: the tables are there
    assert ns262.rows(conn, "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' "
                            "AND TABLE_NAME = 'nodes'") == [(1,)]


def test_an_ordinary_setup_then_compiles_the_packaged_classes(conn):
    from iris_vector_graph import IRISGraphEngine

    status = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS).initialize_schema()
    assert status["objectscript_deployed"] and status["objectscript_failed"] == {}
    assert "Graph.KG.PageRank" in _compiled(conn, _packaged_classes())
    # and still nothing only the server's directory has
    assert _compiled(conn, _server_dir_classes() - _packaged_classes()) == set()
