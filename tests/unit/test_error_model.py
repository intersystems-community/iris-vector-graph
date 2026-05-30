from iris_vector_graph import (
    IVGError,
    PrerequisiteError,
    IndexNotFoundError,
    IndexNotBuiltError,
    EmbeddingsMissingError,
    IndexNotSyncedError,
    NodeNotFoundError,
)

import pytest

_PREREQ = [
    IndexNotFoundError,
    IndexNotBuiltError,
    EmbeddingsMissingError,
    IndexNotSyncedError,
]
_ALL = _PREREQ + [PrerequisiteError, NodeNotFoundError]


@pytest.mark.parametrize("cls", _ALL)
def test_all_descend_from_single_root(cls):
    assert issubclass(cls, IVGError)


@pytest.mark.parametrize("cls", _PREREQ)
def test_prerequisite_errors_descend_from_prerequisite_error(cls):
    assert issubclass(cls, PrerequisiteError)


def test_index_not_found_lists_known_and_names_create_verb():
    e = IndexNotFoundError("genes", known=["a", "b"])
    msg = str(e)
    assert "genes" in msg
    assert "a" in msg and "b" in msg
    assert "create_index" in msg
    assert e.name == "genes"


def test_index_not_found_handles_empty_registry():
    e = IndexNotFoundError("genes", known=[])
    assert "no indexes" in str(e).lower()


def test_index_not_built_reports_rows_and_remedy():
    e = IndexNotBuiltError("docs", rows=0)
    assert e.rows == 0
    assert e.name == "docs"
    assert "build()" in e.remedy
    assert "build()" in str(e)


def test_embeddings_missing_picks_remedy_by_scope():
    assert "embed_nodes" in EmbeddingsMissingError(scope="nodes").remedy
    assert "embed_edges" in EmbeddingsMissingError(scope="edges").remedy


def test_index_not_synced_names_sync_and_pending():
    e = IndexNotSyncedError(pending=12)
    assert e.pending == 12
    assert "12" in str(e)
    assert "sync()" in e.remedy


def test_node_not_found_carries_id():
    e = NodeNotFoundError("Gene::7157")
    assert e.node_id == "Gene::7157"
    assert "Gene::7157" in str(e)


@pytest.mark.parametrize("cls,kwargs", [
    (IndexNotFoundError, {"name": "x"}),
    (IndexNotBuiltError, {"name": "x"}),
    (EmbeddingsMissingError, {}),
    (IndexNotSyncedError, {}),
])
def test_every_prerequisite_error_has_nonempty_remedy(cls, kwargs):
    e = cls(**kwargs)
    assert e.remedy and e.remedy.strip()
