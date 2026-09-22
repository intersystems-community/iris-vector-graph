"""Spec 230 US2 (FR-019, FR-020) — every pre-migration edge vector is accounted for.

A 3.2.0 `Graph_KG.kg_EdgeEmbeddings` row is keyed `(s, p, o_id)` and says nothing
about a graph, so two graphs asserting the same edge shared one row and the second
write replaced the first. 4.0.0 keys an edge vector `(graph_id, s, p, o_id)` behind
an identity primary key, which is why the rows move to a new table rather than
having a column added: no `ALTER` reaches an identity key.

The graph of an edge vector is the graph holding the *edge*, read from `rdf_edges` —
not the graph of either endpoint, which is what made this its own placement instead
of a case in 227's node migration. Each row gets one of three answers:

* the triple is asserted in exactly one graph → place it there;
* in more than one → ask the resolver, and quarantine `ambiguous_graph` (or
  `resolver_declined`) if there is no answer;
* in no graph → quarantine `no_edge`.

Defaulting to `''` is not on the list: a vector for graph B's edge, placed in the
default graph, scores and ranks exactly like an answer.
"""

from __future__ import annotations

from iris_vector_graph.migrations.docs_and_edge_vectors import (
    AmbiguousEdgeVector,
    EDGE_VECTOR_QUARANTINE_REASONS,
    place_edge_vectors,
)

from tests.unit.migration_fakes_230 import edge_vectors_conn

E1 = ("n1", "TREATS", "n2")
E2 = ("n3", "CAUSES", "n4")


def _vec(key):
    return {"s": key[0], "p": key[1], "o_id": key[2], "emb": f"emb-of-{key[0]}"}


def test_a_vector_whose_edge_is_in_one_graph_is_placed_there():
    conn = edge_vectors_conn([_vec(E1)], {E1: ["tenant-a"]})

    report = place_edge_vectors(conn)

    assert conn.registry.placed() == {E1: "tenant-a"}
    assert report.rows_placed == {"tenant-a": 1}
    assert report.rows_quarantined == {}


def test_the_default_graph_is_a_placement_like_any_other():
    conn = edge_vectors_conn([_vec(E1)], {E1: [""]})

    report = place_edge_vectors(conn)

    assert conn.registry.placed() == {E1: ""}
    assert report.rows_placed == {"": 1}


def test_a_vector_naming_no_edge_is_quarantined_not_defaulted():
    conn = edge_vectors_conn([_vec(E1)], {})

    report = place_edge_vectors(conn)

    assert conn.registry.quarantined() == {E1: "no_edge"}
    assert report.rows_quarantined == {"no_edge": 1}
    assert conn.registry.placed() == {}


def test_an_ambiguous_vector_is_quarantined_when_no_resolver_is_given():
    conn = edge_vectors_conn([_vec(E1)], {E1: ["tenant-a", "tenant-b"]})

    report = place_edge_vectors(conn)

    assert report.rows_quarantined == {"ambiguous_graph": 1}
    assert conn.registry.quarantined() == {E1: "ambiguous_graph"}


def test_a_resolver_places_an_ambiguous_vector():
    seen: list[AmbiguousEdgeVector] = []

    def resolver(ambiguous):
        seen.append(ambiguous)
        return "tenant-b"

    conn = edge_vectors_conn([_vec(E1)], {E1: ["tenant-a", "tenant-b"]})

    report = place_edge_vectors(conn, resolver=resolver)

    assert conn.registry.placed() == {E1: "tenant-b"}
    assert report.rows_placed == {"tenant-b": 1}
    assert [(a.s, a.p, a.o_id, a.candidate_graphs) for a in seen] == [
        ("n1", "TREATS", "n2", ("tenant-a", "tenant-b"))
    ]


def test_a_resolver_naming_a_graph_that_does_not_assert_the_edge_has_declined():
    """Placing it there would give a graph a vector for an edge it never asserted,
    which is the silent misplacement the quarantine exists to prevent."""
    conn = edge_vectors_conn([_vec(E1)], {E1: ["tenant-a", "tenant-b"]})

    report = place_edge_vectors(conn, resolver=lambda _: "tenant-z")

    assert conn.registry.quarantined() == {E1: "resolver_declined"}
    assert report.rows_quarantined == {"resolver_declined": 1}


