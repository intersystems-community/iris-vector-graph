from unittest.mock import MagicMock, patch
import json
import pytest


def make_engine_with_info(info_json: str):
    e = MagicMock()
    iris_obj = MagicMock()
    iris_obj.classMethodValue.return_value = info_json
    e._iris_obj.return_value = iris_obj
    return e


def call_info_method(engine_method, *args):
    return engine_method(*args)


@pytest.mark.parametrize("type_str,raw_info,method_name,args", [
    ("ivf",   '{"nlist":8,"dim":16,"metric":"cosine","indexed":10}', "ivf_info",   ("myidx",)),
    ("bm25",  '{"indexed":5,"k1":1.5,"b":0.75}',                    "bm25_info",  ("myidx",)),
    ("vec",   '{"name":"myidx","dim":16,"metric":"cosine"}',         "vec_info",   ("myidx",)),
    ("plaid", '{"type":"plaid","indexed":4,"nlist":3,"dim":16}',     "plaid_info", ("myidx",)),
])
def test_info_method_returns_type_key(type_str, raw_info, method_name, args):
    import iris_vector_graph.engine as eng_module
    from iris_vector_graph.engine import IRISGraphEngine

    mock_iris_obj = MagicMock()
    mock_iris_obj.classMethodValue.return_value = raw_info

    with patch.object(IRISGraphEngine, "_iris_obj", return_value=mock_iris_obj):
        engine = object.__new__(IRISGraphEngine)
        result = getattr(engine, method_name)(*args)

    assert "type" in result, f"{method_name} must return 'type' key"
    assert result["type"] == type_str, f"Expected type={type_str!r}, got {result['type']!r}"
