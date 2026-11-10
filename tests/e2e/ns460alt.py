"""Shared by 4.6.0 E2E files that need a scratch namespace whose IVG Schema is not `Graph_KG` (R125, R126, R130,
R131): the namespace `IVG_NAMESPACE` (an IVG460* or IVG262* scratch namespace with a database of its own, made by
`scripts/dev/make_scratch_namespace.py` if it does not exist), set up by this checkout's `initialize_schema()` with
`schema_prefix="RAG"`.

These files never use `tests/conftest.py`'s session fixtures: those deploy the classes unrewritten (for `Graph_KG`)
into `IVG_NAMESPACE`, and set up the default schema there, which a namespace recording `RAG` refuses.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.gate import SCRATCH_PREFIXES

#: "IVG470*/IVG463*/IVG460*/IVG262*", for the skip reason
_PREFIXES_TEXT = "/".join(p + "*" for p in SCRATCH_PREFIXES)

REPO = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("IVG_PORT", "31972"))
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
NS = os.environ.get("IVG_NAMESPACE", "")
SCHEMA = "RAG"
#: The vector width every file here builds its engine with (a namespace holds one width for the default table).
DIM = 8


def _usable_namespace() -> bool:
    # the gate's own list (4.7.0: IVG470*/IVG463* namespaces skipped here while the gate honoured them)
    return bool(NS) and NS.upper() == NS and NS.startswith(SCRATCH_PREFIXES)


def container_running() -> bool:
    try:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "true"


requires_namespace = [
    pytest.mark.skipif(not _usable_namespace(),
                       reason=f"IVG_NAMESPACE must name an {_PREFIXES_TEXT} scratch namespace (its IVG Schema is RAG)"),
    pytest.mark.skipif(not container_running(), reason=f"{CONTAINER} is not running"),
]


def make_namespace() -> None:
    """`NS` with a database of its own; left as it is when it exists."""
    sys.path.insert(0, str(REPO / "scripts" / "dev"))
    import make_scratch_namespace

    make_scratch_namespace.make(NS)


def connect():
    import iris

    return iris.connect("localhost", PORT, NS, "_SYSTEM", "SYS")


def engine(conn, **kwargs):
    from iris_vector_graph import IRISGraphEngine

    kwargs.setdefault("embedding_dimension", DIM)
    return IRISGraphEngine(conn, namespace=NS, schema_prefix=SCHEMA, **kwargs)


def session(lines: list, namespace: str = "") -> str:
    """Run ObjectScript lines in `namespace` (default `NS`) through `iris session`, and return what it printed."""
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", namespace or NS],
                         input=script, capture_output=True, text=True, timeout=180)
    return out.stdout + out.stderr


def marked(out: str, tag: str) -> str:
    """The text a session printed between `<<TAG:` and `:TAG>>`; raises naming the output when it is absent."""
    start = f"<<{tag}:"
    if start not in out:
        raise AssertionError(f"no {tag} marker in the session output:\n{out[-3000:]}")
    return out.split(start, 1)[1].split(f":{tag}>>", 1)[0]


def rows(conn, sql: str, params=None) -> list:
    cur = conn.cursor()
    cur.execute(sql, params or [])
    out = [tuple(r) for r in cur.fetchall()]
    cur.close()
    return out
