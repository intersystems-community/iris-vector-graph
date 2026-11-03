"""Spec 262 T007: the schema record in ^IVG.Deploy("schema"), and the error a namespace's record raises (FR-005,
FR-006, FR-006a; contracts/python-api.md)."""

import pytest

import iris_vector_graph
from iris_vector_graph.schema_name import IVGSchemaError, read_record, write_record


class RecordingNative:
    """The two Native API calls the record uses, over a dict; nothing else exists on it."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.sets = []

    def get(self, gname, *subs):
        return self.data.get((gname, *subs))

    def set(self, value, gname, *subs):
        self.sets.append((value, gname, *subs))
        self.data[(gname, *subs)] = value


def test_no_record_reads_as_none():
    assert read_record(RecordingNative()) is None


def test_an_empty_record_reads_as_none():
    assert read_record(RecordingNative({("^IVG.Deploy", "schema"): ""})) is None


def test_a_record_reads_back_as_written():
    native = RecordingNative({("^IVG.Deploy", "schema"): "IVGALT"})
    assert read_record(native) == "IVGALT"


def test_write_record_sets_the_name_and_when_and_by_which_version():
    native = RecordingNative()
    write_record(native, "IVGALT", version="4.6.0")
    assert native.data[("^IVG.Deploy", "schema")] == "IVGALT"
    recorded = native.data[("^IVG.Deploy", "schema", "recorded")]
    assert "4.6.0" in recorded
    assert recorded[:4].isdigit()  # a $ZDateTime(,3)-style timestamp first
    assert [s[1:] for s in native.sets] == [("^IVG.Deploy", "schema"), ("^IVG.Deploy", "schema", "recorded")]


def test_the_error_is_a_runtime_error_exported_from_the_package():
    assert issubclass(IVGSchemaError, RuntimeError)
    assert iris_vector_graph.IVGSchemaError is IVGSchemaError


def test_the_not_upgraded_message():
    text = str(IVGSchemaError.not_upgraded("IVG262UPG"))
    assert "set up by an earlier IVG release" in text
    assert "initialize_schema()" in text
    assert "initialize_schema(dry_run=True)" in text
    assert "IVG262UPG" in text


def test_the_mismatch_message_names_both_schemas():
    err = IVGSchemaError.mismatch(recorded="Graph_KG", requested="RAG", namespace="USER")
    text = str(err)
    for part in ("Graph_KG", "RAG", "one IVG Schema per namespace", "use a separate namespace"):
        assert part in text
    assert err.recorded == "Graph_KG" and err.requested == "RAG"


@pytest.mark.parametrize("recorded, requested, same", [("ivgalt", "IVGALT", True), ("IVGALT", "IVGALT2", False)])
def test_names_compare_without_case(recorded, requested, same):
    from iris_vector_graph.schema_name import same_schema

    assert same_schema(recorded, requested) is same
