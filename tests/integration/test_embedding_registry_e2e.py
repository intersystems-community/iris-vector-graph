"""E2E tests for the embedding identity registry (spec 226, US1).

Live against `ivg-iris-enterprise` (port 31972). Nothing here is mockable: the whole point
of the registry is what the *database* records about a table, and the decisive failure it
prevents — two models at the same width — produces no IRIS error at all.

**The declared width is not a constant in this suite.** `tests/integration/conftest.py`'s
`engine` fixture builds at 768 and migrates the columns back to 128 on teardown, so the live
width depends on which fixture ran last. Every width assertion below reads the width at test
time through `GraphSchema.get_embedding_dimension`. Hardcoding 128 would pass on Tuesday.
"""

import contextlib
import os
import threading
import uuid

import pytest

from iris_vector_graph.constants import VECTOR_TABLE_NAMES
from iris_vector_graph.embedding_identity import (
    EmbeddingIdentity,
    identity_from_config,
    normalize_model_key,
)
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph.schema import GraphSchema

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_REGISTRY = "Graph_KG.embedding_registry"
_NODE_TABLE = "kg_NodeEmbeddings"

_REGISTRY_COLUMNS = (
    "table_name",
    "graph_id",
    "mechanism",
    "model_key",
    "declared_config",
    "dimension",
    "dtype",
    "set_at",
    "set_by",
)


# --------------------------------------------------------------------------- helpers


def _cursor(conn):
    return conn.cursor()


def _live_dimension(conn, table: str = _NODE_TABLE):
    """The width IRIS currently declares for ``table.emb``, read at call time."""
    cur = _cursor(conn)
    try:
        return GraphSchema.get_embedding_dimension(cur, f"Graph_KG.{table}")
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _existing_embedding_tables(conn):
    """The subset of VECTOR_TABLE_NAMES that actually exists here.

    `kg_NodeEmbeddings_optimized` is absent in DDL-only namespaces, so "one row per
    embedding table" has to mean "per table that exists".
    """
    present = []
    for name in VECTOR_TABLE_NAMES:
        cur = _cursor(conn)
        try:
            cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{name}")
            cur.fetchone()
            present.append(name)
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                cur.close()
    return present


def _registry_rows(conn):
    cur = _cursor(conn)
    try:
        cur.execute(
            "SELECT table_name, graph_id, mechanism, model_key, declared_config, "
            f"dimension, dtype, set_at, set_by FROM {_REGISTRY}"
        )
        return {row[0]: dict(zip(_REGISTRY_COLUMNS, row)) for row in cur.fetchall()}
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _clear_registry(conn):
    cur = _cursor(conn)
    try:
        with contextlib.suppress(Exception):
            cur.execute(f"DELETE FROM {_REGISTRY}")
        conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _seed_adopted_row(conn, table: str, dimension):
    cur = _cursor(conn)
    try:
        cur.execute(
            f"INSERT INTO {_REGISTRY} (table_name, graph_id, mechanism, model_key, "
            "declared_config, dimension, dtype, set_by) "
            "VALUES (?, '', NULL, NULL, NULL, ?, 'DOUBLE', 'adopted')",
            [table, dimension],
        )
        conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _open_connection(conn):
    """A second DB-API connection to the same namespace, for the concurrency test."""
    import iris.dbapi as _dbapi

    return _dbapi.connect(
        hostname=conn.hostname,
        port=conn.port,
        namespace=conn.namespace,
        username="_SYSTEM",
        password="SYS",
    )


# ------------------------------------------------------------------- T009 / T010 / T011


