"""4.6.0 R206: `khop` answered `[]` for a reader holding the database at `R` (walkthrough 1 P8).

The reader holds ADMIN_GUIDE §3's reading role: the database at `R`, `%Native_ClassExecution:U`,
`%Native_GlobalAccess:U`, and SELECT and EXECUTE on the IVG Schema; it may not SELECT from the class dictionary. The
engine's capability probe read "no IVG classes" from that refusal, so every read gated on the classes took its empty
path. The answers below were worked out by hand for the default graph a -> b -> c -> d (KNOWS): two hops from `a`
reach `b` (step 1) and `c` (step 2). Degree centrality caches each degree in `^KG("degc")`, which a reader at `R`
cannot write: since R050 W1/W3 (81cf0603) the refused store is skipped and the reader gets the answer, where it got
`<PROTECT>` before; it stores nothing (4.6.0 R309 found this test still expecting the refusal: it had not run since).

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


def _cache_digest(nat) -> dict:
    """sha256 of every valued node under ^KG("degc") and ^KG("degw"), the degree caches (subscripts and value)."""
    import hashlib

    out = {}
    for cache in ("degc", "degw"):
        h = hashlib.sha256()

        def walk(prefix: tuple, depth: int) -> None:
            if depth <= 0:
                return
            current = nat.nextSubscript(False, "^KG", *prefix, "")
            while current is not None and current != "":
                subs = prefix + (current,)
                if int(nat.isDefined("^KG", *subs)) in (1, 11):
                    h.update(repr((subs, nat.getString("^KG", *subs))).encode())
                walk(subs, depth - 1)
                current = nat.nextSubscript(False, "^KG", *prefix, current)

        walk((cache,), 8)
        out[cache] = h.hexdigest()
    return out


def test_degree_centrality_answers_a_reader_and_stores_nothing(reader):
    """a -> b -> c -> d, direction out: n = 4 nodes with an edge, so each score is degree / 3: a, b and c have one
    out-edge (1/3), d none (0; it has an edge, in, so it has a row)."""
    import iris

    sysconn = rr.connect()
    try:
        nat = iris.createIRIS(sysconn)
        before = _cache_digest(nat)
        rows = reader.degree_centrality()
        after = _cache_digest(nat)
    finally:
        sysconn.close()
    got = {r["id"]: (r["degree"], r["score"]) for r in rows}
    assert set(got) == set("abcd")
    for n in "abc":
        assert got[n][0] == 1 and got[n][1] == pytest.approx(1 / 3, abs=1e-12), (n, got[n])
    assert got["d"][0] == 0 and got["d"][1] == pytest.approx(0.0, abs=1e-12), got["d"]
    assert [r["id"] for r in rows][-1] == "d"  # highest first
    assert after == before, "the reader's call changed the degree cache"


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
