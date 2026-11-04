"""4.6.0 R185, live: `scripts/enterprise-container.sh compile-all` and `compile` refuse a namespace recorded under
another IVG Schema, and load nothing into it.

The scratch namespace's record (`^IVG.Deploy("schema")`, `Graph_KG` after make_scratch_namespace.py) is set to `RAG`
for the test and put back after it. With it `RAG`, each command exits 1 naming `RAG`, and no class in the namespace
is recompiled: every Graph.KG class's compile time (`%Dictionary.CompiledClass.TimeChanged`) is what it was. The
happy path (a load into a namespace recorded as `Graph_KG`) is not run here: it compiles through irispython, which on
the enterprise image is the path `up` uses for USER only (tests/unit/test_460_r185_container_script_refusal.py).
"""

from __future__ import annotations

import os
import subprocess

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

SCRIPT = alt.REPO / "scripts" / "enterprise-container.sh"


def _run(*args):
    env = dict(os.environ, IVG_NAMESPACE=NS, IVG_ARNO_CONTAINER=alt.CONTAINER)
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, timeout=120, env=env, cwd=alt.REPO)


def _compiled_times(conn):
    cur = conn.cursor()
    cur.execute("SELECT ID, TimeChanged FROM %Dictionary.CompiledClass WHERE ID %STARTSWITH 'Graph.KG.' ORDER BY ID")
    rows = [tuple(str(v) for v in r) for r in cur.fetchall()]
    cur.close()
    return rows


@pytest.fixture(scope="module")
def recorded_rag():
    import iris

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    native = iris.createIRIS(conn)
    before = native.get("^IVG.Deploy", "schema")
    assert str(before).upper() == "GRAPH_KG", before
    staged = _run("deploy")
    assert staged.returncode == 0, staged.stdout + staged.stderr
    native.set("RAG", "^IVG.Deploy", "schema")
    try:
        yield conn
    finally:
        native.set(before, "^IVG.Deploy", "schema")
        conn.close()


@pytest.mark.parametrize("args", [("compile-all",), ("compile", "Graph.KG.Meta")], ids=["compile-all", "compile"])
def test_a_namespace_recorded_as_rag_is_refused(recorded_rag, args):
    before = _compiled_times(recorded_rag)
    assert before, "no Graph.KG class is compiled in the namespace"
    out = _run(*args)
    text = out.stdout + out.stderr
    assert out.returncode == 1, text
    assert "records RAG as its IVG Schema" in text and "Nothing was loaded" in text, text
    assert _compiled_times(recorded_rag) == before
