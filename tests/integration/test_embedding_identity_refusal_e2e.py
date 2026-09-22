"""E2E tests for embedding identity enforcement (spec 226 US2, as 227 leaves it).

Live against `ivg-iris-enterprise` (port 31972). Nothing here is mockable: the registry
is a statement the *database* makes about a table, and the failures it prevents produce
either no IRIS error at all or a raw `SQLCODE -104` naming a hashed table.

**What 227 changed about this file.** 3.2.0 had one embedding table, so two models at one
width collided in it and the registry's only honest answer was to refuse the second. 227
routes `(graph_id, model_key)` to its own physical table, so those two models no longer
meet: they get two tables, and each holds only its own vectors. The refusal that mattered
most in 226 is therefore *superseded by separation*, and the test below asserts the
separation instead of the refusal — asserting the old behaviour would have locked in a
conflict the design has removed.

What survives is every conflict that is still reachable on **one** route, because a route
is one column with one declared width and one recorded provenance:

* **width** — the same model offered at another width. The column declaration is the
  authority; IRIS enforces it at INSERT.
* **mechanism** — one model name, two ways of producing it. `route_table_name` hashes the
  model key only, so a local `sentence-transformers` embedder and an IRIS
  `embedding_config` of the same name resolve to the *same* route. Their vectors are not
  interchangeable, and only the registry can say so.

The declared width of `kg_NodeEmbeddings` is not a constant in this suite (see
`test_embedding_registry_e2e.py`'s module docstring), so the tests that write to the
default pair read it at test time. The routed tables declare whatever their first write
declares, so those tests name their own widths.
"""

import contextlib
import os
import uuid

import pytest

