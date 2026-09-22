"""Spec 230 — a refused vector write says which width the column declares.

`_get_embedding_dimension` answers from `self.embedding_dimension` whenever the
constructor was given one, and deliberately so: reading `%Dictionary.CompiledProperty`
on every write contends for the class's Class-Changed_Timestamp and raises SQLCODE -150
under concurrent writers. The consequence is that an engine constructed with
`embedding_dimension=128` against a column declared `VECTOR(DOUBLE, 768)` passes its own
pre-check and is refused by IRIS at the INSERT — with `Field 'Graph_KG.kg_NodeEmbeddings.emb'
(value '3D41...@$vector') failed validation`, which names a hash and a column and neither
of the two widths that actually disagree.

One column read after a write has already failed costs nothing and turns that into a
sentence the caller can act on. The fast path stays.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph import schema as schema_module
from iris_vector_graph.engine import IRISGraphEngine


#: What IRIS answers when the offered vector is not the column's declared width.
_IRIS_104 = Exception(
    "<SQL ERROR>; Details: [SQLCODE: <-104>:<Field validation failed in INSERT>] "
    "[%msg: <Field 'Graph_KG.kg_NodeEmbeddings.emb' (value '3D41@$vector') failed validation>]"
)


def _engine(insert_error=_IRIS_104):
    """An engine whose route is the legacy table and whose INSERT fails."""
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine.vector_dtype = "DOUBLE"
    engine.embedding_dimension = 128
    engine._t = lambda name: f"Graph_KG.{name}"
    engine._assert_node_exists = MagicMock()
    engine._route_for_write = MagicMock(return_value=("kg_NodeEmbeddings", None))
    engine.enforce_embedding_identity = MagicMock(return_value=None)

    cursor = engine.conn.cursor.return_value

    def execute(sql, params=None):
        if sql.lstrip().upper().startswith("INSERT"):
            raise insert_error

    cursor.execute.side_effect = execute
    return engine


def _declares(monkeypatch, width):
    monkeypatch.setattr(
        schema_module.GraphSchema,
        "get_embedding_dimension",
        staticmethod(lambda cursor, table_name=None: width),
    )


def test_a_refused_write_names_both_widths(monkeypatch):
    _declares(monkeypatch, 768)
    engine = _engine()

    with pytest.raises(ValueError) as exc:
        engine.store_embedding("n1", [0.5] * 128)

    message = str(exc.value)
    assert "768" in message
    assert "128" in message
    assert "Graph_KG.kg_NodeEmbeddings" in message
    # The driver's own error is kept, not swallowed: it carries the SQLCODE.
    assert "-104" in str(exc.value.__cause__)


def test_a_batch_is_diagnosed_the_same_way(monkeypatch):
    _declares(monkeypatch, 768)
    engine = _engine()

    with pytest.raises(ValueError) as exc:
        engine.store_embeddings([{"node_id": "n1", "embedding": [0.5] * 128}])

    assert "768" in str(exc.value)
    assert "128" in str(exc.value)


def test_a_failure_that_is_not_about_width_is_left_alone(monkeypatch):
    """Widths agree, so whatever refused the write was something else.

    Diagnosing every failed INSERT as a width problem would be a second wrong answer
    on top of the first.
    """
    _declares(monkeypatch, 128)
    refusal = RuntimeError("SQLCODE -104: something else entirely")
    engine = _engine(insert_error=refusal)

    with pytest.raises(RuntimeError) as exc:
        engine.store_embedding("n1", [0.5] * 128)

    assert exc.value is refusal


def test_an_undeclared_column_is_left_alone(monkeypatch):
    """`None` means the column has no declared width, so there is no width to compare."""
    _declares(monkeypatch, None)
    engine = _engine()

    with pytest.raises(Exception) as exc:
        engine.store_embedding("n1", [0.5] * 128)

    assert exc.value is _IRIS_104
