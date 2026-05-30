import pytest
from pydantic import ValidationError, TypeAdapter

from iris_vector_graph.index_config import (
    IndexConfig,
    VectorIndexConfig,
    FulltextIndexConfig,
    MultiVectorIndexConfig,
    NeighborhoodVectorConfig,
    HNSWIndexConfig,
)
from iris_vector_graph.index_protocol import Index, IndexHandle, _rows_of

_ADAPTER = TypeAdapter(IndexConfig)


def test_indexhandle_is_alias_of_index():
    assert IndexHandle is Index


@pytest.mark.parametrize("payload,expected", [
    ({"name": "v", "type": "vector"}, VectorIndexConfig),
    ({"name": "f", "type": "fulltext", "properties": ["name"]}, FulltextIndexConfig),
    ({"name": "m", "type": "multivector"}, MultiVectorIndexConfig),
    ({"name": "n", "type": "neighborhood_vector"}, NeighborhoodVectorConfig),
    ({"name": "h", "type": "hnsw"}, HNSWIndexConfig),
])
def test_discriminated_union_routes_by_type(payload, expected):
    assert isinstance(_ADAPTER.validate_python(payload), expected)


@pytest.mark.parametrize("payload", [
    {"name": "v", "type": "vector", "metric": "manhattan"},
    {"name": "v", "type": "vector", "nlist": 0},
    {"name": "f", "type": "fulltext", "properties": []},
    {"name": "", "type": "vector"},
    {"name": "v", "type": "vector", "unknown_field": 1},
    {"name": "f", "type": "fulltext", "b": 2.0},
])
def test_invalid_config_rejected(payload):
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload)


def test_defaults_are_sane():
    v = VectorIndexConfig(name="v")
    assert v.method == "ivf" and v.metric == "cosine" and v.nlist == 256
    n = NeighborhoodVectorConfig(name="n")
    assert n.buckets == 256 and n.max_buckets == 32


@pytest.mark.parametrize("info,expected", [
    ({"num_vectors": 42}, 42),
    ({"count": 7}, 7),
    ({"rows": 3}, 3),
    ({"indexed": 11}, 11),
    ({"n_docs": 9}, 9),
    ({}, 0),
    ({"size": "bad"}, 0),
])
def test_rows_of_extracts_count(info, expected):
    assert _rows_of(info) == expected