def test_a_resolver_that_declines_is_recorded_as_a_decline():
    conn = edge_vectors_conn([_vec(E1)], {E1: ["tenant-a", "tenant-b"]})

    report = place_edge_vectors(conn, resolver=lambda _: None)

    assert report.rows_quarantined == {"resolver_declined": 1}


def test_a_resolver_is_not_consulted_about_an_unambiguous_vector():
    asked = []
    conn = edge_vectors_conn([_vec(E1)], {E1: ["tenant-a"]})

    place_edge_vectors(conn, resolver=lambda a: asked.append(a) or "tenant-b")

    assert asked == []
    assert conn.registry.placed() == {E1: "tenant-a"}


def test_a_resolver_is_not_consulted_about_a_vector_with_no_edge():
    asked = []
    conn = edge_vectors_conn([_vec(E1)], {})

    report = place_edge_vectors(conn, resolver=lambda a: asked.append(a) or "tenant-a")

    assert asked == []
    assert report.rows_quarantined == {"no_edge": 1}


def test_every_row_is_either_placed_or_quarantined():
    orphan = ("n9", "NOTED", "n10")
    shared = ("n5", "LINKS", "n6")
    conn = edge_vectors_conn(
        [_vec(E1), _vec(E2), _vec(orphan), _vec(shared)],
        {E1: ["tenant-a"], E2: ["tenant-b"], shared: ["tenant-a", "tenant-b"]},
    )

    report = place_edge_vectors(conn)

    assert report.rows_accounted == 4, report
    assert report.rows_placed == {"tenant-a": 1, "tenant-b": 1}
    assert report.rows_quarantined == {"no_edge": 1, "ambiguous_graph": 1}


def test_every_quarantine_reason_is_a_declared_one():
    conn = edge_vectors_conn(
        [_vec(E1), _vec(E2)], {E2: ["tenant-a", "tenant-b"]}
    )

    report = place_edge_vectors(conn)

    assert set(report.rows_quarantined) <= set(EDGE_VECTOR_QUARANTINE_REASONS), report


def test_source_rows_are_never_deleted():
    """The reshape drops the source table as a whole, after every row has a home.
    Deleting row by row as the placement runs would leave a killed pass with vectors
    in neither table."""
    conn = edge_vectors_conn([_vec(E1), _vec(E2)], {E1: ["a"], E2: ["b"]})

    place_edge_vectors(conn)

    assert conn.registry.deleted == []
    assert len(conn.registry.source) == 2


def test_a_second_run_changes_nothing():
    conn = edge_vectors_conn(
        [_vec(E1), _vec(E2)], {E1: ["tenant-a"]}
    )

    place_edge_vectors(conn)
    before = conn.registry.snapshot()

    second = place_edge_vectors(conn)

    assert conn.registry.snapshot() == before
    assert second.rows_accounted == 0, (
        f"a re-run re-placed rows that were already placed: {second}"
    )


def test_a_dry_run_reports_without_writing():
    conn = edge_vectors_conn([_vec(E1), _vec(E2)], {E1: ["tenant-a"]})

    report = place_edge_vectors(conn, dry_run=True)

    assert report.rows_placed == {"tenant-a": 1}
    assert report.rows_quarantined == {"no_edge": 1}
    assert conn.registry.staging == []
    assert conn.registry.quarantine == []


def test_no_vector_is_read_into_python():
    """ADR-0005: a `VECTOR(DOUBLE, n)` fetched and re-bound is reshaped by the
    driver's idea of the value. The fake refuses a `SELECT` naming `emb`, so this
    test states the guarantee the fake enforces on every other test in the file."""
    conn = edge_vectors_conn([_vec(E1)], {E1: ["tenant-a"]})

    place_edge_vectors(conn)

    moves = [sql for sql, _ in conn.registry.executed if "emb" in sql]
    assert moves, "the vector never moved"
    assert all(sql.upper().startswith("INSERT INTO") for sql in moves), moves