class TestFreshSchemaRecordsIdentity:
    def test_fresh_schema_records_identity(self, iris_connection):
        """T009 / FR-001–005: initialize_schema leaves a registry row per embedding table.

        The engine declares a model, so the row is `claimed`, not `adopted`: adoption is for
        the case where nothing in the database knows what produced the vectors.
        """
        _clear_registry(iris_connection)
        model = f"ivg-226-{uuid.uuid4().hex[:8]}"
        dim = _live_dimension(iris_connection) or 128

        engine = IRISGraphEngine(iris_connection, embedding_dimension=dim, embedding_config=model)
        engine.initialize_schema(auto_deploy_objectscript=False)

        rows = _registry_rows(iris_connection)
        present = _existing_embedding_tables(iris_connection)
        assert present, "no embedding table exists — the namespace is not initialized"

        for table in present:
            assert table in rows, f"{table} has no registry row after initialize_schema"
            row = rows[table]
            assert row["mechanism"] == "iris-embedding-config"
            assert row["model_key"] == normalize_model_key(model)
            assert row["dtype"] == "DOUBLE"
            assert row["set_at"] is not None
            assert row["graph_id"] == ""
            assert row["set_by"] == "claimed"
            # The declared width, read now — never a constant (see module docstring).
            assert row["dimension"] == _live_dimension(iris_connection, table)

        assert set(rows) <= set(VECTOR_TABLE_NAMES), (
            f"registry holds a row for something that is not an embedding table: "
            f"{set(rows) - set(VECTOR_TABLE_NAMES)}"
        )

    def test_declared_config_keeps_the_raw_string(self, iris_connection):
        """FR-004: the raw declaration is retained for diagnostics, unnormalized."""
        _clear_registry(iris_connection)
        model = f"  IVG-226-Raw-{uuid.uuid4().hex[:6]}  "
        dim = _live_dimension(iris_connection) or 128

        engine = IRISGraphEngine(iris_connection, embedding_dimension=dim)
        engine.set_embedding_identity(identity_from_config(model, dimension=dim), _NODE_TABLE)

        row = _registry_rows(iris_connection)[_NODE_TABLE]
        assert row["model_key"] == normalize_model_key(model)
        assert row["declared_config"] == model


class TestRegistryColumnShape:
    def test_column_shape_matches_the_contract(self, iris_connection):
        """T010: the shape in contracts/embedding_registry.sql, read from the dictionary."""
        cur = _cursor(iris_connection)
        try:
            cur.execute(
                "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'embedding_registry'"
            )
            cols = {
                str(r[0]).lower(): {"type": str(r[1]).lower(), "nullable": str(r[2]).upper()}
                for r in cur.fetchall()
            }
        finally:
            with contextlib.suppress(Exception):
                cur.close()

        assert cols, "Graph_KG.embedding_registry does not exist"
        assert set(_REGISTRY_COLUMNS) <= set(
            cols
        ), f"missing columns: {set(_REGISTRY_COLUMNS) - set(cols)}"

        # Primary key columns are NOT NULL; the unknown-model columns are nullable.
        assert cols["table_name"]["nullable"] == "NO"
        assert cols["graph_id"]["nullable"] == "NO"
        assert cols["dtype"]["nullable"] == "NO"
        assert cols["set_at"]["nullable"] == "NO"
        assert cols["mechanism"]["nullable"] == "YES"
        assert cols["model_key"]["nullable"] == "YES"

        # No `text` columns anywhere (CLAUDE.md, IRIS SQL rules).
        for name, meta in cols.items():
            assert meta["type"] != "text", f"{name} is declared `text`"

    def test_graph_id_defaults_to_empty_string(self, iris_connection):
        """FR-003: graph_id exists and defaults to '' — 3.2.0 reads it as 'all graphs'."""
        _clear_registry(iris_connection)
        cur = _cursor(iris_connection)
        try:
            cur.execute(
                f"INSERT INTO {_REGISTRY} (table_name, mechanism, model_key, dimension, dtype) "
                "VALUES (?, 'external', 'default-probe', 128, 'DOUBLE')",
                [_NODE_TABLE],
            )
            iris_connection.commit()
        finally:
            with contextlib.suppress(Exception):
                cur.close()

        assert _registry_rows(iris_connection)[_NODE_TABLE]["graph_id"] == ""

    def test_primary_key_is_table_name_and_graph_id(self, iris_connection):
        """T010: asserted behaviourally — a duplicate (table_name, graph_id) is rejected.

        This is what makes the concurrent-INSERT claim path safe, so it is tested as
        behaviour rather than as a dictionary row.
        """
        _clear_registry(iris_connection)
        cur = _cursor(iris_connection)
        try:
            insert = (
                f"INSERT INTO {_REGISTRY} (table_name, graph_id, mechanism, model_key, "
                "dimension, dtype) VALUES (?, '', 'external', 'pk-probe', 128, 'DOUBLE')"
            )
            cur.execute(insert, [_NODE_TABLE])
            iris_connection.commit()
            with pytest.raises(Exception):
                cur.execute(insert, [_NODE_TABLE])
                iris_connection.commit()
        finally:
            with contextlib.suppress(Exception):
                iris_connection.rollback()
            with contextlib.suppress(Exception):
                cur.close()


