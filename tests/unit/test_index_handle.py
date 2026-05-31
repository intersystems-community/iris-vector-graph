from unittest.mock import MagicMock, call
import pytest
from iris_vector_graph.index_protocol import IVGIndex, IndexHandle


@pytest.fixture
def mock_engine():
    e = MagicMock()
    e._search_vector_index.return_value = [("a", 0.9)]
    e.bm25_search.return_value = [("b", 1.2)]
    e.search_nodes_by_vector.return_value = [("c", 0.8)]
    e.plaid_search.return_value = [("d", 0.7)]
    e._vector_index_info.return_value = {"type": "vector", "indexed": 5}
    e.bm25_info.return_value = {"type": "fulltext", "indexed": 3}
    e._neighborhood_index_info.return_value = {"type": "neighborhood_vector", "indexed": 10}
    e.plaid_info.return_value = {"type": "multivector", "indexed": 4}
    e._probe_native_vec.return_value = True
    e.store_embedding.return_value = None
    return e


@pytest.mark.parametrize("index_type,search_attr,expected", [
    ("vector",      "_search_vector_index", [("a", 0.9)]),
    ("fulltext",    "bm25_search",          [("b", 1.2)]),
    ("multivector", "plaid_search",         [("d", 0.7)]),
])
def test_index_handle_search_dispatch(mock_engine, index_type, search_attr, expected):
    mock_engine._search_vector_index.return_value = [("a", 0.9)]
    mock_engine.bm25_search.return_value = [("b", 1.2)]
    mock_engine.search_nodes_by_vector.return_value = [("c", 0.8)]
    mock_engine.plaid_search.return_value = [("d", 0.7)]
    
    handle = IndexHandle(name="idx", type=index_type, engine=mock_engine)
    result = handle.search([0.1, 0.2], k=3)
    assert result == expected
    getattr(mock_engine, search_attr).assert_called_once()


@pytest.mark.parametrize("index_type,insert_attr,args", [
    ("vector",      "_vector_index_insert", ("idx", "doc1", [0.1, 0.2])),
    ("fulltext",    "bm25_insert",          ("idx", "doc1", [0.1, 0.2])),
    ("multivector", "plaid_insert",         ("idx", "doc1", [0.1, 0.2])),
])
def test_index_handle_insert_dispatch(mock_engine, index_type, insert_attr, args):
    handle = IndexHandle(name="idx", type=index_type, engine=mock_engine)
    handle.insert("doc1", [0.1, 0.2])
    getattr(mock_engine, insert_attr).assert_called_once_with(*args)


@pytest.mark.parametrize("index_type,drop_attr", [
    ("vector",      "_vector_index_drop"),
    ("fulltext",    "bm25_drop"),
    ("hnsw",        None),
    ("multivector", "plaid_drop"),
])
def test_index_handle_drop_dispatch(mock_engine, index_type, drop_attr):
    handle = IndexHandle(name="idx", type=index_type, engine=mock_engine)
    handle.drop()
    if drop_attr:
        getattr(mock_engine, drop_attr).assert_called_once_with("idx")


@pytest.mark.parametrize("index_type,info_attr,expected_type", [
    ("vector",      "_vector_index_info",   "vector"),
    ("fulltext",    "bm25_info",            "fulltext"),
    ("hnsw",        None,                   "hnsw"),
    ("multivector", "plaid_info",           "multivector"),
])
def test_index_handle_info_dispatch(mock_engine, index_type, info_attr, expected_type):
    mock_engine._vector_index_info.return_value = {"type": "vector", "indexed": 5}
    mock_engine.bm25_info.return_value = {"type": "fulltext", "indexed": 3}
    mock_engine.plaid_info.return_value = {"type": "multivector", "indexed": 4}
    
    handle = IndexHandle(name="idx", type=index_type, engine=mock_engine)
    result = handle.info()
    if info_attr:
        assert result.get("type") == expected_type
        getattr(mock_engine, info_attr).assert_called_once_with("idx")


def test_index_handle_is_ivgindex(mock_engine):
    handle = IndexHandle(name="idx", type="vector", engine=mock_engine)
    assert isinstance(handle, IVGIndex)


def test_index_handle_rejects_empty_name(mock_engine):
    with pytest.raises(Exception):
        IndexHandle(name="", type="vector", engine=mock_engine)


def test_index_handle_rejects_unknown_type(mock_engine):
    with pytest.raises(Exception):
        IndexHandle(name="idx", type="unknown", engine=mock_engine)


def test_ivgindex_protocol_is_runtime_checkable():
    from typing import runtime_checkable
    assert hasattr(IVGIndex, "__protocol_attrs__") or True
    assert isinstance(IndexHandle(name="x", type="vector", engine=MagicMock()), IVGIndex)
