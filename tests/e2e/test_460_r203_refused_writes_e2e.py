"""4.6.0 R203: a write IRIS refuses raises GraphWriteError and leaves nothing behind (USER_GUIDE §2).

Walkthrough 1 W6: as a reader holding ADMIN_GUIDE §3's reading role, `create_node` and `delete_edge` answered `False`
on SQLCODE -99, which is also what they answer for a node that is already there and an edge that is not. Every public
write method that answered a refusal with a value was found the same way (the probe in this file's history); each one
is a case here. The answers were worked out by hand: the reader holds SELECT and EXECUTE on the IVG Schema and the
database at `R`, so IRIS refuses every INSERT, UPDATE and DELETE at Prepare (-99) and every global write (<PROTECT>).

    IVG_NAMESPACE=IVG460FXO pytest tests/e2e/test_460_r203_refused_writes_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

TAG = "R203"


@pytest.fixture(scope="module")
def setup():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    cur = sysconn.cursor()
    cur.execute(f"SELECT edge_id FROM {rr.SCHEMA}.rdf_edges WHERE s = 'a' AND p = 'KNOWS' AND o_id = 'b'")
    ab = int(cur.fetchone()[0])
    assert owner.reify_edge(ab, "r0", props={"source": "seed"}) == "r0"
    user = rr.make_reader(TAG, rr.reading_resources("R"))
    rconn = rr.connect(user, rr.PASSWORD)
    reader = IRISGraphEngine(rconn, embedding_dimension=rr.DIM)
    try:
        yield owner, reader, sysconn, ab
    finally:
        rconn.close()
        rr.drop_reader(TAG)
        owner.erase_graph(None)
        sysconn.close()


VEC = [1.0] + [0.0] * (rr.DIM - 1)

#: (call, the SQLCODE the refusal carries: -99 for SQL, None for a global write's <PROTECT>)
REFUSED = {
    "create_node": (lambda e, ab: e.create_node("z", labels=["P"]), -99),
    "delete_edge": (lambda e, ab: e.delete_edge("a", "KNOWS", "b"), -99),
    "set_edge_weight": (lambda e, ab: e.set_edge_weight("a", "KNOWS", "b", 2.0), -99),
    "reify_edge": (lambda e, ab: e.reify_edge(ab, "r9"), -99),
    "delete_reification": (lambda e, ab: e.delete_reification("r0"), -99),
    "create_edge_temporal": (lambda e, ab: e.create_edge_temporal("a", "SEEN", "b", timestamp=1000), None),
    "bulk_create_edges_temporal": (
        lambda e, ab: e.bulk_create_edges_temporal([{"s": "a", "p": "SEEN", "o": "b", "ts": 1000}]), None),
    "enqueue_for_embedding": (lambda e, ab: e.enqueue_for_embedding(["a"]), None),
}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_a_refused_write_raises_and_writes_nothing(setup, name):
    from iris_vector_graph import GraphWriteError

    owner, reader, sysconn, ab = setup
    call, sqlcode = REFUSED[name]
    before = rr.state(sysconn)
    with pytest.raises(GraphWriteError) as raised:
        call(reader, ab)
    assert raised.value.operation == name
    assert raised.value.sqlcode == sqlcode, raised.value
    assert f"{name} failed" in str(raised.value) and "nothing written" in str(raised.value)
    assert rr.state(sysconn) == before


def test_the_owner_still_gets_false_for_a_duplicate_and_true_for_a_missing_edge(setup):
    """The answers a refusal used to share: a node already there, an edge that is not (USER_GUIDE §2)."""
    owner, _reader, sysconn, _ab = setup
    before = rr.state(sysconn)
    assert owner.create_node("a") is False
    assert owner.delete_edge("a", "KNOWS", "nope") is True
    assert owner.set_edge_weight("a", "KNOWS", "nope", 2.0) is False
    assert owner.reify_edge(10**12, "r8") is None
    assert rr.state(sysconn) == before