class TestSetEmbeddingIdentityRejections:
    """T011 / FR-003, FR-004: what `set_embedding_identity` refuses outright."""

    @pytest.fixture
    def engine(self, iris_connection):
        _clear_registry(iris_connection)
        dim = _live_dimension(iris_connection) or 128
        return IRISGraphEngine(iris_connection, embedding_dimension=dim)

    def test_table_outside_vector_table_names_raises(self, engine, iris_connection):
        ident = identity_from_config("m", dimension=_live_dimension(iris_connection) or 128)
        with pytest.raises(ValueError):
            engine.set_embedding_identity(ident, "Graph_KG.nodes")

    def test_non_empty_graph_id_raises(self, engine, iris_connection):
        """3.2.0 must not interpret graph_id as anything other than 'all graphs'."""
        ident = identity_from_config("m", dimension=_live_dimension(iris_connection) or 128)
        with pytest.raises(ValueError):
            engine.set_embedding_identity(ident, _NODE_TABLE, graph_id="tenant-a")

    def test_mechanism_outside_the_closed_set_raises(self, engine):
        ident = EmbeddingIdentity(mechanism="hand-rolled", model_key="m", dimension=128)
        with pytest.raises(ValueError):
            engine.set_embedding_identity(ident, _NODE_TABLE)

    def test_unknown_identity_raises(self, engine):
        """Only `adopt_embedding_identities` writes an unknown model (FR-007)."""
        ident = EmbeddingIdentity(mechanism=None, model_key=None, dimension=128)
        assert ident.is_unknown is True
        with pytest.raises(ValueError):
            engine.set_embedding_identity(ident, _NODE_TABLE)


class TestSetEmbeddingIdentityCases:
    """The four cases in contracts/embedding_identity_api.md."""

    def test_insert_when_no_row_exists(self, iris_connection):
        _clear_registry(iris_connection)
        dim = _live_dimension(iris_connection) or 128
        engine = IRISGraphEngine(iris_connection, embedding_dimension=dim)
        engine.set_embedding_identity(identity_from_config("model-x", dimension=dim))
        row = _registry_rows(iris_connection)[_NODE_TABLE]
        assert row["model_key"] == "model-x"
        assert row["set_by"] == "claimed"

    def test_same_model_is_a_no_op(self, iris_connection):
        _clear_registry(iris_connection)
        dim = _live_dimension(iris_connection) or 128
        engine = IRISGraphEngine(iris_connection, embedding_dimension=dim)
        ident = identity_from_config("model-x", dimension=dim)
        engine.set_embedding_identity(ident)
        first = _registry_rows(iris_connection)[_NODE_TABLE]["set_at"]
        engine.set_embedding_identity(ident)
        assert _registry_rows(iris_connection)[_NODE_TABLE]["set_at"] == first

    def test_different_model_conflicts(self, iris_connection):
        _clear_registry(iris_connection)
        dim = _live_dimension(iris_connection) or 128
        engine = IRISGraphEngine(iris_connection, embedding_dimension=dim)
        engine.set_embedding_identity(identity_from_config("model-x", dimension=dim))
        with pytest.raises(EmbeddingIdentityConflict):
            engine.set_embedding_identity(identity_from_config("model-y", dimension=dim))
        assert _registry_rows(iris_connection)[_NODE_TABLE]["model_key"] == "model-x"

    def test_force_overwrites_and_records_forced(self, iris_connection):
        _clear_registry(iris_connection)
        dim = _live_dimension(iris_connection) or 128
        engine = IRISGraphEngine(iris_connection, embedding_dimension=dim)
        engine.set_embedding_identity(identity_from_config("model-x", dimension=dim))
        engine.set_embedding_identity(identity_from_config("model-y", dimension=dim), force=True)
        row = _registry_rows(iris_connection)[_NODE_TABLE]
        assert row["model_key"] == "model-y"
        assert row["set_by"] == "forced"

    def test_get_returns_none_when_unrecorded(self, iris_connection):
        _clear_registry(iris_connection)
        dim = _live_dimension(iris_connection) or 128
        engine = IRISGraphEngine(iris_connection, embedding_dimension=dim)
        assert engine.get_embedding_identity(_NODE_TABLE) is None

    def test_get_round_trips_what_set_recorded(self, iris_connection):
        _clear_registry(iris_connection)
        dim = _live_dimension(iris_connection) or 128
        engine = IRISGraphEngine(iris_connection, embedding_dimension=dim)
        engine.set_embedding_identity(identity_from_config("Model-X", dimension=dim))
        recorded = engine.get_embedding_identity(_NODE_TABLE)
        assert recorded is not None
        assert recorded.model_key == "model-x"
        assert recorded.mechanism == "iris-embedding-config"
        assert recorded.dimension == dim
        assert recorded.dtype == "DOUBLE"
        assert recorded.is_unknown is False


