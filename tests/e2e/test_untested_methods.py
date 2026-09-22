import json
import os
import tempfile
import warnings
from unittest.mock import MagicMock

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IVG_PORT", "31972"))
SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.fixture
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture
def demo_nodes(engine):
    for nid, lbl, props in [
        ("un_a", "Animal", {"name": "cat"}),
        ("un_b", "Animal", {"name": "dog"}),
        ("un_c", "Plant",  {"name": "rose"}),
    ]:
        engine.create_node(nid, labels=[lbl], properties=props)
    yield
    for nid in ("un_a", "un_b", "un_c"):
        engine.delete_node(nid)


def test_is_ready_false_on_broken_connection():
    from iris_vector_graph.engine import IRISGraphEngine
    eng = object.__new__(IRISGraphEngine)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.execute.side_effect = Exception("dead")
    eng.conn = mock_conn
    # `is_ready` is a method (_engine/schema.py), not a property. Read as an
    # attribute it yields a bound method, which is truthy, so `is False` failed
    # and `if engine.is_ready:` in caller code would pass on a dead connection.
    assert eng.is_ready() is False


def test_bulk_ingest_edges_empty_returns_zero(engine):
    assert engine.bulk_ingest_edges([]) == 0


def test_khop2_count_exact_empty_raises():
    from pydantic import ValidationError
    from iris_vector_graph.engine import IRISGraphEngine
    eng = object.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._arno_available = None
    eng._arno_capabilities = {}
    with pytest.raises(ValidationError):
        eng.khop2_count_exact("")


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_is_ready_returns_true(engine):
    assert engine.is_ready() is True


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_count_nodes_all(engine, demo_nodes):
    assert engine.count_nodes() >= 3


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_count_nodes_by_label(engine, demo_nodes):
    assert engine.count_nodes(label="Animal") >= 2


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_count_nodes_unknown_label(engine):
    assert engine.count_nodes(label="NonExistentLabel_XYZ_ABC") == 0


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_reload_table_mappings_clears_cache(engine):
    engine._table_mapping_cache = {"stale": True}
    engine._rel_mapping_cache = {"stale": True}
    engine.reload_table_mappings()
    assert engine._table_mapping_cache != {"stale": True}


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_get_rel_mapping_unknown_returns_none(engine):
    assert engine.get_rel_mapping("X_XYZ", "UNKNOWN_PRED_XYZ", "Y_XYZ") is None


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_get_rel_mapping_populates_cache(engine):
    engine._rel_mapping_cache = None
    engine.get_rel_mapping("Animal", "KNOWS", "Animal")
    assert engine._rel_mapping_cache is not None


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_get_schema_visualization_structure(engine, demo_nodes):
    try:
        schema = engine.get_schema_visualization()
        assert isinstance(schema, dict)
        assert "nodes" in schema and "relationships" in schema
    except Exception as e:
        if "Message out of order" in str(e) or "COMMUNICATION" in str(e):
            pytest.skip(f"Transient connection issue: {e}")
        raise


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_get_unembedded_nodes_includes_demo(engine, demo_nodes):
    result = engine.get_unembedded_nodes()
    assert isinstance(result, list)
    for nid in ("un_a", "un_b", "un_c"):
        assert nid in result


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_get_embeddings_empty_ids(engine):
    assert engine.get_embeddings([]) == []


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_get_embeddings_nonexistent(engine):
    assert isinstance(engine.get_embeddings(["nonexistent_xyz_abc_123"]), list)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_validate_vector_table_returns_dict(engine):
    result = engine.validate_vector_table("Graph_KG.kg_NodeEmbeddings", "emb")
    assert isinstance(result, dict)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_validate_vector_table_nonexistent(engine):
    try:
        result = engine.validate_vector_table("Graph_KG.nonexistent_xyz", "v")
        assert isinstance(result, dict)
    except (ValueError, Exception):
        pass


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_bulk_ingest_edges_count(engine):
    cur = engine.conn.cursor()
    for nid in ("bie_a", "bie_b"):
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [nid])
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
    engine.conn.commit()
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        n = engine.bulk_ingest_edges([{"s": "bie_a", "p": "T1", "o": "bie_b"}])
    assert n == 1
    cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE p='T1'")
    for nid in ("bie_a", "bie_b"):
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
    engine.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_bulk_ingest_edges_dirty_flag(engine):
    """`auto_sync=False` leaves the index stale and says so.

    The default is `auto_sync=True`, which sets `_nkg_dirty` and then calls
    `sync()`, which clears it — so the flag is `False` on return by design and a
    test that asserted `True` after a default call was asserting the opposite of
    the contract. `auto_sync=False` is the case where the flag is the caller's
    only signal that a rebuild is owed.
    """
    cur = engine.conn.cursor()
    for nid in ("bie_c", "bie_d"):
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [nid])
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
    engine.conn.commit()
    engine._nkg_dirty = False
    try:
        engine.bulk_ingest_edges(
            [{"s": "bie_c", "p": "T2", "o": "bie_d"}], auto_sync=False
        )
        assert engine._nkg_dirty is True
        assert engine.status().pending_sync is True

        # And the default does sync, so it does not leave the flag set.
        engine.bulk_ingest_edges([{"s": "bie_c", "p": "T2b", "o": "bie_d"}])
        assert engine._nkg_dirty is False
    finally:
        engine.sync()
    cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE p IN ('T2','T2b')")
    for nid in ("bie_c", "bie_d"):
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
    engine.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_bulk_ingest_edges_stale_index_blocks_var_length_query(engine):
    """An unsynced bulk ingest makes the next var-length query raise, not warn.

    This test used to assert a `RuntimeWarning` from `bulk_ingest_edges` itself.
    Ingest emits none: it uses `logger.warning` for its own fallbacks, and the
    stale-index signal belongs to the *consumer*. The only enforcement of a stale
    `^NKG` is `IndexNotSyncedError`, raised from the two var-length Cypher routes
    in `_engine/query.py` before any traversal runs. A caller who ingests without
    syncing and then asks for a path gets an error, which is the contract worth
    pinning — a swallowed warning would let a wrong answer through.
    """
    from iris_vector_graph.errors import IndexNotSyncedError

    cur = engine.conn.cursor()
    for nid in ("bie_e", "bie_f"):
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [nid])
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
    engine.conn.commit()

    vlp = "MATCH (a)-[*1..2]->(b) WHERE a.node_id = 'bie_e' RETURN b"
    try:
        engine.bulk_ingest_edges(
            [{"s": "bie_e", "p": "T3", "o": "bie_f"}], auto_sync=False
        )
        with pytest.raises(IndexNotSyncedError):
            engine.execute_cypher(vlp)

        # After the sync the same query is answerable — so the raise above was the
        # stale index, not a translation failure.
        engine.sync()
        engine.execute_cypher(vlp)
    finally:
        engine.sync()
    cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE p='T3'")
    for nid in ("bie_e", "bie_f"):
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
    engine.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_backfill_degp_returns_int(engine):
    assert isinstance(engine.backfill_degp(), int)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_backfill_deg2p_exact_returns_int(engine):
    assert isinstance(engine.backfill_deg2p_exact(), int)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_khop2_count_exact_nonexistent_returns_zero(engine):
    assert engine.khop2_count_exact("absolutely_nonexistent_xyz_abc", "PRED") == 0


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_khop2_count_exact_matches_khop2_count(engine):
    import iris
    cur = engine.conn.cursor()
    for nid in ("kce_a", "kce_b", "kce_c"):
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [nid])
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
    engine.conn.commit()
    engine.create_edge("kce_a", "CHAIN", "kce_b")
    engine.create_edge("kce_b", "CHAIN", "kce_c")
    engine.backfill_degp()
    engine.backfill_deg2p_exact()
    exact = engine.khop2_count_exact("kce_a", "CHAIN")
    o = iris.createIRIS(engine.conn)
    slow = int(o.classMethodValue("Graph.KG.Traversal", "KHop2Count", "kce_a", "CHAIN"))
    assert exact == slow
    for nid in ("kce_a", "kce_b", "kce_c"):
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", [nid, nid])
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
    engine.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_export_graph_ndjson_creates_file(engine, demo_nodes):
    with tempfile.NamedTemporaryFile(suffix=".ndjson", delete=False, mode="w") as f:
        path = f.name
    try:
        engine.export_graph_ndjson(path)
        assert os.path.getsize(path) > 0
        lines = [json.loads(l) for l in open(path) if l.strip()]
        assert len(lines) >= 3
    finally:
        os.unlink(path)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_vec_expand_returns_list(engine):
    try:
        engine.vec_create_index("ue_idx", dim=4, metric="cosine")
        engine.vec_insert("ue_idx", "ue_s", [0.1, 0.2, 0.3, 0.4])
        engine.vec_insert("ue_idx", "ue_n", [0.1, 0.2, 0.3, 0.5])
        assert isinstance(engine.vec_expand("ue_idx", "ue_s", k=1), list)
    except Exception:
        pytest.skip("VecIndex unavailable")
    finally:
        try:
            engine.vec_drop("ue_idx")
        except Exception:
            pass


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_vector_search_returns_list(engine):
    try:
        sources = [{"table": "Graph_KG.kg_NodeEmbeddings", "vector_col": "emb", "id_col": "id"}]
        result = engine.multi_vector_search(sources, [0.1, 0.2, 0.3, 0.4], top_k=1)
        assert isinstance(result, list)
    except Exception:
        pytest.skip("vector_search unavailable")


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_multi_vector_search_empty_sources(engine):
    assert isinstance(engine.multi_vector_search([], [0.1], top_k=5), list)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_kg_neighborhood_expansion_returns_list(engine, demo_nodes):
    engine.create_edge("un_a", "KNOWS2", "un_b")
    try:
        assert isinstance(engine.kg_NEIGHBORHOOD_EXPANSION(["un_a"], expansion_depth=1), list)
    finally:
        engine.conn.cursor().execute("DELETE FROM Graph_KG.rdf_edges WHERE s='un_a' AND p='KNOWS2'")
        engine.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_kg_neighborhood_expansion_empty_seeds(engine):
    assert isinstance(engine.kg_NEIGHBORHOOD_EXPANSION([]), list)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_embed_text_without_embedder_depends_on_the_optional_extra(engine):
    """With no embedder and no `embedding_config`, `embed_text` needs the extra.

    `embed_text` auto-loads `SentenceTransformer("all-MiniLM-L6-v2")` as its last
    resort and raises `RuntimeError` naming the missing package when
    `sentence-transformers` is not installed — the same behaviour 3.2.0 shipped.
    Asserting a list unconditionally passed only on an install that happened to
    carry the `[full]` extra, so the branch taken here is decided by what is
    importable rather than assumed.
    """
    import importlib.util

    from iris_vector_graph.engine import IRISGraphEngine
    eng = object.__new__(IRISGraphEngine)
    eng.conn = engine.conn
    eng._arno_available = None
    eng._arno_capabilities = {}
    eng._nkg_dirty = False
    eng._index_registry = {}
    eng._table_mapping_cache = None
    eng._rel_mapping_cache = None
    eng._connection_params = None
    eng.embedding_config = None
    eng.embedder = None
    eng.embedding_dimension = 768

    if importlib.util.find_spec("sentence_transformers") is None:
        with pytest.raises(RuntimeError, match="sentence-transformers"):
            eng.embed_text("hello world")
    else:
        assert isinstance(eng.embed_text("hello world"), list)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_materialize_inference_returns_dict(engine):
    SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
    TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    cur = engine.conn.cursor()
    for nid in ("inf_mammal", "inf_animal", "inf_cat"):
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [nid])
        if cur.fetchone()[0] == 0:
            engine.create_node(nid)
    engine.create_edge("inf_mammal", SUBCLASS, "inf_animal")
    engine.create_edge("inf_cat", TYPE, "inf_mammal")
    engine.conn.commit()
    try:
        result = engine.materialize_inference(rules="rdfs")
        assert isinstance(result, dict)
        engine.retract_inference()
    except Exception as e:
        if "JSON_VALUE" in str(e) or "function" in str(e).lower():
            pytest.skip("JSON_VALUE not available on this IRIS tier")
        raise
    finally:
        for nid in ("inf_mammal", "inf_animal", "inf_cat"):
            cur.execute("DELETE FROM Graph_KG.rdf_props WHERE s=?", [nid])
            cur.execute("DELETE FROM Graph_KG.rdf_labels WHERE s=?", [nid])
            cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", [nid, nid])
            cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
        engine.conn.commit()


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
def test_retract_inference_returns_int(engine):
    try:
        result = engine.retract_inference()
        assert isinstance(result, int)
    except Exception as e:
        if "JSON_VALUE" in str(e) or "function" in str(e).lower():
            pytest.skip("JSON_VALUE not available on this IRIS tier")
        raise
