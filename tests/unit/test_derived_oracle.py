"""The derived-store oracle, against an in-memory host and fake rows. No IRIS.

Each Violation kind is shown to be detected and the consistent state is shown to be clean. The stored counters
`deg`/`degp` are gone (spec 249): the oracle no longer verifies them, and instead verifies the degree cache `degc` that
`Graph.KG.Adj.Degree` keeps, which must be exact whenever its generation is current (RISK R9: an entry for a source with
no rows left must be seen, not pass as "0 mismatches").
"""

from __future__ import annotations

from collections import Counter

import pytest

from tests.support.derived_oracle import (
    ADJACENCY_KINDS,
    ADVISORY_KINDS,
    ALL_PREDICATES,
    EXACT_KINDS,
    KINDS,
    DerivedOracle,
    Violation,
    graph_key,
)


def _collate(sub):
    """IRIS collation: numeric subscripts sort before string ones, each group in its own order."""
    if isinstance(sub, (int, float)):
        return (0, sub, "")
    return (1, 0, str(sub))


class DictHost:
    """A dict-backed stand-in for ``iris_vector_graph.host``: ``get`` and ``next_subscript`` over global tuples.

    A global is stored under the key ``(name, sub1, ..., subN)``. ``next_subscript(name, *subs)`` takes the last
    subscript as the position to move past, and ``""`` as the start, as the Native API does.
    """

    def __init__(self):
        self.store = {}

    def set(self, value, name, *subs):
        self.store[(name, *subs)] = value

    def get(self, name, *subs):
        return self.store.get((name, *subs))

    def next_subscript(self, name, *subs, reverse=False):
        parent, after = subs[:-1], subs[-1]
        depth = len(parent)
        siblings = set()
        for key in self.store:
            if key[0] == name and len(key) > depth + 1 and key[1 : depth + 1] == parent:
                siblings.add(key[depth + 1])
        ordered = sorted(siblings, key=_collate, reverse=reverse)
        if after == "":
            return ordered[0] if ordered else None
        for candidate in ordered:
            if (_collate(candidate) > _collate(after)) != reverse:
                return candidate
        return None


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        self.conn.sql.append(sql)

    def fetchall(self):
        return list(self.conn.rows)


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def cursor(self):
        return FakeCursor(self)


def write_consistent(host, rows, *, multigraph=False, cache=False):
    """Write the derived stores exactly as the engine does for ``rows``: out and in, with the default graph under key
    ``0`` (an int, as ObjectScript stores it). With ``cache`` also fill the degree cache the way ``Adj.Degree`` does:
    ``^KG("degc", g, s, key) = (gen, count)`` for every source, total (``$Char(1)``) and per predicate, with the
    generation read from ``^KG("gen", g, s)`` (absent means 0)."""
    counts = Counter()
    pcounts = Counter()
    for graph_id, s, p, o, ekey in rows:
        g = _graph_subscript(graph_id)
        host.set(1.0, "^KG", "out", g, s, p, o)
        host.set(1.0, "^KG", "in", g, o, p, s)
        counts[(g, s)] += 1
        pcounts[(g, s, p)] += 1
        if multigraph:
            host.set(1.0, "^KG", "out", g, s, p, o, ekey)
            host.set(1.0, "^KG", "in", g, o, p, s, ekey)
    if cache:
        for (g, s), n in counts.items():
            host.set((_gen(host, g, s), n), "^KG", "degc", g, s, ALL_PREDICATES)
        for (g, s, p), n in pcounts.items():
            host.set((_gen(host, g, s), n), "^KG", "degc", g, s, p)
    return host


def _gen(host, g, s):
    return int(host.get("^KG", "gen", g, s) or 0)


def _graph_subscript(graph_id):
    key = graph_key(graph_id)
    return 0 if key == "0" else key


ROWS = [
    (None, "a", "knows", "b", 0),
    ("", "a", "knows", "c", 0),
    ("", "a", "likes", "c", 0),
    ("", "b", "knows", "c", 0),
    ("g1", "x", "p", "y", 0),
]


def _oracle(host, rows, **kw):
    return DerivedOracle(host, FakeConn(rows), **kw)


def _kinds(violations):
    return {v.kind for v in violations}


def _fresh(rows=ROWS, **kw):
    host = write_consistent(DictHost(), rows, **kw)
    return host


# ------------------------------------------------------------------------------------------------ the clean state


def test_a_consistent_state_reports_no_violations():
    assert _oracle(_fresh(), ROWS).violations() == []


def test_an_empty_graph_and_empty_stores_report_nothing():
    assert _oracle(DictHost(), []).violations() == []


def test_violation_kinds_are_the_six_documented_ones():
    assert KINDS == {
        "ghost_out",
        "ghost_in",
        "missing_out",
        "missing_in",
        "out_in_mismatch",
        "degc_mismatch",
    }
    assert ADJACENCY_KINDS | {"degc_mismatch"} == EXACT_KINDS == KINDS
    assert not (ADJACENCY_KINDS & {"degc_mismatch"})


