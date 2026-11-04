"""4.6.0 R206: `khop` answered `[]` for a reader holding the database at `R` (walkthrough 1 P8).

The reader holds ADMIN_GUIDE §3's reading role: the database at `R`, `%Native_ClassExecution:U`,
`%Native_GlobalAccess:U`, and SELECT and EXECUTE on the IVG Schema; it may not SELECT from the class dictionary. The
engine's capability probe read "no IVG classes" from that refusal, so every read gated on the classes took its empty
path. The answers below were worked out by hand for the default graph a -> b -> c -> d (KNOWS): two hops from `a`
reach `b` (step 1) and `c` (step 2). A read that must write a work global under `R` raises naming what it could not
write, never an empty answer.

    IVG_NAMESPACE=IVG460FXO pytest tests/e2e/test_460_r206_reader_reads_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

TAG = "R206"


@pytest.fixture(scope="module")
def reader():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    user = rr.make_reader(TAG, rr.reading_resources("R"))
    rconn = rr.connect(user, rr.PASSWORD)
    try:
        yield IRISGraphEngine(rconn, embedding_dimension=rr.DIM)
    finally:
        rconn.close()
        rr.drop_reader(TAG)
        owner.erase_graph(None)
        sysconn.close()


def test_the_reader_finds_the_classes(reader):
    assert reader.capabilities.objectscript_deployed is True


def test_khop_answers_the_two_hop_neighbourhood(reader):
    assert reader.khop("a", hops=2) == {
        "nodes": ["a", "b", "c"],
        "edges": [{"s": "a", "p": "KNOWS", "o": "b", "w": 1, "step": 1},
                  {"s": "b", "p": "KNOWS", "o": "c", "w": 1, "step": 2}],
    }


def test_khop_of_a_missing_seed_is_still_empty(reader):
    assert reader.khop("nope", hops=2) == {"nodes": [], "edges": []}


def test_a_read_that_writes_a_work_global_raises_naming_it(reader):
    """degree_centrality caches each degree in ^KG("degc"): at `R` the reader cannot, and is told so."""
    with pytest.raises(RuntimeError, match=r'<PROTECT>.*\^KG\("degc"'):
        reader.degree_centrality()


def test_a_refused_erase_names_the_refusal_not_a_missing_class(reader):
    """delete_nodes_by_prefix said "Graph.KG.Eraser.EraseNodes is not deployed" to a reader (the same probe)."""
    with pytest.raises(Exception) as raised:
        reader.delete_nodes_by_prefix("d")
    assert "not deployed" not in str(raised.value)


def test_a_writer_without_the_dictionary_is_stopped_by_ledger_strict_mode():
    """ADMIN_GUIDE §3's writing role holds no SELECT on the class dictionary either. The ledger guard asked it in SQL
    only, read the refusal as "no ledger", and let every structural write through a strict ledger."""
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.ledger.errors import LedgerStrictModeError

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    user = rr.make_reader("R206W", rr.reading_resources("RW"),
                          sql_privileges=("SELECT", "EXECUTE", "INSERT", "UPDATE", "DELETE"))
    wconn = rr.connect(user, rr.PASSWORD)
    owner.ledger.enable(strict=True)
    try:
        writer = IRISGraphEngine(wconn, embedding_dimension=rr.DIM)
        before = rr.state(sysconn)
        with pytest.raises(LedgerStrictModeError):
            writer.create_node("z")
        assert rr.state(sysconn) == before
    finally:
        owner.ledger.disable()
        wconn.close()
        rr.drop_reader("R206W")
        owner.erase_graph(None)
        sysconn.close()
