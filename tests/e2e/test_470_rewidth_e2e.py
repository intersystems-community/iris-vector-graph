"""4.7.0 row 25: `initialize_schema()` at another width no longer re-declares an empty vector column without asking.

On 4.6.5, two processes calling `initialize_schema()` on one namespace with different `embedding_dimension` values
both succeeded: the second re-declared the empty `kg_NodeEmbeddings.emb` (and the other vector columns) at its own
width with no warning, and the first process's writes then failed. Now the second raises
`EmbeddingIdentityConflict` before changing a column; the error names the column's width, the engine's, and the way
through: `initialize_schema(rewidth=True)` re-declares an empty column. `rewidth=True` still refuses a column that
holds vectors, which no re-declaration can keep.

The namespace's vector tables are emptied for each test and put back at 768 (`rr.DIM`) after it.

    IVG_R470_WR_NS=IVG470WR pytest tests/e2e/test_470_rewidth_e2e.py
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
VECTOR_TABLES = ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized", "kg_EdgeEmbeddings")
REPO = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _engine(conn, dim):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=dim)


def _widths(conn) -> dict:
    from iris_vector_graph.schema import GraphSchema

    cur = conn.cursor()
    return {t: GraphSchema.get_embedding_dimension(cur, f"Graph_KG.{t}") for t in VECTOR_TABLES}


def _empty_vectors(conn) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM Graph_KG.embedding_registry")
    for t in VECTOR_TABLES:
        cur.execute(f"DELETE FROM Graph_KG.{t}")
    conn.commit()


def _rewidth_kwargs(eng) -> dict:
    # the teardown runs on the code before the fix too, where an empty column followed the engine without asking
    return {"rewidth": True} if "rewidth" in inspect.signature(eng.initialize_schema).parameters else {}


@pytest.fixture
def conn():
    c = _connect()
    _empty_vectors(c)
    yield c
    _empty_vectors(c)
    back = _engine(c, rr.DIM)
    back.initialize_schema(**_rewidth_kwargs(back))
    assert set(_widths(c).values()) == {rr.DIM}, "the namespace's vector columns were not put back at 768"
    c.close()


def _set_up_in_another_process(dim: int) -> None:
    """Process A: its own interpreter and connection, `initialize_schema()` at ``dim``."""
    code = (
        "import iris, sys\n"
        "from iris_vector_graph import IRISGraphEngine\n"
        f"c = iris.connect('localhost', {rr.PORT}, {NS!r}, '_SYSTEM', 'SYS')\n"
        f"IRISGraphEngine(c, embedding_dimension={dim}).initialize_schema()\n"
        "c.close()\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-2000:]


def test_a_second_process_at_another_width_raises_and_the_column_keeps_its_width(conn):
    from iris_vector_graph import EmbeddingIdentityConflict

    _set_up_in_another_process(768)
    assert _widths(conn)["kg_NodeEmbeddings"] == 768
    b = _engine(conn, 384)
    with pytest.raises(EmbeddingIdentityConflict) as info:
        b.initialize_schema()
    message = str(info.value)
    assert "768" in message and "384" in message, message
    assert "initialize_schema(rewidth=True)" in message, message
    assert _widths(conn) == {t: 768 for t in VECTOR_TABLES}, "the refused setup changed a column"


def test_a_namespace_first_set_up_at_the_default_width_says_how_to_fix_it_once(conn):
    """The first-run case: `ivg` and the server set a namespace up at the default 768
    (constants.DEFAULT_EMBEDDING_DIMENSION); the user's model is 384 wide and nothing is stored yet."""
    from iris_vector_graph import EmbeddingIdentityConflict
    from iris_vector_graph.constants import DEFAULT_EMBEDDING_DIMENSION

    _engine(conn, DEFAULT_EMBEDDING_DIMENSION).initialize_schema()
    user = _engine(conn, 384)
    with pytest.raises(EmbeddingIdentityConflict) as info:
        user.initialize_schema()
    message = str(info.value)
    assert "holds no vectors" in message, message
    assert "initialize_schema(rewidth=True)" in message and "once" in message, message
    assert "embedding_dimension=768" in message, message  # the other way: use the width the column has
    plan = user.initialize_schema(dry_run=True)["embedding_dimension"]
    assert "Graph_KG.kg_NodeEmbeddings" in plan["needs_rewidth"], plan
    assert "Graph_KG.kg_NodeEmbeddings" not in plan["alter"], plan
    user.initialize_schema(rewidth=True)
    assert _widths(conn) == {t: 384 for t in VECTOR_TABLES}
    cur = conn.cursor()
    cur.execute("SELECT dimension FROM Graph_KG.embedding_registry WHERE table_name = 'kg_NodeEmbeddings'")
    assert [int(r[0]) for r in cur.fetchall()] == [384]


