"""E2E tests for embedding identity enforcement (spec 226, US2).

Live against `ivg-iris-enterprise` (port 31972). The decisive case — two models at the
**same width** — is unmockable and invisible to IRIS: the INSERT succeeds, the distances
compute, and the rankings are meaningless. Only the registry can refuse it.

The declared width is not a constant in this suite (see
`test_embedding_registry_e2e.py`'s module docstring), so every width here is read at test
time and every "wrong width" is derived from it.
"""

import contextlib
import os
import uuid

import pytest

from iris_vector_graph.embedding_identity import identity_from_config
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from tests.integration.test_embedding_registry_e2e import (
    _clear_registry,
    _live_dimension,
    _registry_rows,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_NODE_TABLE = "kg_NodeEmbeddings"


def _vector(width: int, seed: float = 0.1):
    return [seed] * width


def _embedding_row_count(conn, node_id: str) -> int:
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{_NODE_TABLE} WHERE id = ?", [node_id])
        return int(cur.fetchone()[0])
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _delete_embedding(conn, node_id: str) -> None:
    cur = conn.cursor()
    try:
        with contextlib.suppress(Exception):
            cur.execute(f"DELETE FROM Graph_KG.{_NODE_TABLE} WHERE id = ?", [node_id])
        conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cur.close()


@pytest.fixture
def width(iris_connection):
    """The live declared width of `kg_NodeEmbeddings.emb`, read now."""
    dim = _live_dimension(iris_connection)
    assert dim, "kg_NodeEmbeddings.emb has no declared width — cannot test enforcement"
    return dim


@pytest.fixture
def clean_registry(iris_connection):
    _clear_registry(iris_connection)
    yield
    _clear_registry(iris_connection)


def _engine(conn, *, dim, model=None):
    return IRISGraphEngine(conn, embedding_dimension=dim, embedding_config=model)


# ------------------------------------------------------------------------------- T015


class TestSameWidthDifferentModel:
    def test_same_width_different_model_refused(self, iris_connection, width, clean_registry):
        """T015 / FR-010: identical widths, different models — the case IRIS cannot see."""
        node_a = f"ivg226-a-{uuid.uuid4().hex[:8]}"
        node_b = f"ivg226-b-{uuid.uuid4().hex[:8]}"

        engine_a = _engine(iris_connection, dim=width, model="model-alpha")
        engine_a.create_node(node_a)
        engine_a.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )
        assert engine_a.store_embedding(node_a, _vector(width)) is True

        engine_b = _engine(iris_connection, dim=width, model="model-beta")
        engine_b.create_node(node_b)

        # The point of this test: both writers are at the same width.
        recorded = engine_b.get_embedding_identity(_NODE_TABLE)
        offered_width = width
        assert recorded is not None
        assert (
            recorded.dimension == offered_width
        ), "this is not a width test — the recorded and offered widths must be equal"

        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.store_embedding(node_b, _vector(offered_width, seed=0.2))

        assert (
            _embedding_row_count(iris_connection, node_b) == 0
        ), "the refused write left a vector behind"
        assert _embedding_row_count(iris_connection, node_a) == 1

        _delete_embedding(iris_connection, node_a)


# ------------------------------------------------------------------------------- T016


class TestBatchIsAllOrNothing:
    def test_conflicting_batch_writes_nothing(self, iris_connection, width, clean_registry):
        """T016: `store_embeddings` refuses before the first INSERT, not mid-loop."""
        nodes = [f"ivg226-batch-{i}-{uuid.uuid4().hex[:6]}" for i in range(3)]

        engine_a = _engine(iris_connection, dim=width, model="model-alpha")
        for nid in nodes:
            engine_a.create_node(nid)
        engine_a.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        engine_b = _engine(iris_connection, dim=width, model="model-beta")
        items = [{"node_id": nid, "embedding": _vector(width, seed=0.3)} for nid in nodes]
        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.store_embeddings(items)

        for nid in nodes:
            assert (
                _embedding_row_count(iris_connection, nid) == 0
            ), f"{nid} was written by a refused batch — the refusal is not all-or-nothing"


# ------------------------------------------------------------------------------- T017


class TestRefusalMessage:
    def test_model_conflict_message_names_everything(self, iris_connection, width, clean_registry):
        """T017 / FR-013: table, recorded, offered, and the differing field."""
        node = f"ivg226-msg-{uuid.uuid4().hex[:8]}"
        engine_a = _engine(iris_connection, dim=width, model="model-alpha")
        engine_a.create_node(node)
        engine_a.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        engine_b = _engine(iris_connection, dim=width, model="model-beta")
        with pytest.raises(EmbeddingIdentityConflict) as excinfo:
            engine_b.store_embedding(node, _vector(width, seed=0.4))

        msg = str(excinfo.value)
        assert _NODE_TABLE in msg
        assert "model-alpha" in msg
        assert "model-beta" in msg
        assert excinfo.value.table_name == _NODE_TABLE
        assert excinfo.value.recorded.model_key == "model-alpha"
        assert excinfo.value.offered.model_key == "model-beta"

    def test_width_conflict_message_points_at_the_column(
        self, iris_connection, width, clean_registry
    ):
        """FR-013: a width conflict must say the column declaration has to change."""
        node = f"ivg226-msgw-{uuid.uuid4().hex[:8]}"
        engine = _engine(iris_connection, dim=width, model="model-alpha")
        engine.create_node(node)
        engine.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        with pytest.raises(EmbeddingIdentityConflict) as excinfo:
            engine.store_embedding(node, _vector(width + 1, seed=0.5))

        msg = str(excinfo.value)
        assert "column declaration" in msg
        assert str(width) in msg and str(width + 1) in msg
        assert "-104" in msg, "the message should name the SQLCODE IRIS raises at INSERT"


