"""Spec 227 T030 — the stored procedure is graph-scoped and one parameter shorter.

`kg_KNN_VEC`'s fourth argument was `embeddingConfig`: accepted, ignored, and
documented as removable in 4.0.0. It is now `graphId`, and load-bearing.

The slot keeps its position and its VARCHAR type, so a 3.2.0 four-argument call
still compiles — and now searches a graph named after the caller's model. That is
not a deprecation that can be warned about from SQL, which is why FR-025 requires
it in the changelog and why this file asserts the old name is gone rather than
merely unused.

`kg_RRF_FUSE` passed `NULL` into that slot to satisfy the arity. It now threads a
real graph, because a fusion that scores vectors from every graph and then ranks
them would reintroduce the leak one layer up.
"""

import re

import pytest

from iris_vector_graph.schema import GraphSchema


@pytest.fixture
def procedures():
    return GraphSchema.get_procedures_sql_list()


def _statement(procedures, name: str) -> str:
    """The statement that *creates* `name`.

    Matched on `CREATE ... PROCEDURE`, not on the bare name: `kg_KNN_VEC(` also
    appears inside `kg_RRF_FUSE`'s body, and matching that would make the two
    statements indistinguishable.
    """
    needle = f"PROCEDURE Graph_KG.{name}("
    matches = [s for s in procedures if needle in s]
    assert matches, f"no statement creates {name}"
    assert len(matches) == 1, f"{name} is created more than once: {len(matches)} statements"
    return matches[0]


# --- kg_KNN_VEC -----------------------------------------------------------------


def _declared_parameters(sql: str, name: str) -> list:
    """The parameter list, with SQL line comments stripped.

    Comments have to go before splitting on commas: the prose explaining the slot
    change contains commas of its own.
    """
    body = re.search(rf"{name}\((.*?)\)\s*LANGUAGE", sql, re.DOTALL)
    assert body, sql
    lines = [
        ln for ln in body.group(1).splitlines()
        if ln.strip() and not ln.strip().startswith("--")
    ]
    return [p.strip() for p in " ".join(lines).split(",") if p.strip()]


def test_the_ignored_parameter_is_gone(procedures):
    """Asserted against the declaration, not the file text.

    The comment above the parameter still names `embeddingConfig`, on purpose —
    the slot changed meaning in place and a reader needs to know that.
    """
    sql = _statement(procedures, "kg_KNN_VEC")
    declared = _declared_parameters(sql, "kg_KNN_VEC")
    assert not any("embeddingConfig" in p for p in declared), declared


def test_the_fourth_parameter_is_the_graph(procedures):
    sql = _statement(procedures, "kg_KNN_VEC")
    declared = _declared_parameters(sql, "kg_KNN_VEC")
    assert len(declared) == 4, declared
    assert declared[-1].startswith("IN graphId VARCHAR"), declared


def test_both_sides_of_the_graph_comparison_are_coalesced(procedures):
    """An empty host variable binds as SQL NULL in IRIS (FR-005)."""
    sql = _statement(procedures, "kg_KNN_VEC")
    assert re.search(
        r"COALESCE\(n\.graph_id,\s*''\)\s*=\s*COALESCE\(:graphId,\s*''\)", sql
    ), sql


def test_the_label_join_is_scoped(procedures):
    sql = _statement(procedures, "kg_KNN_VEC")
    assert re.search(
        r"COALESCE\(L\.graph_id,\s*''\)\s*=\s*COALESCE\(:graphId,\s*''\)", sql
    ), f"the rdf_labels join is unscoped, so the label filter is the leak: {sql}"


def test_the_projected_column_is_node_id(procedures):
    sql = _statement(procedures, "kg_KNN_VEC")
    assert "n.node_id" in sql, sql
    assert not re.search(r"\bn\.id\b", sql), (
        "n.id no longer exists on the re-keyed embedding table"
    )


def test_to_vector_stays_unlengthened(procedures):
    """ADR-0005: a declared length reshapes the query and returns a plausible
    wrong score instead of SQLCODE -257."""
    sql = _statement(procedures, "kg_KNN_VEC")
    assert "TO_VECTOR(:queryInput, DOUBLE)" in sql, sql
    assert not re.search(r"TO_VECTOR\(:queryInput,\s*DOUBLE,\s*\d+\)", sql), sql


# --- kg_RRF_FUSE ----------------------------------------------------------------


def test_rrf_fuse_no_longer_passes_null_into_the_fourth_slot(procedures):
    sql = _statement(procedures, "kg_RRF_FUSE")
    assert "kg_KNN_VEC(:queryVector, :k1, NULL, NULL)" not in sql, (
        "the four-argument call existed to satisfy the ignored parameter (FR-023); "
        "passing NULL now means 'the default graph' by accident rather than by "
        "decision"
    )


def test_rrf_fuse_threads_a_graph(procedures):
    """Otherwise fusion re-widens what kg_KNN_VEC just narrowed."""
    sql = _statement(procedures, "kg_RRF_FUSE")
    assert re.search(r"IN\s+graphId\s+VARCHAR", sql), sql
    assert re.search(r"kg_KNN_VEC\(:queryVector,\s*:k1,\s*NULL,\s*:graphId\)", sql), sql


# --- the removed keyword argument ----------------------------------------------


def test_get_procedures_sql_list_no_longer_accepts_embedding_dimension():
    """FR-024. It was inert and documented in docs/KNOWN_ISSUES.md.

    Removed rather than kept-and-warned: a parameter that looks like it controls
    the vector width, and does not, is worse than no parameter.
    """
    with pytest.raises(TypeError):
        GraphSchema.get_procedures_sql_list(embedding_dimension=384)