def test_rewidth_true_refuses_a_column_that_holds_vectors(conn):
    from iris_vector_graph import EmbeddingIdentityConflict

    nid = "t470wd" + uuid.uuid4().hex[:10]
    a = _engine(conn, 768)
    a.initialize_schema()
    assert a.create_node(nid)
    a.store_embedding(nid, [0.25] * 768)
    b = _engine(conn, 384)
    with pytest.raises(EmbeddingIdentityConflict) as info:
        b.initialize_schema(rewidth=True)
    message = str(info.value)
    assert "Graph_KG.kg_NodeEmbeddings" in message and "holds 1 vector" in message, message
    assert "embedding_dimension=768" in message, message
    assert _widths(conn)["kg_NodeEmbeddings"] == 768
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = ?", [nid])
    assert int(cur.fetchone()[0]) == 1, "the vector was lost"
    a.delete_node(nid)


# ------------------------------------------------------------------------------- the HTTP API and the ivg CLI


@pytest.fixture
def api(conn, monkeypatch):
    """The app's shared engine on this namespace; ``POST /admin/schema/init`` sets its width from the body."""
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.delenv("IVG_READ_ONLY", raising=False)
    monkeypatch.setattr(cypher_api, "_engine_cache", _engine(_connect(), None))
    yield TestClient(cypher_api.app)
    cypher_api._engine_cache.conn.close()


def test_the_http_api_answers_409_and_takes_rewidth(conn, api):
    """4.7.0 row 25: the refusal is the caller's state, not a server fault (it was 500), and the body's ``rewidth``
    is the way through."""
    _engine(conn, 768).initialize_schema()
    resp = api.post("/admin/schema/init", json={"embedding_dimension": 384})
    assert resp.status_code == 409, resp.text
    assert "rewidth" in resp.json()["detail"] and "384" in resp.json()["detail"], resp.text
    assert _widths(conn)["kg_NodeEmbeddings"] == 768
    resp = api.post("/admin/schema/init", json={"embedding_dimension": 384, "rewidth": True})
    assert resp.status_code == 200, resp.text
    assert _widths(conn) == {t: 384 for t in VECTOR_TABLES}


def test_the_cli_says_why_and_takes_rewidth(conn, api, monkeypatch):
    """`ivg schema init` posts to the API; its requests go to the test client here."""
    import httpx
    from click.testing import CliRunner

    from iris_vector_graph.cli import cli

    monkeypatch.setattr(httpx, "post", lambda url, **kw: api.post(url.split("8200", 1)[1], **kw))
    _engine(conn, 768).initialize_schema()
    runner = CliRunner()
    refused = runner.invoke(cli, ["--url", "http://localhost:8200", "schema", "init", "--embedding-dim", "384"])
    assert refused.exit_code == 1, refused.output
    assert "409" in refused.output and "--rewidth" in refused.output, refused.output
    assert _widths(conn)["kg_NodeEmbeddings"] == 768
    done = runner.invoke(
        cli, ["--url", "http://localhost:8200", "schema", "init", "--embedding-dim", "384", "--rewidth"])
    assert done.exit_code == 0, done.output
    assert _widths(conn) == {t: 384 for t in VECTOR_TABLES}