# ------------------------------------------------------------------------------- T018


class TestRefusalIsNotSwallowed:
    def test_conflict_propagates_through_initialize_schema(
        self, iris_connection, width, clean_registry
    ):
        """T018 / FR-012 / SC-006: `initialize_schema` must not log-and-continue."""
        _engine(iris_connection, dim=width, model="model-alpha").set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        engine_b = _engine(iris_connection, dim=width, model="model-beta")
        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.initialize_schema(auto_deploy_objectscript=False)

        assert _registry_rows(iris_connection)[_NODE_TABLE]["model_key"] == "model-alpha"

    def test_value_error_from_migration_propagates(self, iris_connection, clean_registry):
        """The handler around `_migrate_vector_dimensions` must not eat a ValueError.

        `embedding_dimension=0` is not a width. Before this feature the wide
        `except Exception` at the migration step turned it into a warning and
        `initialize_schema` returned success.
        """
        engine = IRISGraphEngine(iris_connection, embedding_dimension=0)
        with pytest.raises(ValueError):
            engine.initialize_schema(auto_deploy_objectscript=False)


# ------------------------------------------------------------------------------ T018a


class TestEnforcementIsNotOptIn:
    def test_no_flag_disables_the_check(self, iris_connection, width, clean_registry):
        """T018a(a) / FR-014: the default construction path enforces."""
        node = f"ivg226-optin-{uuid.uuid4().hex[:8]}"
        engine_a = _engine(iris_connection, dim=width, model="model-alpha")
        engine_a.create_node(node)
        engine_a.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        # Constructed exactly as a caller would, with no enforcement-related argument.
        engine_b = IRISGraphEngine(
            iris_connection, embedding_dimension=width, embedding_config="model-beta"
        )
        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.store_embedding(node, _vector(width, seed=0.6))

        # And no environment variable turns it off: there is nothing to set. Assert the
        # names a future reader might reach for do not exist as switches.
        for var in (
            "IVG_SKIP_EMBEDDING_IDENTITY",
            "IVG_EMBEDDING_IDENTITY_WARN_ONLY",
            "IVG_IGNORE_EMBEDDING_IDENTITY",
        ):
            os.environ[var] = "1"
        try:
            engine_c = IRISGraphEngine(
                iris_connection, embedding_dimension=width, embedding_config="model-beta"
            )
            with pytest.raises(EmbeddingIdentityConflict):
                engine_c.store_embedding(node, _vector(width, seed=0.7))
        finally:
            for var in (
                "IVG_SKIP_EMBEDDING_IDENTITY",
                "IVG_EMBEDDING_IDENTITY_WARN_ONLY",
                "IVG_IGNORE_EMBEDDING_IDENTITY",
            ):
                os.environ.pop(var, None)

    def test_no_warning_substitutes_for_the_exception(
        self, iris_connection, width, clean_registry, caplog
    ):
        """T018a(b) / FR-014: it raises instead of warning and carrying on."""
        node = f"ivg226-warn-{uuid.uuid4().hex[:8]}"
        engine_a = _engine(iris_connection, dim=width, model="model-alpha")
        engine_a.create_node(node)
        engine_a.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        engine_b = _engine(iris_connection, dim=width, model="model-beta")
        with caplog.at_level("WARNING"):
            with pytest.raises(EmbeddingIdentityConflict):
                engine_b.store_embedding(node, _vector(width, seed=0.8))

        substitutes = [
            r.getMessage()
            for r in caplog.records
            if "identity" in r.getMessage().lower() and "conflict" in r.getMessage().lower()
        ]
        assert not substitutes, f"a log record stood in for the refusal: {substitutes}"
        assert _embedding_row_count(iris_connection, node) == 0

    def test_undeclared_model_at_wrong_width_is_still_refused(
        self, iris_connection, width, clean_registry
    ):
        """T018a(c): the width half of the 2x2 holds where no model is declared.

        An engine with no `embedding_config` and no embedder declares nothing, so no model
        is compared — and it is still refused at the wrong width. Declaring nothing is not
        a way past the contract.
        """
        node = f"ivg226-undecl-{uuid.uuid4().hex[:8]}"
        engine_a = _engine(iris_connection, dim=width, model="model-alpha")
        engine_a.create_node(node)
        engine_a.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        engine_b = IRISGraphEngine(iris_connection, embedding_dimension=width)
        assert engine_b.embedding_config is None
        assert engine_b.embedder is None
        assert engine_b._offered_embedding_identity().is_unknown is True

        # Same width: no model declared, so nothing to conflict on — the write goes in.
        assert engine_b.store_embedding(node, _vector(width, seed=0.9)) is True
        _delete_embedding(iris_connection, node)

        # Wrong width: refused, as an EmbeddingIdentityConflict and not a bare ValueError.
        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.store_embedding(node, _vector(width + 1, seed=0.9))
        assert _embedding_row_count(iris_connection, node) == 0
