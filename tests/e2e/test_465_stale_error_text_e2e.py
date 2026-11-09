"""4.6.5: a refused write's message names only its own failure.

Measured on 4.6.4 as a user holding ADMIN_GUIDE §3's reading role, on one engine: `delete_node("bob")` fails (SQLCODE
-415, "erase failed on rdf_edges ..."), then `create_edge("alice", "KNOWS", "carol")` fails with -99, and the -99
message carried an extra line from the earlier call:

    create_edge failed (SQLCODE -99), nothing written: <SQL ERROR>; Details: [SQLCODE: <-99>:<Privilege violation>]
    [Error: <<THROW> *%Exception.StatusException ERROR #5001: erase failed on rdf_edges with SQLCODE -415; ...>]
    [Location: <Prepare>]
    [%msg: <User ... is not privileged for the operation>]

The `[Error: ...]` line is IRIS's: the server process of the connection keeps `$ZERROR` from the last error that
Graph.KG.Eraser threw (or any code of the process caught), and the next SQL error's text names it, once. The Python
side did not make it. Every IVG write opens with `Graph_KG.Txn_Begin` in that process, which now clears `$ZERROR`, so
the write's own errors are the only ones its message can carry.

The reader holds SELECT and EXECUTE on the IVG Schema and the database at R, so IRIS refuses every INSERT, UPDATE and
DELETE at Prepare (-99), as in tests/e2e/test_460_r203_refused_writes_e2e.py.

    IVG_R465_ROLE_NS=IVG463WB pytest tests/e2e/test_465_stale_error_text_e2e.py
"""

from __future__ import annotations

import os
import re

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R465_ROLE_NS", "")
TAG = "R465"

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
              scratch_vars.requires("IVG_R465_ROLE_NS")]

#: a line of a driver message that names a failure other than the statement's own
STALE = re.compile(r"\[Error: ")


@pytest.fixture(scope="module")
def setup():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect(namespace=NS)
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    for node in ("alice", "bob", "carol"):
        assert owner.create_node(node, labels=["P"]) is True
    assert owner.create_edge("alice", "KNOWS", "bob") is True
    user = rr.make_reader(TAG, rr.reading_resources("R", namespace=NS), namespace=NS)
    rconn = rr.connect(user, rr.PASSWORD, namespace=NS)
    try:
        yield IRISGraphEngine(rconn, embedding_dimension=rr.DIM), user
    finally:
        rconn.close()
        rr.drop_reader(TAG, namespace=NS)
        owner.erase_graph(None)
        sysconn.close()


def _refused(call):
    from iris_vector_graph import GraphWriteError

    with pytest.raises(GraphWriteError) as raised:
        call()
    return raised.value


#: the writes whose refusal is an SQL -99 at Prepare: each one's message must be its own
SQL_REFUSED = {
    "create_edge": lambda e: e.create_edge("alice", "KNOWS", "carol"),
    "create_node": lambda e: e.create_node("zed", labels=["P"]),
    "set_edge_weight": lambda e: e.set_edge_weight("alice", "KNOWS", "bob", 2.0),
    "delete_edge": lambda e: e.delete_edge("alice", "KNOWS", "bob"),
}


@pytest.mark.parametrize("name", sorted(SQL_REFUSED))
def test_a_refused_write_after_a_failed_delete_names_only_its_own_failure(setup, name):
    reader, user = setup
    for _round in range(3):
        first = _refused(lambda: reader.delete_node("bob"))
        assert "erase failed on rdf_edges" in str(first), str(first)
        second = _refused(lambda: SQL_REFUSED[name](reader))
        assert second.sqlcode == -99, str(second)
        text = str(second)
        assert "erase failed" not in text and "-415" not in text and "THROW" not in text, text
        assert not STALE.search(text), text
        # what is left is the failure's own: its SQLCODE, the place, and the privilege the user lacks
        assert "Privilege violation" in text and f"{user} is not privileged" in text, text
        assert text.count("SQLCODE") == 2 and text.count("[Location: <Prepare>]") == 1, text


def test_the_first_message_after_a_failure_is_as_clean_as_the_second(setup):
    """It was once: the stale line was in the first message after the other failure and not the next."""
    reader, _user = setup
    _refused(lambda: reader.delete_node("bob"))
    first = str(_refused(lambda: reader.create_edge("alice", "KNOWS", "carol")))
    second = str(_refused(lambda: reader.create_edge("alice", "KNOWS", "carol")))
    assert first == second, f"{first!r} != {second!r}"


def test_a_failed_delete_after_a_refused_write_names_only_its_own_failure(setup):
    """The other order: the -99 first, then the -415, whose message must not carry the privilege error."""
    reader, user = setup
    _refused(lambda: reader.create_edge("alice", "KNOWS", "carol"))
    text = str(_refused(lambda: reader.delete_node("bob")))
    assert "erase failed on rdf_edges" in text
    assert "Privilege violation" not in text and "-99" not in text and not STALE.search(text), text