# --------------------------------------------------------------- T022 / T023 / T024 / T025


_OPTIMIZED = "kg_NodeEmbeddings_optimized"
#: Deliberately unlike anything a constant or a `.cls` file could supply: not
#: `DEFAULT_EMBEDDING_DIMENSION` (768), not the `.cls` value (384), not the live node width.
_WITNESS_WIDTH = 96


def _alter_width(conn, table: str, width):
    """Re-declare ``table.emb``. ``width=None`` declares it with no length at all."""
    decl = "VECTOR(DOUBLE)" if width is None else f"VECTOR(DOUBLE, {width})"
    cur = _cursor(conn)
    try:
        cur.execute(f"ALTER TABLE Graph_KG.{table} ALTER COLUMN emb {decl}")
        conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _embedding_fingerprint(conn, table: str = _NODE_TABLE):
    """Everything the embedding rows are, as the driver renders them.

    Deliberately not a vector-function call: the point is that adoption never reads or
    rewrites this data, so any change at all must show up.
    """
    cur = _cursor(conn)
    try:
        cur.execute(f"SELECT id, emb FROM Graph_KG.{table} ORDER BY id")
        return sorted((str(r[0]), str(r[1])) for r in cur.fetchall())
    finally:
        with contextlib.suppress(Exception):
            cur.close()


@pytest.fixture
def witness_width(iris_connection):
    """`kg_NodeEmbeddings_optimized.emb` at a width no constant in the codebase holds.

    Restores the original declaration afterwards, so the suite's width oscillation is
    unaffected by this test.
    """
    original = _live_dimension(iris_connection, _OPTIMIZED)
    if original is None:
        pytest.skip(f"Graph_KG.{_OPTIMIZED} is absent in this namespace")
    _alter_width(iris_connection, _OPTIMIZED, _WITNESS_WIDTH)
    try:
        yield _WITNESS_WIDTH
    finally:
        _alter_width(iris_connection, _OPTIMIZED, original)


