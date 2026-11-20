"""Number normalization preserves JSON strings in search results."""

import json

import pytest

from iris_vector_graph.stores.iris_sql_store import _fix_iris_json


@pytest.mark.parametrize("node_id", ["part:.5", "rxd,.7y", "rxd[.9z", 'q":.5', "a\\\\,.5"])
def test_an_id_that_looks_like_a_number_comes_back_as_stored(node_id):
    raw = '[{"id":' + json.dumps(node_id) + ',"score":.5}]'
    assert json.loads(_fix_iris_json(raw)) == [{"id": node_id, "score": 0.5}]


def test_numbers_still_get_their_leading_zero():
    raw = '{"results":[{"id":"a","score":.25},{"id":"b","score":-.5}],"x":[.1, .2],"skipped":[]}'
    assert json.loads(_fix_iris_json(raw)) == {
        "results": [{"id": "a", "score": 0.25}, {"id": "b", "score": -0.5}], "x": [0.1, 0.2], "skipped": []}


def test_well_formed_json_is_unchanged():
    raw = '[{"id":"n:0.5","score":0.75,"t":"[.9"}]'
    assert _fix_iris_json(raw) == raw