from iris_vector_graph.embedding_identity import identity_from_config, normalize_model_key
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph.routing import route_table_name
from tests.integration.test_embedding_registry_e2e import (
    _clear_registry,
    _live_dimension,
    _registry_rows,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_NODE_TABLE = "kg_NodeEmbeddings"

#: A width for the routed tables these tests create. Small, and not the live width of
#: `kg_NodeEmbeddings`, so a route that accidentally resolved to the legacy table would
#: fail rather than pass by coincidence.
ROUTE_WIDTH = 16


def _vector(width: int, seed: float = 0.1):
    return [seed] * width


def _onehot(width: int, pos: int):
    """A vector distinguishable from another one-hot by cosine alone.

    `[0.1] * n` and `[0.2] * n` are parallel, so cosine cannot tell them apart. Two
    one-hots at different positions score 1.0 against themselves and 0.0 against each
    other, which is what lets these tests prove *which* vector a table holds without
    reading a stored vector back into Python (ADR-0005).
    """
    vec = [0.0] * width
    vec[pos] = 1.0
    return vec


def _route_table(graph: str, model: str) -> str:
    """The routed table name for a pair, derived the way the engine derives it."""
    return route_table_name(graph, normalize_model_key(model))


def _row_count(conn, table: str, node_id: str, graph: str = "") -> int:
    """Rows for one node in one graph of one table.

    Scoped on `graph_id` as well as `node_id`, because 227 re-keyed these tables on
    `(graph_id, node_id)` and an unscoped count answers for every graph at once.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(*) FROM Graph_KG.{table}"
            " WHERE node_id = ? AND COALESCE(graph_id, '') = COALESCE(?, '')",
            [node_id, graph],
        )
        return int(cur.fetchone()[0])
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _cosine(conn, table: str, node_id: str, probe, graph: str = ""):
    """`VECTOR_COSINE` of the stored vector against `probe`, or None if no row."""
    probe_str = ",".join(str(x) for x in probe)
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT VECTOR_COSINE(emb, TO_VECTOR('{probe_str}', DOUBLE))"
            f" FROM Graph_KG.{table}"
            " WHERE node_id = ? AND COALESCE(graph_id, '') = COALESCE(?, '')",
            [node_id, graph],
        )
        row = cur.fetchone()
        return None if row is None else float(row[0])
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _delete_embedding(conn, node_id: str, table: str = _NODE_TABLE) -> None:
    cur = conn.cursor()
    try:
        with contextlib.suppress(Exception):
            cur.execute(f"DELETE FROM Graph_KG.{table} WHERE node_id = ?", [node_id])
        conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cur.close()


class _StubEmbedder:
    """An embedder that can name itself, which is all the identity layer reads.

    `_embedder_model_name` looks for `model_name` first, so this is enough to make an
    engine offer `sentence-transformers` for a given model key. It is never called to
    embed anything here: these tests supply their own vectors.
    """

    def __init__(self, model_name: str):
        self.model_name = model_name


@pytest.fixture
def width(iris_connection):
    """The live declared width of `kg_NodeEmbeddings.emb`, read now."""
    dim = _live_dimension(iris_connection)
    assert dim, "kg_NodeEmbeddings.emb has no declared width — cannot test enforcement"
    return dim


@pytest.fixture
def clean_registry(iris_connection):
    """No recorded routes and no routed tables, before and after.

    Both halves: a surviving table with no row is the orphan state that made a
    later run at another width fail (see `_clear_registry`).
    """
    _clear_registry(iris_connection)
    yield
    _clear_registry(iris_connection)


def _engine(conn, *, dim, model=None, embedder=None):
    return IRISGraphEngine(
        conn, embedding_dimension=dim, embedding_config=model, embedder=embedder
    )


# ------------------------------------------------------------------------------- T015


class TestSameWidthDifferentModel:
    def test_same_width_different_model_gets_its_own_route(
        self, iris_connection, clean_registry
    ):
        """T015 / FR-010 under 227: separated, not refused.

        The case 226 could only refuse — two models, one width, one graph, and an IRIS
        that sees nothing wrong — is now a pair of routes. Both writes succeed, and the
        guarantee moves from "the second is rejected" to "the second cannot be mistaken
        for the first", which is the stronger of the two.
        """
        node = f"ivg227-shared-{uuid.uuid4().hex[:8]}"
        vec_a = _onehot(ROUTE_WIDTH, 0)
        vec_b = _onehot(ROUTE_WIDTH, 1)

        engine_a = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-alpha")
        engine_a.create_node(node)
        assert engine_a.store_embedding(node, vec_a) is True

        engine_b = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-beta")
        assert engine_b.store_embedding(node, vec_b) is True

        table_a = _route_table("", "model-alpha")
        table_b = _route_table("", "model-beta")
        assert table_a != table_b, "two models hashed to one table"

        # One row each, under the same node ID.
        assert _row_count(iris_connection, table_a, node) == 1
        assert _row_count(iris_connection, table_b, node) == 1

        # And each route holds its own vector, not the other's. Cosine rather than a
        # read-back: 1.0 against itself, 0.0 against the other one-hot.
        assert _cosine(iris_connection, table_a, node, vec_a) == pytest.approx(1.0)
        assert _cosine(iris_connection, table_a, node, vec_b) == pytest.approx(0.0)
        assert _cosine(iris_connection, table_b, node, vec_b) == pytest.approx(1.0)
        assert _cosine(iris_connection, table_b, node, vec_a) == pytest.approx(0.0)

        # Neither landed in 3.2.0's table: a declared model is not the default pair.
        assert _row_count(iris_connection, _NODE_TABLE, node) == 0

        rows = _registry_rows(iris_connection)
        assert rows[table_a]["model_key"] == "model-alpha"
        assert rows[table_b]["model_key"] == "model-beta"

    def test_one_models_route_is_not_the_others_fallback(
        self, iris_connection, clean_registry
    ):
        """A model with no route reads nothing — it does not read the other model's.

        The refusal 226 relied on is gone, so this is what stops one model's vectors
        from being served as another's: an unrouted pair resolves to nothing (FR-013).
        """
        node = f"ivg227-norow-{uuid.uuid4().hex[:8]}"
        engine_a = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-alpha")
        engine_a.create_node(node)
        assert engine_a.store_embedding(node, _onehot(ROUTE_WIDTH, 0)) is True

        engine_b = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-beta")
        assert engine_b.resolve_route() is None, "model-beta has no route to read"
        assert node in engine_b.get_unembedded_nodes(model_key="model-beta")


# ------------------------------------------------------------------------------- T016


class TestBatchIsAllOrNothing:
    def test_conflicting_batch_writes_nothing(self, iris_connection, clean_registry):
        """T016: `store_embeddings` refuses before the first INSERT, not mid-loop.

        Driven by a width conflict on one route, which is the reachable shape under 227:
        one model, one table, a batch offered at a width the column does not declare.
        """
        nodes = [f"ivg227-batch-{i}-{uuid.uuid4().hex[:6]}" for i in range(3)]
        seed = f"ivg227-seed-{uuid.uuid4().hex[:6]}"
        table = _route_table("", "model-alpha")

        engine_a = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-alpha")
        engine_a.create_node(seed)
        for nid in nodes:
            engine_a.create_node(nid)
        # The route, and therefore the declared width, now exists.
        assert engine_a.store_embedding(seed, _vector(ROUTE_WIDTH)) is True

        engine_b = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-alpha")
        items = [
            {"node_id": nid, "embedding": _vector(ROUTE_WIDTH + 1, seed=0.3)}
            for nid in nodes
        ]
        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.store_embeddings(items)

        for nid in nodes:
            assert (
                _row_count(iris_connection, table, nid) == 0
            ), f"{nid} was written by a refused batch — the refusal is not all-or-nothing"
        # The seed row and the recorded width are untouched by the refusal.
        assert _row_count(iris_connection, table, seed) == 1
        assert _registry_rows(iris_connection)[table]["dimension"] == ROUTE_WIDTH


# ------------------------------------------------------------------------------- T017


class TestRefusalMessage:
    def test_mechanism_conflict_message_names_everything(
        self, iris_connection, clean_registry
    ):
        """T017 / FR-013: table, recorded, offered, and the differing field.

        The reachable model-level conflict under 227. `route_table_name` hashes the model
        key and nothing else, so a local `sentence-transformers` embedder called
        `shared-model-name` and an IRIS `embedding_config` of the same name resolve to
        **one** route. Same name, different producer, incomparable vectors — invisible to
        IRIS, and the registry is the only thing that can say so.
        """
        model = "shared-model-name"
        table = _route_table("", model)
        node_a = f"ivg227-mech-a-{uuid.uuid4().hex[:8]}"
        node_b = f"ivg227-mech-b-{uuid.uuid4().hex[:8]}"

        engine_st = _engine(
            iris_connection, dim=ROUTE_WIDTH, embedder=_StubEmbedder(model)
        )
        engine_st.create_node(node_a)
        engine_st.create_node(node_b)
        assert engine_st.store_embedding(node_a, _vector(ROUTE_WIDTH)) is True
        assert _registry_rows(iris_connection)[table]["mechanism"] == "sentence-transformers"

        engine_cfg = _engine(iris_connection, dim=ROUTE_WIDTH, model=model)
        with pytest.raises(EmbeddingIdentityConflict) as excinfo:
            engine_cfg.store_embedding(node_b, _vector(ROUTE_WIDTH, seed=0.4))

        msg = str(excinfo.value)
        assert table in msg
        assert "sentence-transformers" in msg
        assert "iris-embedding-config" in msg
        assert excinfo.value.table_name == table
        assert excinfo.value.recorded.mechanism == "sentence-transformers"
        assert excinfo.value.offered.mechanism == "iris-embedding-config"
        assert _row_count(iris_connection, table, node_b) == 0

    def test_width_conflict_message_points_at_the_column(
        self, iris_connection, clean_registry
    ):
        """FR-013: a width conflict must say the column declaration has to change."""
        node = f"ivg227-msgw-{uuid.uuid4().hex[:8]}"
        engine = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-alpha")
        engine.create_node(node)
        assert engine.store_embedding(node, _vector(ROUTE_WIDTH)) is True

        with pytest.raises(EmbeddingIdentityConflict) as excinfo:
            engine.store_embedding(node, _vector(ROUTE_WIDTH + 1, seed=0.5))

        msg = str(excinfo.value)
        assert "column declaration" in msg
        assert str(ROUTE_WIDTH) in msg and str(ROUTE_WIDTH + 1) in msg
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
    def test_no_flag_disables_the_check(self, iris_connection, clean_registry):
        """T018a(a) / FR-014: the default construction path enforces."""
        node = f"ivg227-optin-{uuid.uuid4().hex[:8]}"
        engine_a = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-alpha")
        engine_a.create_node(node)
        assert engine_a.store_embedding(node, _vector(ROUTE_WIDTH)) is True

        # Constructed exactly as a caller would, with no enforcement-related argument.
        engine_b = IRISGraphEngine(
            iris_connection, embedding_dimension=ROUTE_WIDTH, embedding_config="model-alpha"
        )
        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.store_embedding(node, _vector(ROUTE_WIDTH + 1, seed=0.6))

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
                iris_connection,
                embedding_dimension=ROUTE_WIDTH,
                embedding_config="model-alpha",
            )
            with pytest.raises(EmbeddingIdentityConflict):
                engine_c.store_embedding(node, _vector(ROUTE_WIDTH + 1, seed=0.7))
        finally:
            for var in (
                "IVG_SKIP_EMBEDDING_IDENTITY",
                "IVG_EMBEDDING_IDENTITY_WARN_ONLY",
                "IVG_IGNORE_EMBEDDING_IDENTITY",
            ):
                os.environ.pop(var, None)

    def test_no_warning_substitutes_for_the_exception(
        self, iris_connection, clean_registry, caplog
    ):
        """T018a(b) / FR-014: it raises instead of warning and carrying on."""
        seed = f"ivg227-warnseed-{uuid.uuid4().hex[:8]}"
        node = f"ivg227-warn-{uuid.uuid4().hex[:8]}"
        table = _route_table("", "model-alpha")

        engine_a = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-alpha")
        engine_a.create_node(seed)
        engine_a.create_node(node)
        assert engine_a.store_embedding(seed, _vector(ROUTE_WIDTH)) is True

        engine_b = _engine(iris_connection, dim=ROUTE_WIDTH, model="model-alpha")
        with caplog.at_level("WARNING"):
            with pytest.raises(EmbeddingIdentityConflict):
                engine_b.store_embedding(node, _vector(ROUTE_WIDTH + 1, seed=0.8))

        substitutes = [
            r.getMessage()
            for r in caplog.records
            if "identity" in r.getMessage().lower() and "conflict" in r.getMessage().lower()
        ]
        assert not substitutes, f"a log record stood in for the refusal: {substitutes}"
        assert _row_count(iris_connection, table, node) == 0

    def test_undeclared_model_at_wrong_width_is_still_refused(
        self, iris_connection, width, clean_registry
    ):
        """T018a(c): the width half of the 2x2 holds where no model is declared.

        An engine with no `embedding_config` and no embedder declares nothing, so no model
        is compared — and it is still refused at the wrong width. Declaring nothing is not
        a way past the contract.

        This is also the default pair (FR-015), so it writes to `kg_NodeEmbeddings` rather
        than to a route, and the width it is held to is that table's declared one.
        """
        node = f"ivg227-undecl-{uuid.uuid4().hex[:8]}"
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
        assert _row_count(iris_connection, _NODE_TABLE, node) == 1
        _delete_embedding(iris_connection, node)
        assert _row_count(iris_connection, _NODE_TABLE, node) == 0

        # Wrong width: refused, as an EmbeddingIdentityConflict and not a bare ValueError.
        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.store_embedding(node, _vector(width + 1, seed=0.9))
        assert _row_count(iris_connection, _NODE_TABLE, node) == 0
