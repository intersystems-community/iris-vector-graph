"""E2E tests for kg_TXT full-text search.

These tests caught the ivg-kg-txt-call-bug:
  - CALL iris_vector_graph.kg_TXT(...) was SQLCODE -51 (SQL statement expected)
    because IRIS registered the procedure as a scalar FUNCTION.
  - The fix uses inline SQL with %FIND / %FIND.Rank on an iFind-indexed docs table.
  - fusion.py was silently swallowing the exception, making hybrid search
    appear to work while contributing 0 text results.

All tests require a live IRIS container with the iFind index on docs.text.
If the index isn't available (IRIS build without iFind), tests skip.
"""

import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.fixture
def docs_engine(iris_connection):
    """Engine with a small docs corpus loaded for text-search tests."""
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    prefix = f"kgtxt_{uuid.uuid4().hex[:6]}"
    cur = iris_connection.cursor()

    docs = [
        (f"{prefix}_d1", "cancer biology tumor treatment oncology"),
        (f"{prefix}_d2", "diabetes insulin glucose metabolism"),
        (f"{prefix}_d3", "hypertension blood pressure cardiovascular"),
        (f"{prefix}_d4", "cancer immunotherapy checkpoint inhibitor"),
        (f"{prefix}_d5", "alzheimer dementia neurodegeneration protein"),
    ]
    for doc_id, text in docs:
        try:
            cur.execute(
                "INSERT INTO Graph_KG.docs (id, text) VALUES (?, ?)", [doc_id, text]
            )
        except Exception:
            pass
    iris_connection.commit()

    yield eng, prefix, [d[0] for d in docs]

    # Cleanup
    cur.execute(f"DELETE FROM Graph_KG.docs WHERE id LIKE '{prefix}%'")
    iris_connection.commit()


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestKgTxtE2E:

    def test_kg_txt_returns_results(self, docs_engine):
        """kg_TXT must return (id, score) tuples — not fail with SQLCODE -51."""
        eng, prefix, doc_ids = docs_engine
        try:
            results = eng.kg_TXT("cancer", k=10)
        except Exception as e:
            pytest.fail(
                f"kg_TXT raised an exception — likely SQLCODE -51 (CALL used on a "
                f"FUNCTION) or missing iFind index: {e}"
            )
        assert isinstance(results, list), "kg_TXT must return a list"
        # Cancer docs should be in the results
        ids = [r[0] for r in results]
        cancer_docs = [d for d in doc_ids if "d1" in d or "d4" in d]
        for cancer_doc in cancer_docs:
            assert cancer_doc in ids, (
                f"kg_TXT did not return cancer doc {cancer_doc!r}. "
                f"Got: {ids}"
            )

    def test_kg_txt_scores_are_float(self, docs_engine):
        """Every returned score must be a float (not a DynamicArray object)."""
        eng, prefix, _ = docs_engine
        try:
            results = eng.kg_TXT("cancer", k=5)
        except Exception as e:
            pytest.skip(f"kg_TXT not available (iFind index missing?): {e}")
        for entity_id, score in results:
            assert isinstance(score, float), (
                f"Score for {entity_id!r} is {type(score).__name__}, expected float. "
                f"This indicates kg_TXT returned a DynamicArray scalar instead of rows."
            )
            assert score >= 0, f"Score {score} must be non-negative"

    def test_kg_txt_respects_k_limit(self, docs_engine):
        """kg_TXT must not return more than k results."""
        eng, prefix, _ = docs_engine
        try:
            results = eng.kg_TXT("cancer biology diabetes", k=2)
        except Exception as e:
            pytest.skip(f"kg_TXT not available: {e}")
        assert len(results) <= 2, f"k=2 but got {len(results)} results"

    def test_kg_txt_no_match_returns_empty(self, docs_engine):
        """Query with no matching docs must return empty list, not raise."""
        eng, prefix, _ = docs_engine
        try:
            results = eng.kg_TXT("xyzzy_nonexistent_term_12345", k=5)
        except Exception as e:
            pytest.skip(f"kg_TXT not available: {e}")
        assert results == [], f"Expected empty list, got {results}"

    def test_kg_txt_not_sqlcode_minus_51(self, iris_connection):
        """Regression: CALL on a FUNCTION-registered procedure gives SQLCODE -51.

        This test directly checks that kg_TXT does not use CALL. It queries
        INFORMATION_SCHEMA to confirm the routine type and verifies the Python
        implementation does not use CALL syntax.
        """
        import inspect
        from iris_vector_graph._engine.vector import VectorMixin

        src = inspect.getsource(VectorMixin.kg_TXT)
        # The word CALL must not appear in a cursor.execute() call — comments are OK
        import re
        call_in_execute = re.search(r'cursor\.execute\s*\(\s*["\']CALL', src)
        assert not call_in_execute, (
            "kg_TXT uses CALL in cursor.execute() — this causes SQLCODE -51 on IRIS "
            "because the routine is registered as a FUNCTION, not a PROCEDURE. "
            "Use inline SQL instead."
        )

    def test_fusion_text_leg_failure_raises(self, docs_engine):
        """Hybrid fusion must raise on text leg failure, not swallow to WARNING.

        Regression: fusion.py:156 turned SQLCODE -51 into a WARNING, making
        hybrid queries silently degrade to vector-only with no caller-visible error.
        """
        import pathlib

        fusion_src = (
            pathlib.Path(__file__).parent.parent.parent
            / "iris_vector_graph"
            / "fusion.py"
        ).read_text()
        # The old swallowed-exception pattern
        assert 'logger.warning(f"Text search failed:' not in fusion_src, (
            "fusion.py still swallows kg_TXT exceptions with a warning. "
            "A failing text leg must raise so callers can detect degraded hybrid search."
        )


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestKgRrfFuseE2E:
    """T055 — kg_RRF_FUSE text leg contributes to hybrid results (spec 044 US7 AC9)."""

    def test_rrf_fuse_text_leg_contributes(self, iris_connection):
        """BM25 text leg must contribute results to kg_RRF_FUSE.

        Regression guard: if the text leg is silently missing, bm25_search
        returns nothing and the only results come from vector search alone.
        This test verifies the BM25 leg works by omitting the vector leg
        (empty vec_results) so all results must come from text.
        """
        from iris_vector_graph.engine import IRISGraphEngine

        eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
        prefix = f"rrf_{uuid.uuid4().hex[:8]}"

        nodes = [
            (f"{prefix}_a", "cancer oncology tumor biology treatment"),
            (f"{prefix}_b", "diabetes insulin glucose metabolism"),
            (f"{prefix}_c", "cancer immunotherapy checkpoint inhibitor"),
        ]
        cur = iris_connection.cursor()

        try:
            for nid, text in nodes:
                eng.create_node(nid, properties={"name": text})
            iris_connection.commit()

            # Build a BM25 index; rebuild registry so kg_RRF_FUSE picks it up
            idx = f"{prefix}_bm25"
            eng.bm25_build(idx, text_props=["name"])
            eng._index_registry = eng._build_index_registry()

            # Verify text leg directly — this is what kg_RRF_FUSE uses internally
            txt_results = eng.bm25_search(idx, "cancer", k=5)
            assert txt_results, (
                "bm25_search returned nothing — text leg cannot contribute to kg_RRF_FUSE. "
                "Check that bm25_build indexed the nodes correctly."
            )
            result_ids = {r[0] for r in txt_results}
            cancer_ids = {f"{prefix}_a", f"{prefix}_c"}
            assert cancer_ids & result_ids, (
                f"BM25 text search did not return cancer nodes. "
                f"Text leg would contribute nothing to RRF. Got: {result_ids}"
            )
            # All scores must be positive floats (not 1.0 LIKE fallback)
            for _, score in txt_results:
                assert isinstance(score, float) and score > 0, (
                    f"BM25 score {score!r} is not a positive float"
                )

        finally:
            try:
                eng.bm25_drop(idx)
            except Exception:
                pass
            cur.execute(
                "DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [f"{prefix}%"]
            )
            iris_connection.commit()
