"""api.main builds its app at import. An engine whose first access raises (the namespace records another IVG Schema)
must leave the app up with the database unavailable and the reason logged, not fail the import: bare `pytest`
collection imports api.main, and on a host whose localhost:1972 namespace records another schema it stopped
collecting (4.6.2 gate on dpgenai1)."""

import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from iris_vector_graph.schema_name import IVGSchemaError


class _MismatchedEngine:
    @property
    def conn(self):
        raise IVGSchemaError("Namespace USER records OtherKG as its IVG Schema, and this engine asks for Graph_KG.")


def test_create_app_with_a_mismatched_schema_serves_without_the_database(caplog):
    import api.main

    with patch.object(api.main, "get_engine", return_value=_MismatchedEngine()), caplog.at_level(logging.WARNING):
        app = api.main.create_app()

    health = TestClient(app).get("/health").json()
    assert health["database"] == "unavailable"
    assert not hasattr(app.state, "engine")
    assert "records OtherKG as its IVG Schema" in caplog.text


def test_create_app_with_a_given_engine_still_raises_its_schema_error():
    import api.main

    with pytest.raises(IVGSchemaError, match="records OtherKG"):
        api.main.create_app(engine=_MismatchedEngine())
