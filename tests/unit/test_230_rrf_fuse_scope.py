"""Spec 230 US2 (FR-008) — fused search is scoped, and actually fuses.

`kg_RRF_FUSE` takes a `graphId` and hands it to the vector leg only. Its own
source carried the admission:

    -- :graphId does NOT reach this leg. `Graph_KG.docs` has no graph_id column, so
    -- there is nothing to filter on, and the ids it answers are document ids while
    -- the vector leg answers node ids — two key spaces the FULL OUTER JOIN below
    -- can never match.

Two defects in one comment. The text leg reads every graph's documents, and the
`FULL OUTER JOIN` that is supposed to fuse the legs joins node IDs to document
IDs, so it never matches: every row came from one leg with the other leg's
columns NULL. Re-keying `docs` (FR-007) gives both legs one key space; passing
`:graphId` into `kg_TXT` gives both legs one graph.

These tests read the generated procedure text. Only IRIS can prove the fusion
ranks correctly — `tests/e2e/test_230_retrieval_scope.py` does that.
"""

from __future__ import annotations

import re

from iris_vector_graph.schema import GraphSchema


def _procedures() -> dict[str, str]:
    """Each `CREATE OR REPLACE PROCEDURE`/`FUNCTION` body, keyed by bare name."""
    out: dict[str, str] = {}
    for statement in GraphSchema.get_procedures_sql_list():
        match = re.search(
            r"CREATE OR REPLACE (?:PROCEDURE|FUNCTION)\s+[\w.]*?(\w+)\s*\(",
            statement,
            re.IGNORECASE,
        )
        if match:
            out[match.group(1)] = statement
    return out


def _fuse() -> str:
    procedures = _procedures()
    assert "kg_RRF_FUSE" in procedures, sorted(procedures)
    return procedures["kg_RRF_FUSE"]


def _txt() -> str:
    procedures = _procedures()
    assert "kg_TXT" in procedures, sorted(procedures)
    return procedures["kg_TXT"]


def _uncommented(sql: str) -> str:
    """The statement with `--` comments stripped.

    A predicate described in a comment is not a predicate, and the comment being
    replaced here is one that described the missing predicate in detail.
    """
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def test_kg_txt_takes_a_graph():
    signature = _txt().split("LANGUAGE SQL")[0]

    assert re.search(r"IN\s+graphId\s+VARCHAR\(256\)", signature), (
        f"kg_TXT has no graph parameter, so its caller cannot scope it:\n{signature}"
    )


def test_kg_txt_filters_by_that_graph():
    body = _uncommented(_txt())

    assert re.search(
        r"COALESCE\(\s*d\.graph_id\s*,\s*''\s*\)\s*=\s*COALESCE\(\s*:graphId\s*,\s*''\s*\)",
        body,
    ), (
        "kg_TXT does not restrict docs by graph. COALESCE is required on both sides: "
        "graph_id is nullable on an upgraded install, and IRIS binds an empty host "
        f"variable as NULL so `= :graphId` would match nothing for the default graph:\n{body}"
    )


def test_kg_txt_projects_the_graph_it_matched():
    """The fusing join needs the column, so the leg has to answer with it."""
    body = _uncommented(_txt())
    projection = body[body.upper().index("SELECT") : body.upper().index("FROM")]

    assert "graph_id" in projection, (
        f"kg_TXT does not project graph_id, so kg_RRF_FUSE cannot join on it:\n{projection}"
    )


def test_the_text_leg_of_the_fusion_is_given_the_graph():
    body = _uncommented(_fuse())

    assert re.search(r"kg_TXT\(\s*:qtext\s*,\s*:k2\s*,\s*0\s*,\s*:graphId\s*\)", body), (
        "kg_RRF_FUSE still calls kg_TXT without :graphId, so a scoped fusion ranks "
        f"one graph's vectors against every graph's documents:\n{body}"
    )


def test_both_legs_are_joined_on_the_graph_as_well_as_the_id():
    body = _uncommented(_fuse())
    join = re.search(r"FULL OUTER JOIN\s+K\s+ON\s+(.*?)\n\s*\)", body, re.DOTALL)

    assert join, f"the fusing join is not where this test expects it:\n{body}"
    condition = " ".join(join.group(1).split())

    assert "graph_id" in condition, (
        "the legs are joined on id alone. Two graphs holding the same node ID fuse "
        f"into one row, mixing a vector score from one graph with text from another: {condition}"
    )
    assert "V.id = K.id" in condition, (
        f"the legs no longer agree on the id, which is what fusion means: {condition}"
    )


def test_the_fused_row_carries_a_graph_from_whichever_leg_answered():
    """`COALESCE(V.x, K.x)` is the existing idiom for a `FULL OUTER JOIN`: either
    side can be the NULL one, so neither side can be named alone."""
    body = _uncommented(_fuse())

    assert re.search(
        r"COALESCE\(\s*V\.graph_id\s*,\s*K\.graph_id\s*\)",
        body,
    ), (
        "the fused projection names one leg's graph_id, which is NULL for every row "
        f"the other leg alone answered:\n{body}"
    )


def test_the_fusion_still_answers_four_columns():
    """`(id, rrf, vs, ts)` is the shape callers unpack positionally.

    `tests/unit/test_ivf_index.py` asserts `len(row) == 4`. Adding graph_id to the
    legs is internal; widening the answer is a second breaking change nobody asked
    for, and the caller already knows which graph it asked about.
    """
    body = _uncommented(_fuse())
    final = body[body.rindex("SELECT TOP") :]
    projection = final[: final.upper().index("FROM")]
    columns = [part.strip() for part in projection.split("TOP :k", 1)[1].split(",")]

    assert columns == ["id", "rrf", "vs", "ts"], (
        f"the fused projection changed shape, breaking positional callers: {columns}"
    )


def test_the_leak_comment_is_gone():
    """The comment documented the defect. Leaving it once fixed is worse than
    never having written it: the next reader believes the leak is still open."""
    sql = _fuse()

    assert ":graphId does NOT reach this leg" not in sql, (
        "kg_RRF_FUSE still carries the comment saying the text leg is unscoped"
    )
    assert "two key spaces the FULL OUTER JOIN below can never match" not in sql, (
        "kg_RRF_FUSE still carries the comment saying its join cannot match"
    )


def test_the_vector_leg_keeps_its_graph():
    """Guard the half that already worked: it is the same statement being edited."""
    body = _uncommented(_fuse())

    assert re.search(r"kg_KNN_VEC\(\s*:queryVector\s*,\s*:k1\s*,\s*NULL\s*,\s*:graphId\s*\)", body), (
        f"the vector leg lost its graph:\n{body}"
    )