class TestAdoption:
    def test_adoption_from_live_declaration(self, iris_connection, witness_width):
        """T022 / FR-006, FR-007: the width comes from the data dictionary, nothing else.

        The engine here declares no model, so nothing claims the adopted rows and
        `set_by` stays `adopted`.

        Adoption is asserted through its own entry point rather than through
        `initialize_schema`, because the step that follows adoption inside
        `initialize_schema` is the dimension migration: it brings every vector column to
        the engine's configured width and then moves the freshly adopted row to match. A
        row read after a full `initialize_schema` therefore holds the *post*-migration
        width, which cannot distinguish "read from the dictionary" from "copied from the
        engine's configuration". The second half of this test covers that path.
        """
        from iris_vector_graph.constants import DEFAULT_EMBEDDING_DIMENSION

        _clear_registry(iris_connection)
        node_width = _live_dimension(iris_connection) or 128
        assert node_width != witness_width, (
            "the witness width must differ from the node table's — otherwise this test "
            "cannot tell the two columns apart"
        )

        engine = IRISGraphEngine(iris_connection, embedding_dimension=node_width)
        outcomes = engine.adopt_embedding_identities()
        assert outcomes[_OPTIMIZED] == "adopted"

        rows = _registry_rows(iris_connection)
        assert _OPTIMIZED in rows, "adoption skipped a table that exists"

        row = rows[_OPTIMIZED]
        assert row["set_by"] == "adopted"
        assert row["model_key"] is None, "adoption guessed a model"
        assert row["mechanism"] is None
        assert row["dtype"] == "DOUBLE"
        assert row["dimension"] == witness_width
        assert (
            row["dimension"] != DEFAULT_EMBEDDING_DIMENSION
        ), "the width came from constants.DEFAULT_EMBEDDING_DIMENSION"
        assert row["dimension"] != 384, "the width came from the .cls file"
        assert (
            row["dimension"] != node_width
        ), "the width came from another table — adoption must read each column"

        # And the node table's own row records its own width, not the witness's.
        assert rows[_NODE_TABLE]["dimension"] == node_width

        # The full path: an installation that predates the registry needs no operator
        # action. `initialize_schema` adopts, then migrates, and the row it leaves behind
        # agrees with the column as the column now stands.
        _clear_registry(iris_connection)
        engine.initialize_schema(auto_deploy_objectscript=False)
        rows = _registry_rows(iris_connection)
        assert (
            rows[_NODE_TABLE]["set_by"] == "adopted"
        ), "initialize_schema did not adopt — an existing installation would be unguarded"
        assert rows[_NODE_TABLE]["model_key"] is None
        assert rows[_NODE_TABLE]["dimension"] == _live_dimension(iris_connection)
        assert rows[_OPTIMIZED]["dimension"] == _live_dimension(
            iris_connection, _OPTIMIZED
        ), "the recorded width no longer agrees with the column after migration"

    def test_adoption_does_not_touch_vector_data(self, iris_connection):
        """T023 / FR-007: adoption reads the dictionary, never the vectors."""
        _clear_registry(iris_connection)
        width = _live_dimension(iris_connection) or 128
        node = f"ivg226-adopt-{uuid.uuid4().hex[:8]}"

        engine = IRISGraphEngine(iris_connection, embedding_dimension=width)
        engine.create_node(node)
        engine.store_embedding(node, [0.25] * width)

        before = _embedding_fingerprint(iris_connection)
        assert before, "no embedding rows — this test would prove nothing"

        _clear_registry(iris_connection)
        engine.adopt_embedding_identities()

        assert _embedding_fingerprint(iris_connection) == before

    def test_claim_over_adopted_row_and_wrong_width_refusal(self, iris_connection):
        """T024 / FR-008: a never-claimed row still enforces its width."""
        _clear_registry(iris_connection)
        width = _live_dimension(iris_connection) or 128
        node = f"ivg226-claim-{uuid.uuid4().hex[:8]}"

        engine = IRISGraphEngine(iris_connection, embedding_dimension=width)
        engine.create_node(node)
        engine.adopt_embedding_identities()
        assert _registry_rows(iris_connection)[_NODE_TABLE]["set_by"] == "adopted"

        # No model was ever claimed, and the wrong width is still refused.
        with pytest.raises(EmbeddingIdentityConflict):
            engine.store_embedding(node, [0.5] * (width + 1))

        # The first writer to declare a model at the recorded width claims the row.
        claimer = IRISGraphEngine(
            iris_connection, embedding_dimension=width, embedding_config="adopting-model"
        )
        claimer.set_embedding_identity(
            identity_from_config("adopting-model", dimension=width), _NODE_TABLE
        )
        row = _registry_rows(iris_connection)[_NODE_TABLE]
        assert row["model_key"] == "adopting-model"
        assert row["set_by"] == "claimed"

    def test_no_declared_width_records_null(self, iris_connection):
        """T025: an unlengthed column adopts a NULL width, not a guess.

        This is the `SQLCODE -260` case: -260 is raised off the column declaration, not
        off the data, so a column with no declared length has no width to record.
        """
        original = _live_dimension(iris_connection, _OPTIMIZED)
        if original is None:
            pytest.skip(f"Graph_KG.{_OPTIMIZED} is absent in this namespace")

        _clear_registry(iris_connection)
        _alter_width(iris_connection, _OPTIMIZED, None)
        try:
            assert _live_dimension(iris_connection, _OPTIMIZED) is None

            width = _live_dimension(iris_connection) or 128
            engine = IRISGraphEngine(iris_connection, embedding_dimension=width)
            outcomes = engine.adopt_embedding_identities()

            assert outcomes[_OPTIMIZED] == "adopted_no_declared_width"
            assert _registry_rows(iris_connection)[_OPTIMIZED]["dimension"] is None
        finally:
            _alter_width(iris_connection, _OPTIMIZED, original)
            _clear_registry(iris_connection)