def test_no_kind_is_advisory_any_more():
    """The counters were the only kinds a rollback could leave behind. The cache is validated by generation, so a
    rollback over-bumps and costs a recount but never a wrong answer."""
    assert ADVISORY_KINDS == frozenset()


# ----------------------------------------------------------------------------------------------- graph key mapping


@pytest.mark.parametrize("graph_id", [None, "", "\x00", "\x00\x00"])
def test_null_empty_and_nul_padded_graph_ids_are_the_default_graph_key_zero(graph_id):
    assert graph_key(graph_id) == "0"


def test_a_named_graph_keeps_its_name_as_the_key():
    assert graph_key("g1") == "g1"


def test_rows_read_the_default_graph_under_key_zero_whichever_spelling_they_use():
    oracle = _oracle(DictHost(), [(None, "a", "p", "b", 0), ("", "a", "p", "c", 0)])
    assert {r[0] for r in oracle.rows()} == {"0"}


def test_rows_select_coalesced_graph_id_so_a_null_graph_is_read_as_the_default_graph():
    oracle = _oracle(DictHost(), [])
    oracle.rows()
    assert "COALESCE(graph_id, '')" in oracle.last_sql


def test_rows_read_ekey_only_when_multigraph():
    plain = _oracle(DictHost(), [])
    plain.rows()
    assert "ekey" not in plain.last_sql.split("FROM")[0]

    multi = _oracle(DictHost(), [], multigraph=True)
    multi.rows()
    assert "ekey" in multi.last_sql.split("FROM")[0]


# -------------------------------------------------------------------------------------------- ghost and missing


def test_an_out_entry_with_no_row_is_ghost_out():
    host = _fresh()
    host.set(1.0, "^KG", "out", 0, "a", "knows", "zzz")
    host.set(1.0, "^KG", "in", 0, "zzz", "knows", "a")
    found = _oracle(host, ROWS).violations()
    assert Violation("ghost_out", "out", ("0", "a", "knows", "zzz")) in found
    assert Violation("ghost_in", "in", ("0", "zzz", "knows", "a")) in found


def test_an_in_entry_with_no_row_is_ghost_in_and_a_mirror_mismatch():
    host = _fresh()
    host.set(1.0, "^KG", "in", 0, "q", "knows", "a")
    found = _oracle(host, ROWS).violations()
    assert _kinds(found) == {"ghost_in", "out_in_mismatch"}
    assert Violation("ghost_in", "in", ("0", "q", "knows", "a")) in found


def test_a_row_with_no_out_entry_is_missing_out():
    host = _fresh()
    del host.store[("^KG", "out", 0, "a", "knows", "b")]
    found = _oracle(host, ROWS).violations()
    assert Violation("missing_out", "out", ("0", "a", "knows", "b")) in found


def test_a_row_with_no_in_entry_is_missing_in():
    host = _fresh()
    del host.store[("^KG", "in", "g1", "y", "p", "x")]
    found = _oracle(host, ROWS).violations()
    assert Violation("missing_in", "in", ("g1", "y", "p", "x")) in found


def test_a_missing_out_entry_is_also_a_mirror_disagreement():
    host = _fresh()
    del host.store[("^KG", "out", 0, "a", "knows", "b")]
    del host.store[("^KG", "in", 0, "b", "knows", "a")]
    found = _oracle(host, ROWS).violations()
    assert _kinds(found) == {"missing_out", "missing_in"}


# ------------------------------------------------------------------------------------------- mirror disagreement


def test_an_out_entry_without_its_in_mirror_is_out_in_mismatch():
    host = _fresh()
    del host.store[("^KG", "in", "g1", "y", "p", "x")]
    found = _oracle(host, ROWS).violations()
    assert "out_in_mismatch" in _kinds(found)


def test_an_in_entry_without_its_out_mirror_is_out_in_mismatch_on_the_in_side():
    host = _fresh()
    del host.store[("^KG", "out", "g1", "x", "p", "y")]
    found = _oracle(host, ROWS).violations()
    mismatches = [v for v in found if v.kind == "out_in_mismatch"]
    assert mismatches and mismatches[0].store == "in"  # the out entry is gone, the in entry remains, so it is reported on in


def test_a_triple_present_in_only_one_mirror_with_no_row_reports_both_kinds():
    host = _fresh()
    host.set(1.0, "^KG", "out", 0, "m", "p", "n")
    found = _oracle(host, ROWS).violations()
    assert {"ghost_out", "out_in_mismatch"} <= _kinds(found)


# ------------------------------------------------------------------------------------- the old counters are ignored


def test_deg_and_degp_entries_are_not_the_oracles_business_any_more():
    """Leftovers of an older release (or of a restore of an old archive) are neither verified nor reported."""
    host = _fresh()
    host.set(99, "^KG", "deg", 0, "a")
    host.set(1, "^KG", "deg", 0, "ghost")
    host.set(1, "^KG", "degp", 0, "ghost", "knows")
    assert _oracle(host, ROWS).violations() == []


# ------------------------------------------------------------------------------------------ the degree cache (R9)


