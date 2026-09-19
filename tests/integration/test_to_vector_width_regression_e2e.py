"""E2E regression test for ADR-0005: never declare a width inside `TO_VECTOR`.

Spec 226, US4 / T037 / FR-017.

`GraphSchema.get_procedures_sql_list` accepts an `embedding_dimension` it reads nowhere,
and the procedure it generates says `TO_VECTOR(:queryInput, DOUBLE)` with no length. That
reads like an unfinished wire-up, and completing it is a one-line change. This test exists
so that change fails the suite.

What the two assertions are for: the first pins the behaviour we want (a wrong-width query
raises `SQLCODE -257`), and the second shows what "finishing" the parameter would buy
instead — a plausible number computed against a query vector IRIS silently padded or
truncated. Without the second assertion the first looks like a test of an error message.
With it, the trade is legible: the declared width converts a loud, correct refusal into a
quietly wrong ranking.

Live against `ivg-iris-enterprise` (port 31972). This cannot be mocked: the fact under
test is what the IRIS SQL engine does with a length argument.
"""

import contextlib
import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_WIDTH = 4
_STORED = "0.1,0.2,0.3,0.4"


@pytest.fixture
def probe_table(iris_connection):
    """A throwaway table declared `VECTOR(DOUBLE, 4)` holding one row.

    Its own table, created and dropped here, rather than any table the engine owns: this
    test deliberately issues wrong-width queries, and a width this small keeps the
    arithmetic readable.
    """
    table = f"Graph_KG.ivg226_tovector_{uuid.uuid4().hex[:8]}"
    cur = iris_connection.cursor()
    try:
        cur.execute(
            f"CREATE TABLE {table} (id VARCHAR(64) PRIMARY KEY, emb VECTOR(DOUBLE, {_WIDTH}))"
        )
        cur.execute(
            f"INSERT INTO {table} (id, emb) VALUES (?, TO_VECTOR(?, DOUBLE, {_WIDTH}))",
            ["a", _STORED],
        )
        iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cur.close()

    yield table

    cur = iris_connection.cursor()
    try:
        cur.execute(f"DROP TABLE {table}")
        iris_connection.commit()
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            cur.close()


def _score(conn, table: str, query: str, *, declare_width: bool):
    """`VECTOR_COSINE` against the stored row, with and without a declared query width."""
    converter = f"TO_VECTOR(?, DOUBLE, {_WIDTH})" if declare_width else "TO_VECTOR(?, DOUBLE)"
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT VECTOR_COSINE(emb, {converter}) FROM {table} WHERE id = 'a'",
            [query],
        )
        row = cur.fetchone()
        return None if row is None else tuple(row)[0]
    finally:
        with contextlib.suppress(Exception):
            cur.close()


class TestUnlengthedToVectorRefusesAWrongWidth:
    def test_matching_width_scores(self, iris_connection, probe_table):
        """The control: the form the procedures use works for a correct query."""
        score = _score(iris_connection, probe_table, "0.1,0.2,0.3,0.4", declare_width=False)
        assert score is not None
        assert float(score) == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.parametrize("query", ["0.1,0.2,0.3", "0.1,0.2,0.3,0.4,0.5,0.6"])
    def test_wrong_width_raises_minus_257(self, iris_connection, probe_table, query):
        """Too short and too long both refuse. The absence of a length is what makes
        IRIS compare the two widths at all."""
        with pytest.raises(Exception) as excinfo:
            _score(iris_connection, probe_table, query, declare_width=False)
        message = str(excinfo.value)
        assert "-257" in message, f"expected SQLCODE -257, got: {message}"


class TestDeclaringTheWidthSilentlyReshapesTheQuery:
    """What wiring `embedding_dimension` into the procedure would actually do.

    This is not a test of desirable behaviour — it is the measurement ADR-0005 rests on,
    kept executable so nobody has to take the ADR's word for it.
    """

    def test_a_short_query_returns_a_number_instead_of_an_error(self, iris_connection, probe_table):
        score = _score(iris_connection, probe_table, "0.1,0.2,0.3", declare_width=True)
        assert score is not None, "the lengthed form errored — ADR-0005 needs re-measuring"
        assert 0.0 <= float(score) <= 1.0

    def test_a_long_query_is_truncated_to_a_perfect_match(self, iris_connection, probe_table):
        """The worst case. A six-element query whose first four elements happen to match
        the stored vector scores 1.0: the wrong row first, with maximum confidence."""
        score = _score(iris_connection, probe_table, "0.1,0.2,0.3,0.4,9.9,9.9", declare_width=True)
        assert score is not None
        assert float(score) == pytest.approx(
            1.0, abs=1e-6
        ), "the extra elements were not silently dropped — re-measure ADR-0005"