# ---------------------------------------------------------------------------- T011a


class TestConcurrentClaim:
    def test_concurrent_claim_of_adopted_row(self, iris_connection):
        """T011a / FR-009: the claim is atomic, not a read-then-write race.

        Two writers declare *different* models at the recorded width against the same
        adopted row. Exactly one may win. The loser must lose because its
        `UPDATE ... WHERE model_key IS NULL` matched no row and the re-read disagreed — so
        the pair runs 20 times: a non-atomic implementation cannot get lucky 20 times.
        """
        dim = _live_dimension(iris_connection) or 128

        conn_a = _open_connection(iris_connection)
        conn_b = _open_connection(iris_connection)
        try:
            engine_a = IRISGraphEngine(conn_a, embedding_dimension=dim)
            engine_b = IRISGraphEngine(conn_b, embedding_dimension=dim)

            for i in range(20):
                _clear_registry(iris_connection)
                _seed_adopted_row(iris_connection, _NODE_TABLE, dim)
                seeded = _registry_rows(iris_connection)[_NODE_TABLE]
                assert seeded["model_key"] is None, "seed is not an adopted row"

                model_a = f"race-a-{i}"
                model_b = f"race-b-{i}"
                outcomes = {}
                barrier = threading.Barrier(2)

                def claim(tag, engine, model):
                    ident = identity_from_config(model, dimension=dim)
                    barrier.wait(timeout=30)
                    try:
                        engine.set_embedding_identity(ident, _NODE_TABLE)
                        outcomes[tag] = "won"
                    except EmbeddingIdentityConflict as exc:
                        outcomes[tag] = f"conflict: {exc}"
                    except Exception as exc:  # pragma: no cover - diagnostic
                        outcomes[tag] = f"error: {type(exc).__name__}: {exc}"

                threads = [
                    threading.Thread(target=claim, args=("a", engine_a, model_a)),
                    threading.Thread(target=claim, args=("b", engine_b, model_b)),
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=60)

                winners = [tag for tag, out in outcomes.items() if out == "won"]
                conflicts = [
                    tag
                    for tag, out in outcomes.items()
                    if isinstance(out, str) and out.startswith("conflict")
                ]
                assert (
                    len(winners) == 1
                ), f"iteration {i}: expected exactly one winner, got {outcomes}"
                assert len(conflicts) == 1, (
                    f"iteration {i}: the loser must raise EmbeddingIdentityConflict, "
                    f"got {outcomes}"
                )

                final = _registry_rows(iris_connection)[_NODE_TABLE]["model_key"]
                expected = model_a if winners == ["a"] else model_b
                assert final == expected, (
                    f"iteration {i}: registry holds {final!r}, winner declared "
                    f"{expected!r} — a blend or the loser's value means the claim is "
                    f"not atomic"
                )
        finally:
            for c in (conn_a, conn_b):
                with contextlib.suppress(Exception):
                    c.close()
            _clear_registry(iris_connection)
