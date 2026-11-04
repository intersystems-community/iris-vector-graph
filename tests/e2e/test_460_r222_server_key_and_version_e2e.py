"""4.6.0 R222, live: `IVGClient(url)` with `IVG_API_KEY` exported reaches a server started with that key, and
`GET /server` reports the IRIS version the container reports.

Walkthrough 2 (W11, W12): with the variable exported, `IVGClient(url).server_info()` got 401, and once past that
`iris_version` read "unknown" while `$ZVERSION` in the same container named the build. The server here is
`iris_vector_graph.cypher_api:app` under uvicorn on a free local port, pointed at IVG_TEST_CONTAINER/IVG_PORT and the
IVG460FX* scratch namespace. The expected version is read with `iris session` (`write $ZVERSION`), not through IVG.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")
REPO = Path(__file__).resolve().parents[2]
KEY = "r222-test-key"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    pytest.importorskip("uvicorn")
    pytest.importorskip("httpx")
    import httpx

    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("IVG_", "IRIS_"))}
    env.update(
        IRIS_HOST="localhost", IRIS_PORT=str(alt.PORT), IRIS_NAMESPACE=NS, IVG_API_KEY=KEY,
        PYTHONPATH=str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "iris_vector_graph.cypher_api:app", "--host", "127.0.0.1",
         "--port", str(port)],
        env=env, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"the server exited: {proc.stdout.read()}")
        try:
            if httpx.get(f"{url}/health", timeout=2).status_code < 500:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("the server did not answer /health within 60 s")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def _zversion() -> str:
    out = alt.session(['write "<<ZV:", $ZVERSION, ":ZV>>", !'])
    return alt.marked(out, "ZV")


def test_the_server_refuses_a_client_without_the_key(server, monkeypatch):
    import httpx

    monkeypatch.delenv("IVG_API_KEY", raising=False)
    assert httpx.get(f"{server}/server", timeout=30).status_code == 401


def test_the_client_sends_the_environment_key_and_server_names_the_iris_version(server, monkeypatch):
    from iris_vector_graph import IVGClient

    monkeypatch.setenv("IVG_API_KEY", KEY)
    with IVGClient(server) as client:
        info = client.server_info()
    assert info["iris_version"] == _zversion()
    assert info["namespace"] == NS
