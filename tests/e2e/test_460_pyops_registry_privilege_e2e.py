"""4.6.0 R146: a user who may not read `embedding_registry` gets an error from PyOps, not the legacy table's width.

`getVectorTable` and `getExpectedDimension` caught any failure reading the registry and fell back to the legacy table,
so a named graph's width check could be wrong without a word. They now fall back only when the registry does not
exist (tests/unit/test_460_pyops_registry_read.py), and raise otherwise. Here the registry exists and the reader
(tests/e2e/readers460.py) holds every `/kg` grant but SELECT on it: each call raises PermissionError naming
`<IVG Schema>.embedding_registry`. A reader with every grant gets the answers, worked by hand: the default pair's table
is the legacy `kg_NodeEmbeddings` and its width the namespace's, 8 (ns460alt.DIM).
"""

from __future__ import annotations

import re

import pytest

from tests.e2e import ns460alt as alt
from tests.e2e import readers460 as readers

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

CALLS = {
    "getVectorTable": '##class(Graph.KG.PyOps).getVectorTable("","")',
    "getExpectedDimension": '##class(Graph.KG.PyOps).getExpectedDimension("","")',
    "VectorSearch": '##class(Graph.KG.PyOps).VectorSearch(v,2,"","")',
}


@pytest.fixture(scope="module")
def users():
    alt.make_namespace()
    conn = alt.connect()
    alt.engine(conn).initialize_schema()
    conn.close()
    made = {"full": readers.make("R146_FULL"), "denied": readers.make("R146_NO_REGISTRY", omit=("embedding_registry",))}
    yield made
    readers.drop("R146_FULL")
    readers.drop("R146_NO_REGISTRY")


def test_a_reader_with_every_grant_gets_the_legacy_table_and_its_width(users):
    assert readers.call(users["full"], CALLS["getVectorTable"]) == f"{alt.SCHEMA}.kg_NodeEmbeddings"
    assert readers.call(users["full"], CALLS["getExpectedDimension"]) == str(alt.DIM)


def test_the_absent_registry_note_is_written_once_per_process_at_information_severity(users):
    import subprocess
    import uuid

    what = f"r146probe{uuid.uuid4().hex[:8]}"
    alt.session([f'do ##class(Graph.KG.PyOps).NoteNoRegistry("{what}") do ##class(Graph.KG.PyOps).NoteNoRegistry("{what}")'])
    log = subprocess.run(["docker", "exec", alt.CONTAINER, "cat", "/usr/irissys/mgr/messages.log"],
                         capture_output=True, text=True, timeout=60, errors="replace").stdout
    lines = [line for line in log.splitlines() if f"PyOps.{what}:" in line]
    assert len(lines) == 1, lines
    # messages.log: "<time> (<pid>) <severity> [Utility.Event] <text>"; 0 is information.
    assert re.search(r"\(\d+\) 0 \[Utility\.Event\] IVG ", lines[0]), lines
    assert f"no {alt.SCHEMA}.embedding_registry in namespace {alt.NS}" in lines[0], lines


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_reader_who_may_not_read_the_registry_gets_a_privilege_error_naming_it(users, name):
    out = readers.call(users["denied"], CALLS[name])
    assert out.startswith("EXC"), out
    assert "PermissionError" in out, out
    assert f"SELECT on {alt.SCHEMA}.embedding_registry" in out, out
