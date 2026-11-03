"""Spec 262 T024: IVG reads its own variable, and only its own (US3 scenarios 3 and 4, FR-003)."""

from unittest.mock import MagicMock, patch

from iris_vector_graph.engine import IRISGraphEngine


def _engine(monkeypatch, env, **kwargs):
    for name in ("IVG_SCHEMA_PREFIX", "IRIS_SCHEMA_PREFIX"):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
         patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"), \
         patch("iris_vector_graph.stores.iris_sql_store.IRISGraphStore", MagicMock()):
        return IRISGraphEngine(MagicMock(), **kwargs)


def test_the_argument_wins_over_ivg_schema_prefix(monkeypatch):
    assert _engine(monkeypatch, {"IVG_SCHEMA_PREFIX": "ENVNAME"}, schema_prefix="ARGNAME")._schema_prefix == "ARGNAME"


def test_iris_vector_rags_variable_does_not_move_ivg(monkeypatch):
    assert _engine(monkeypatch, {"IRIS_SCHEMA_PREFIX": "RAG"})._schema_prefix == "Graph_KG"


def test_both_set_ivg_uses_its_own(monkeypatch):
    engine = _engine(monkeypatch, {"IRIS_SCHEMA_PREFIX": "RAG", "IVG_SCHEMA_PREFIX": "IVGALT"})
    assert engine._schema_prefix == "IVGALT"