def test_a_cache_that_matches_the_rows_is_clean():
    host = _fresh(cache=True)
    assert _oracle(host, ROWS).violations() == []


def test_a_current_entry_with_the_wrong_total_is_degc_mismatch():
    host = _fresh(cache=True)
    host.set((0, 5), "^KG", "degc", 0, "a", ALL_PREDICATES)
    found = _oracle(host, ROWS).violations()
    assert Violation("degc_mismatch", "degc", ("0", "a", ALL_PREDICATES)) in found
    assert _kinds(found) == {"degc_mismatch"}


def test_a_current_entry_with_the_wrong_per_predicate_count_is_degc_mismatch():
    host = _fresh(cache=True)
    host.set((0, 9), "^KG", "degc", 0, "a", "knows")
    found = _oracle(host, ROWS).violations()
    assert Violation("degc_mismatch", "degc", ("0", "a", "knows")) in found


def test_R9_a_current_entry_for_a_source_with_zero_rows_is_degc_mismatch():
    """The ghost of spike R1, in the cache: rows and adjacency are gone and a valid entry still says 1.

    The old per-source recount never visited this source. The oracle walks the cache itself."""
    rows = [(None, "a", "knows", "b", 0), (None, "c", "knows", "d", 0)]
    host = write_consistent(DictHost(), rows, cache=True)
    for key in [("^KG", "out", 0, "a", "knows", "b"), ("^KG", "in", 0, "b", "knows", "a")]:
        del host.store[key]
    found = _oracle(host, [(None, "c", "knows", "d", 0)]).violations()
    assert Violation("degc_mismatch", "degc", ("0", "a", ALL_PREDICATES)) in found
    assert Violation("degc_mismatch", "degc", ("0", "a", "knows")) in found
    assert not [v for v in found if v.key[:2] == ("0", "c")]


def test_an_entry_whose_generation_is_behind_is_not_checked():
    """A stale entry is not wrong: it is not valid. The reader recounts when generations differ."""
    host = _fresh(cache=True)
    host.set(3, "^KG", "gen", 0, "a")  # a write after the entry was stored
    host.set((0, 77), "^KG", "degc", 0, "a", ALL_PREDICATES)  # would be wrong if it were current
    assert _oracle(host, ROWS).violations() == []


def test_an_absent_generation_counts_as_zero():
    host = _fresh(cache=True)
    host.set((0, 5), "^KG", "degc", 0, "a", ALL_PREDICATES)
    assert "degc_mismatch" in _kinds(_oracle(host, ROWS).violations())


def test_an_entry_in_a_list_shape_is_read_too():
    host = _fresh(cache=True)
    host.set([0, 5], "^KG", "degc", 0, "a", ALL_PREDICATES)
    assert "degc_mismatch" in _kinds(_oracle(host, ROWS).violations())


def test_an_entry_the_host_returned_in_a_shape_nobody_can_read_is_skipped_not_reported():
    host = _fresh(cache=True)
    host.set(b"\x03\x04\x01", "^KG", "degc", 0, "a", ALL_PREDICATES)
    assert _oracle(host, ROWS).violations() == []


def test_a_multigraph_total_counts_parallel_edges():
    rows = [(None, "a", "knows", "b", 1), (None, "a", "knows", "b", 2)]
    host = write_consistent(DictHost(), rows, multigraph=True, cache=True)
    assert _oracle(host, rows, multigraph=True).violations() == []
    host.set((0, 1), "^KG", "degc", 0, "a", ALL_PREDICATES)
    assert "degc_mismatch" in _kinds(_oracle(host, rows, multigraph=True).violations())


# -------------------------------------------------------------------------------------------- multigraph ekey


def test_multigraph_children_that_match_their_rows_are_clean():
    rows = [(None, "a", "knows", "b", 1), (None, "a", "knows", "b", 2)]
    host = write_consistent(DictHost(), rows, multigraph=True)
    host.set(2, "^KG", "out", 0, "a", "knows", "b", "#")  # the live count is not an edge
    host.set(1, "^KG", "out", 0, "a", "knows", "b", 1)
    host.set(1, "^KG", "out", 0, "a", "knows", "b", 2)
    assert _oracle(host, rows, multigraph=True).violations() == []


def test_a_multigraph_child_with_no_row_is_ghost_out():
    rows = [(None, "a", "knows", "b", 1), (None, "a", "knows", "b", 2)]
    host = write_consistent(DictHost(), rows, multigraph=True)
    host.set(1, "^KG", "out", 0, "a", "knows", "b", 7)
    found = _oracle(host, rows, multigraph=True).violations()
    assert Violation("ghost_out", "out", ("0", "a", "knows", "b", "7")) in found


def test_the_count_sentinel_is_not_mistaken_for_an_edge():
    rows = [(None, "a", "knows", "b", 1)]
    host = write_consistent(DictHost(), rows, multigraph=True)
    host.set(1, "^KG", "out", 0, "a", "knows", "b", "#")
    assert _oracle(host, rows, multigraph=True).violations() == []
