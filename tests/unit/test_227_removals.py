"""The 4.0.0 removals, pinned before they are made (T072).

Three things go away in 4.0.0, and each one was inert in a way that read as
load-bearing:

- `get_procedures_sql_list(embedding_dimension=...)`: accepted, ignored, and named
  after the vector width it never set (FR-024).
- `kg_KNN_VEC`'s `IN embeddingConfig VARCHAR(128)`: accepted, ignored, and now
  replaced by the graph in that slot (FR-022).
- `kg_RRF_FUSE`'s four-argument `kg_KNN_VEC` call, which passed `NULL` into that
  slot to satisfy the arity. `NULL` there now means "the default graph", so leaving
  it would make a fusion silently search one graph while its caller asked for
  another (FR-023).

The removals are the easy half. The half that bites is the callers: a procedure
whose arity changed is still a valid SQL identifier, so an out-of-date `CALL` fails
at runtime, in ObjectScript, where nothing in this test tree compiles it.
"""

import re
from pathlib import Path

import pytest

from iris_vector_graph.schema import GraphSchema

REPO = Path(__file__).resolve().parents[2]


def _procedures():
    return GraphSchema.get_procedures_sql_list()


def _statement(name):
    for sql in _procedures():
        if re.search(rf"(PROCEDURE|FUNCTION)\s+\S*{name}\s*\(", sql):
            return sql
    raise AssertionError(f"{name} is not in the generated procedure list")


# --- FR-024: the inert dimension parameter ------------------------------------


def test_embedding_dimension_is_not_a_parameter_any_more():
    with pytest.raises(TypeError):
        GraphSchema.get_procedures_sql_list(embedding_dimension=384)


def test_the_generated_to_vector_carries_no_length():
    """ADR-0005: a declared length makes IRIS reshape the query vector.

    `TO_VECTOR(:queryInput, DOUBLE, 4)` pads or truncates a six-element query to
    four and scores the reshaped value — a perfect 1.0 for a query that should have
    raised `SQLCODE -257`. Width is enforced on the column and in the registry.
    """
    for sql in _procedures():
        for call in re.findall(r"TO_VECTOR\s*\([^)]*\)", sql):
            assert call.count(",") <= 1, call


# --- FR-022: the inert embeddingConfig parameter -------------------------------


def test_no_generated_procedure_declares_embeddingconfig():
    """Matched on the declaration, not the name: the comment above `kg_KNN_VEC`'s
    fourth parameter still says `embeddingConfig`, which is the point of it."""
    declarations = []
    for sql in _procedures():
        declarations += re.findall(r"^\s*IN\s+\w+\s+\w+", sql, flags=re.MULTILINE)
    assert not any("embeddingConfig" in d for d in declarations), declarations


# --- FR-023: the four-argument call, and the callers of the caller -------------


def test_rrf_fuse_passes_the_graph_into_the_fourth_slot():
    sql = _statement("kg_RRF_FUSE")
    call = re.search(r"kg_KNN_VEC\(([^)]*)\)", sql)
    assert call, sql
    args = [a.strip() for a in call.group(1).split(",")]
    assert len(args) == 4, args
    assert args[3] == ":graphId", args


def test_rrf_fuse_declares_the_graph():
    sql = _statement("kg_RRF_FUSE")
    assert re.search(r"IN\s+graphId\s+VARCHAR", sql), sql


def test_every_objectscript_call_of_rrf_fuse_passes_the_graph():
    """`CALL kg_RRF_FUSE(?, ?, ?, ?, ?, ?)` is six arguments against seven parameters.

    The procedure gained `graphId`, so a caller left at six fails at runtime with an
    arity error — inside ObjectScript, which no test in this tree compiles. Caught
    by counting placeholders in the source instead.
    """
    offenders = []
    for path in (REPO / "iris_src").rglob("*.cls"):
        text = path.read_text()
        for call in re.findall(r"CALL\s+\S*kg_RRF_FUSE\s*\(([^)]*)\)", text):
            if call.count("?") < 7:
                offenders.append(f"{path.relative_to(REPO)}: CALL kg_RRF_FUSE({call})")
    assert not offenders, offenders


def test_python_rrf_fuse_threads_the_graph_into_the_vector_leg():
    """The Python fusion path fuses client-side, so it has to scope its own legs."""
    import inspect

    from iris_vector_graph.engine import IRISGraphEngine

    sig = inspect.signature(IRISGraphEngine.kg_RRF_FUSE)
    assert "graph" in sig.parameters, sig
    assert sig.parameters["graph"].default is None, sig

    src = inspect.getsource(IRISGraphEngine.kg_RRF_FUSE)
    assert re.search(r"kg_KNN_VEC\([^)]*graph=graph", src), src


def test_hybrid_fusion_can_be_scoped():
    """`HybridSearchFusion.multi_modal_search` fuses its own legs too.

    Left without a `graph`, the only search in the library that combines vector and
    text could reach nothing but the default graph — the caller had no parameter to
    pass and no error to read.
    """
    import inspect

    from iris_vector_graph.fusion import HybridSearchFusion

    sig = inspect.signature(HybridSearchFusion.multi_modal_search)
    assert "graph" in sig.parameters, sig
    assert sig.parameters["graph"].default is None, sig

    src = inspect.getsource(HybridSearchFusion.multi_modal_search)
    assert re.search(r"kg_KNN_VEC\([^)]*graph=graph", src), src
