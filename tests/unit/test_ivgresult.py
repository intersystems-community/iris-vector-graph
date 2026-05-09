import pytest
from iris_vector_graph.result import IVGResult
from iris_vector_graph.cypher.translator import QueryMetadata


def make_success(**kwargs):
    return IVGResult(columns=["id", "name"], rows=[("n1", "Alice"), ("n2", "Bob")], **kwargs)


def make_error():
    return IVGResult(columns=[], rows=[], error="<SQL ERROR> bad syntax")


def make_empty():
    return IVGResult(columns=["id"], rows=[])


def test_success_result_has_columns_rows():
    r = make_success()
    assert r.columns == ["id", "name"]
    assert r.rows == [("n1", "Alice"), ("n2", "Bob")]


def test_error_result_is_falsy():
    r = make_error()
    assert not r
    assert bool(r) is False


def test_success_result_is_truthy():
    r = make_success()
    assert r
    assert bool(r) is True


def test_empty_result_still_truthy():
    r = make_empty()
    assert r
    assert bool(r) is True


def test_dict_compat_getitem_columns():
    r = make_success()
    assert r["columns"] == ["id", "name"]
    assert r["rows"] == [("n1", "Alice"), ("n2", "Bob")]


def test_dict_compat_getitem_sql():
    r = IVGResult(columns=["id"], rows=[], sql="SELECT id FROM nodes")
    assert r["sql"] == "SELECT id FROM nodes"


def test_dict_compat_getitem_raises_on_absent_sql():
    r = make_empty()
    with pytest.raises(KeyError):
        _ = r["sql"]


def test_dict_compat_getitem_raises_on_absent_error():
    r = make_success()
    with pytest.raises(KeyError):
        _ = r["error"]


def test_dict_compat_getitem_error_present():
    r = make_error()
    assert r["error"] == "<SQL ERROR> bad syntax"


def test_dict_compat_get_returns_none_for_absent_sql():
    r = make_empty()
    assert r.get("sql") is None


def test_dict_compat_get_returns_none_for_absent_error():
    r = make_success()
    assert r.get("error") is None


def test_dict_compat_get_returns_value_for_present_key():
    r = make_success()
    assert r.get("columns") == ["id", "name"]


def test_dict_compat_get_with_default():
    r = make_empty()
    assert r.get("nonexistent", "fallback") == "fallback"


def test_dict_compat_contains_error_false_on_success():
    r = make_success()
    assert "error" not in r


def test_dict_compat_contains_error_true_on_error():
    r = make_error()
    assert "error" in r


def test_dict_compat_contains_structural_keys():
    r = make_success()
    assert "columns" in r
    assert "rows" in r
    assert "metadata" in r


def test_dict_compat_contains_absent_optional_keys():
    r = make_empty()
    assert "sql" not in r
    assert "params" not in r


def test_missing_key_raises_keyerror():
    r = make_success()
    with pytest.raises(KeyError):
        _ = r["nonexistent_key"]


def test_metadata_always_present():
    r = make_empty()
    assert r.metadata is not None
    assert isinstance(r.metadata, QueryMetadata)


def test_metadata_default_is_empty_querymetadata():
    r = make_empty()
    assert r.metadata.warnings == []
    assert r.metadata.estimated_rows is None


def test_isinstance_check():
    r = make_success()
    assert isinstance(r, IVGResult)


def test_error_none_by_default():
    r = IVGResult(columns=[], rows=[])
    assert r.error is None


def test_sql_params_optional():
    r = IVGResult(columns=["x"], rows=[(1,)], sql="SELECT x", params=["a"])
    assert r.sql == "SELECT x"
    assert r.params == ["a"]
    assert r["sql"] == "SELECT x"
    assert r["params"] == ["a"]