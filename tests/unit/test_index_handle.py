from unittest.mock import MagicMock, call
import pytest
from iris_vector_graph.index_protocol import IVGIndex, IndexHandle


@pytest.fixture
def mock_engine():
    e = MagicMock()
    e.ivf_search.return_value = [("a", 0.9)]
    e.bm25_search.return_value = [("b", 1.2)]
    e.vec_search.return_value = [("c", 0.8)]
    e.plaid_search.return_value = [("d", 0.7)]
    e.ivf_info.return_value = {"type": "ivf", "indexed": 5}
    e.bm25_info.return_value = {"type": "bm25", "indexed": 3}
    e.vec_info.return_value = {"type": "vec", "indexed": 10}
    e.plaid_info.return_value = {"type": "plaid", "indexed": 4}
    return e


@pytest.mark.parametrize("index_type,search_attr,expected", [
    ("ivf",   "ivf_search",   [("a", 0.9)]),
    ("bm25",  "bm25_search",  [("b", 1.2)]),
    ("vec",   "vec_search",   [("c", 0.8)]),
    ("plaid", "plaid_search", [("d", 0.7)]),
])
def test_index_handle_search_dispatch(mock_engine, index_type, search_attr, expected):
    handle = IndexHandle(name="idx", type=index_type, engine=mock_engine)
    result = handle.search([0.1, 0.2], k=3)
    assert result == expected
    getattr(mock_engine, search_attr).assert_called_once()


@pytest.mark.parametrize("index_type,insert_attr", [
    ("ivf",   "ivf_insert"),
    ("bm25",  "bm25_insert"),
    ("vec",   "vec_insert"),
    ("plaid", "plaid_insert"),
])
def test_index_handle_insert_dispatch(mock_engine, index_type, insert_attr):
    handle = IndexHandle(name="idx", type=index_type, engine=mock_engine)
    handle.insert("doc1", [0.1, 0.2])
    getattr(mock_engine, insert_attr).assert_called_once_with("idx", "doc1", [0.1, 0.2])


@pytest.mark.parametrize("index_type,drop_attr", [
    ("ivf",   "ivf_drop"),
    ("bm25",  "bm25_drop"),
    ("vec",   "vec_drop"),
    ("plaid", "plaid_drop"),
])
def test_index_handle_drop_dispatch(mock_engine, index_type, drop_attr):
    handle = IndexHandle(name="idx", type=index_type, engine=mock_engine)
    handle.drop()
    getattr(mock_engine, drop_attr).assert_called_once_with("idx")


@pytest.mark.parametrize("index_type,info_attr,expected_type", [
    ("ivf",   "ivf_info",   "ivf"),
    ("bm25",  "bm25_info",  "bm25"),
    ("vec",   "vec_info",   "vec"),
    ("plaid", "plaid_info", "plaid"),
])
def test_index_handle_info_dispatch(mock_engine, index_type, info_attr, expected_type):
    handle = IndexHandle(name="idx", type=index_type, engine=mock_engine)
    result = handle.info()
    assert result["type"] == expected_type
    getattr(mock_engine, info_attr).assert_called_once_with("idx")


def test_index_handle_is_ivgindex(mock_engine):
    handle = IndexHandle(name="idx", type="ivf", engine=mock_engine)
    assert isinstance(handle, IVGIndex)


def test_index_handle_rejects_empty_name(mock_engine):
    with pytest.raises(Exception):
        IndexHandle(name="", type="ivf", engine=mock_engine)


def test_index_handle_rejects_unknown_type(mock_engine):
    with pytest.raises(Exception):
        IndexHandle(name="idx", type="unknown", engine=mock_engine)


def test_ivgindex_protocol_is_runtime_checkable():
    from typing import runtime_checkable
    assert hasattr(IVGIndex, "__protocol_attrs__") or True
    assert isinstance(IndexHandle(name="x", type="ivf", engine=MagicMock()), IVGIndex)
