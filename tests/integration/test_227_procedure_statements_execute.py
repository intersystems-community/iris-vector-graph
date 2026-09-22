"""Spec 227 sweep — the procedure statements reach the server and run.

Four separate defects made `ivg.vector.search`, `ivg.bm25.search`, `ivg.ppr` and
`ivg.retrieve` unusable on `ivg-iris-enterprise` (`irishealth:2026.3.0AI.113.0`), and
none of them was visible from Python:

1. `ORDER BY <uncast vector alias>` in a `TOP` CTE — SQLCODE -400 `<UNDEFINED>` at
   Query Open (tests/unit/test_227_cte_vector_order_by.py)
2. `?` inside `JSON_TABLE`'s source argument — `Incorrect number of parameters` from
   the driver (tests/unit/test_227_json_table_literal_args.py)
3. `ivg.retrieve` selecting out of the BM25 arm instead of the RRF fusion, and
   projecting `rrf_score` as a node — SQLCODE -29 at Prepare
4. `ORDER BY` without `TOP` in the fusion CTE — SQLCODE -1 at Prepare
   (tests/unit/test_227_retrieve_projection.py covers 3 and 4)

The unit tests assert on the emitted text. They cannot catch the next defect of this
class, because every one of these statements *looked* right: what they had in common
was that nobody had ever handed them to IRIS. This file does that. It asserts nothing
about the rows — the container's content is not the subject — only that each statement
survives Prepare and Query Open.

A fifth defect surfaced only here, because only here does the statement reach the
server: `ivg.retrieve`'s vector arm called IRIS's native `EMBEDDING(text, config)` with
a config name that defaults to blank, and this namespace has no `%Embedding.Config` at
all — `SQLCODE -280 %Embedding.Config ' ' does not exist`. It cannot be given one
either: `sentence_transformers` is not importable inside the instance. The arm now asks
the engine for the query vector (`_retrieve_query_vector`, covered by
tests/unit/test_227_retrieve_embedding_source.py), so this file threads a real
`IRISGraphEngine` — every path that executes Cypher does the same — and `-280` is
listed below as a failure, not tolerated.
"""

import os

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import set_schema_prefix, translate_to_sql

iris = pytest.importorskip("iris")

#: Errors that mean the statement is wrong, listed by the code IRIS reports. A test that
#: only checked "did it raise" would have passed against three of the four defects above,
#: since each one raised something different at a different stage.
TRANSLATOR_ERRORS = (
    "-1>",  # syntax: the `ORDER BY` in a CTE without `TOP`
    "-29>",  # field not found: the fusion's columns read out of the wrong CTE
    "-359>",  # function not found
    "-280>",  # the vector arm asked IRIS to embed with a config this namespace lacks
    "-400>",  # codegen fault: `ORDER BY` an uncast vector alias
    "ARGUMENT ERROR",  # a bind inside JSON_TABLE's source argument
    "PARAMETER ERROR",
)

STATEMENTS = {
    "vector.search": (
        "CALL ivg.vector.search('Gene', 'embedding', {vector}, 3) "
        "YIELD node, score RETURN node, score"
    ),
    "bm25.search": (
        "CALL ivg.bm25.search('default', 'aspirin', 5) YIELD node, score RETURN node, score"
    ),
    "ppr": "CALL ivg.ppr(['ivg:a'], 0.85, 20) YIELD node, score RETURN node, score",
    "retrieve": ("CALL ivg.retrieve('aspirin', 5) YIELD node, rrf_score RETURN node, rrf_score"),
    "retrieve-labelled": (
        "CALL ivg.retrieve('aspirin', 5, 'default', 'Drug') YIELD node, rrf_score "
        "RETURN node, rrf_score"
    ),
}


@pytest.fixture(scope="module")
def connection():
    port = int(os.environ.get("IVG_PORT", "31972"))
    try:
        conn = iris.connect(
            hostname=os.environ.get("IVG_HOST", "localhost"),
            port=port,
            namespace=os.environ.get("IVG_NAMESPACE", "USER"),
            username=os.environ.get("IVG_USER", "_SYSTEM"),
            password=os.environ.get("IVG_PASSWORD", "SYS"),
        )
    except Exception as exc:  # pragma: no cover - container not running
        pytest.skip(f"no IRIS on port {port}: {exc}")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def embedding_width(connection):
    """The embedding column's declared width.

    IRIS checks the width at Query Open even when the table is empty (SQLCODE -257), so a
    fixed-width vector here would fail on any container whose column is declared at a
    different width — which is a fact about the container, not about the statement.
    """
    from iris_vector_graph.schema import GraphSchema

    cursor = connection.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cursor, "kg_NodeEmbeddings")
    finally:
        cursor.close()
    if not width:
        pytest.skip("kg_NodeEmbeddings.emb has no declared width in this namespace")
    return int(width)


@pytest.fixture(scope="module")
def query_vector(embedding_width):
    """A query vector of the column's width, as a Cypher list literal."""
    return "[" + ", ".join(["0.1"] * embedding_width) + "]"


@pytest.fixture(scope="module")
def engine(connection, embedding_width):
    """An engine that can embed, which is what `ivg.retrieve`'s vector arm asks for.

    The embedder returns a fixed vector of the column's width: the subject here is
    whether the emitted statement runs, so the values carry no meaning — only the width
    does, and that is read off the column rather than assumed.
    """
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(
        connection,
        embedder=lambda text: [0.1] * embedding_width,
        vector_dtype="DOUBLE",
        schema_prefix="Graph_KG",
    )


def _translated(cypher: str, engine=None):
    set_schema_prefix("Graph_KG")
    translated = translate_to_sql(parse_query(cypher), None, engine=engine)
    sql = translated.sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    params = translated.parameters or []
    # `parameters` is a list of per-statement lists; these are all single statements.
    if len(params) == 1 and isinstance(params[0], (list, tuple)):
        params = list(params[0])
    return sql, list(params)


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(STATEMENTS))
def test_the_statement_survives_prepare_and_open(name, connection, query_vector, engine):
    sql, params = _translated(STATEMENTS[name].format(vector=query_vector), engine=engine)
    cursor = connection.cursor()

    try:
        cursor.execute(sql, params) if params else cursor.execute(sql)
        cursor.fetchall()
    except Exception as exc:
        message = " ".join(str(exc).split())
        offenders = [code for code in TRANSLATOR_ERRORS if code in message]
        assert offenders == [], f"{name} emitted a statement IRIS rejects {offenders}: {message}"
        raise
    finally:
        cursor.close()
