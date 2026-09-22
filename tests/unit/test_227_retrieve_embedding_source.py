"""Spec 227 sweep — `ivg.retrieve` gets its query vector from the engine, not only IRIS.

The vector arm was hardcoded to IRIS's native `EMBEDDING(text, config)`:

    CAST(VECTOR_COSINE(e.emb, EMBEDDING(?, ?)) AS DOUBLE) AS score

with the config taken from the call's 6th argument, which defaults to `''`. A
two-argument `CALL ivg.retrieve('aspirin', 5)` therefore asked IRIS to embed with a
blank config name, and IRIS answered at Query Open with

    SQLCODE -280 <Embedding configuration error> %Embedding.Config ' ' does not exist.

Nothing about that is recoverable by the caller in a namespace with no config: the
`ivg-iris-enterprise` build (`irishealth:2026.3.0AI.113.0`) ships `%Embedding.Config`
and `%Embedding.SentenceTransformers` as classes, `SELECT * FROM %Embedding.Config`
returns no rows, and `sentence_transformers` is not importable inside the instance —
so no config can be created there either. Native embedding is simply unavailable.

The engine already owns this decision and documents it (`IRISGraphEngine.embed_text`,
`iris_vector_graph/_engine/embeddings.py`): native `EMBEDDING()` when an
`embedding_config` is set, otherwise the configured Python embedder, otherwise a
default SentenceTransformer. The translator bypassed that chain, which is the defect.
Resolution order for the vector arm is now:

1. the call's 6th argument — the caller named a config, so use `EMBEDDING(?, ?)`
2. the engine's own `embedding_config` — same native call, config filled in
3. an engine with no config — `engine.embed_text(query)` produces the vector and the
   arm binds it through `TO_VECTOR(?, DOUBLE)`, exactly as `ivg.vector.search` does
   for a list argument
4. no engine at all — `translate_to_sql()` without one only produces text, so the
   native call is left in place rather than guessing

Live gate: `tests/integration/test_227_procedure_statements_execute.py`.
"""

import json

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import set_schema_prefix, translate_to_sql

QUERY = "aspirin"


class StubEngine:
    """The two attributes `_translate_retrieve` may read, and a call log."""

    def __init__(self, embedding_config=None, vector=None, error=None):
        self.embedding_config = embedding_config
        self._vector = vector if vector is not None else [0.1, 0.2, 0.3]
        self._error = error
        self.embed_calls = []

    def embed_text(self, text):
        self.embed_calls.append(text)
        if self._error is not None:
            raise self._error
        return list(self._vector)


def _translate(cypher: str, engine=None):
    set_schema_prefix("Graph_KG")
    translated = translate_to_sql(parse_query(cypher), None, engine=engine)
    sql = translated.sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    params = translated.parameters or []
    if len(params) == 1 and isinstance(params[0], (list, tuple)):
        params = list(params[0])
    return sql, list(params)


class TestNamedConfigKeepsTheNativeCall:
    def test_the_sixth_argument_selects_native_embedding(self):
        sql, params = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5, 'default', '*', 60, 'my-config') "
            "YIELD node, rrf_score RETURN node, rrf_score"
        )
        assert "EMBEDDING(?, ?)" in sql
        assert "TO_VECTOR(" not in sql
        # Query text first, config second — the order `EMBEDDING(text, config)` needs.
        assert params[:2] == [QUERY, "my-config"]

    def test_a_named_config_is_used_even_when_the_engine_has_one(self):
        engine = StubEngine(embedding_config="engine-config")
        sql, params = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5, 'default', '*', 60, 'call-config') "
            "YIELD node, rrf_score RETURN node, rrf_score",
            engine=engine,
        )
        assert "EMBEDDING(?, ?)" in sql
        assert params[:2] == [QUERY, "call-config"]
        assert engine.embed_calls == []


class TestEngineConfigFillsABlankArgument:
    def test_blank_sixth_argument_falls_back_to_the_engine_config(self):
        engine = StubEngine(embedding_config="engine-config")
        sql, params = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5) YIELD node, rrf_score RETURN node, rrf_score",
            engine=engine,
        )
        assert "EMBEDDING(?, ?)" in sql
        assert params[:2] == [QUERY, "engine-config"]
        assert engine.embed_calls == []

    def test_an_explicitly_blank_config_also_falls_back(self):
        engine = StubEngine(embedding_config="engine-config")
        _, params = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5, 'default', '*', 60, '') "
            "YIELD node, rrf_score RETURN node, rrf_score",
            engine=engine,
        )
        assert params[:2] == [QUERY, "engine-config"]


class TestEngineWithoutAConfigEmbedsTheQuery:
    def test_the_arm_binds_a_vector_instead_of_calling_embedding(self):
        engine = StubEngine(vector=[0.5, 0.25, 0.125])
        sql, params = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5) YIELD node, rrf_score RETURN node, rrf_score",
            engine=engine,
        )
        assert "EMBEDDING(" not in sql
        assert "TO_VECTOR(?, DOUBLE)" in sql
        # One bind, not two: the config parameter has nothing to name.
        assert params[:1] == [json.dumps([0.5, 0.25, 0.125])]
        assert engine.embed_calls == [QUERY]

    def test_the_cast_that_the_server_needs_survives(self):
        """The `-400` fix from `test_227_cte_vector_order_by.py` applies to this arm too."""
        engine = StubEngine()
        sql, _ = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5) YIELD node, rrf_score RETURN node, rrf_score",
            engine=engine,
        )
        assert "CAST(VECTOR_COSINE(e.emb, TO_VECTOR(?, DOUBLE)) AS DOUBLE) AS score" in sql

    def test_a_label_filter_binds_after_the_vector(self):
        engine = StubEngine(vector=[1.0, 2.0])
        sql, params = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5, 'default', 'Drug') "
            "YIELD node, rrf_score RETURN node, rrf_score",
            engine=engine,
        )
        assert "TO_VECTOR(?, DOUBLE)" in sql
        assert "n.label = ?" in sql
        assert params[:2] == [json.dumps([1.0, 2.0]), "Drug"]

    def test_the_query_text_still_reaches_the_bm25_arm(self):
        """The BM25 arm inlines its own copy; embedding locally must not lose it."""
        engine = StubEngine()
        sql, _ = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5) YIELD node, rrf_score RETURN node, rrf_score",
            engine=engine,
        )
        assert f"'{QUERY}'" in sql

    def test_an_engine_that_cannot_embed_says_why(self):
        engine = StubEngine(
            error=RuntimeError(
                "No embedder or embedding_config configured, and 'sentence-transformers' "
                "not installed."
            )
        )
        with pytest.raises(RuntimeError, match="embedding_config"):
            _translate(
                f"CALL ivg.retrieve('{QUERY}', 5) YIELD node, rrf_score RETURN node, rrf_score",
                engine=engine,
            )


class TestNoEngineLeavesTheNativeCallInPlace:
    def test_pure_translation_is_unchanged(self):
        """`translate_to_sql()` with no engine has nothing to ask, so it does not guess.

        This is the shape that raises `SQLCODE -280` if it is executed against a
        namespace with no `%Embedding.Config`. Every path that executes Cypher threads
        an engine (`_engine/query.py`, `cypher_api.py`, `api/routers/cypher.py`).
        """
        sql, params = _translate(
            f"CALL ivg.retrieve('{QUERY}', 5) YIELD node, rrf_score RETURN node, rrf_score"
        )
        assert "EMBEDDING(?, ?)" in sql
        assert params[:2] == [QUERY, ""]
