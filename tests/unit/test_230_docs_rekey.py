"""Spec 230 US2 (FR-007) — `Graph_KG.docs` names a graph and a node.

`Graph_KG.docs` shipped as

    CREATE TABLE Graph_KG.docs(
      id    VARCHAR(256) %EXACT PRIMARY KEY,
      text  VARCHAR(4000) %EXACT
    );

Two things are wrong with that once a namespace holds more than one graph.

**No graph.** The text leg of hybrid search reads this table, so a scoped search
answered its own graph's vectors alongside every graph's documents. There was
nothing to filter on, which is why `kg_RRF_FUSE` carried a comment saying so
rather than a predicate.

**The wrong key space.** `id` was a free-form document ID while the vector leg
answers node IDs, so the `FULL OUTER JOIN` that fuses the two legs could never
match a row: every "fused" result was one leg or the other. Re-keying `id` to a
node ID is what makes fusion able to fuse (FR-008), and it is the reason the
migration has to place or quarantine every existing row rather than default it
(FR-018) — a document ID that names no node is not a node ID.

These tests read the DDL, not a database: the guarantee is in the column
declarations. `tests/e2e/test_230_retrieval_scope.py` is where the placement is
proven against IRIS.
"""

from __future__ import annotations

import re

from iris_vector_graph.schema import GraphSchema

#: Where the index has to be declared as well as in the base schema: an install
#: that already has the table gets its indexes from `ensure_indexes`, not from
#: `get_base_schema_sql`, and an index only the fresh path creates is an index
#: half the installations do not have.
GRAPH_INDEX = "idx_docs_graph"


def _statement(table: str) -> str:
    """The `CREATE TABLE <table>( ... )` statement, whitespace collapsed."""
    sql = GraphSchema.get_base_schema_sql(embedding_dimension=8)
    match = re.search(
        rf"CREATE TABLE (?:IF NOT EXISTS )?{re.escape(table)}\s*\((.*?)\n\);",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"{table} is not declared in the base schema"
    return " ".join(match.group(1).split())


def _docs() -> str:
    return _statement("Graph_KG.docs")


def test_docs_declares_graph_id():
    body = _docs()

    assert re.search(r"graph_id\s+VARCHAR\(256\)\s+%EXACT\s+NOT NULL\s+DEFAULT\s+''", body), (
        "Graph_KG.docs carries no graph_id, so the text leg of every hybrid search "
        f"reads every graph's documents:\n{body}"
    )


def test_docs_graph_id_is_exact_like_every_other_graph_column():
    """`%EXACT` or two spellings of one graph ID stop being one graph.

    IRIS's default collation upper-cases for comparison, so `tenant-A` and
    `TENANT-A` would collide here while remaining two graphs in `nodes`.
    """
    body = _docs()
    declaration = next(
        (part for part in body.split(",") if part.strip().startswith("graph_id")), ""
    )

    assert "%EXACT" in declaration, (
        f"docs.graph_id is not %EXACT while nodes.graph_id is: {declaration!r}"
    )


def test_docs_id_is_no_longer_unique_on_its_own():
    """The same node in two graphs has two documents.

    A bare `id ... PRIMARY KEY` says a node ID appears in at most one graph's
    documents — the same assumption spec 227 broke on `nodes`.
    """
    body = _docs()

    assert not re.search(r"\bid\s+VARCHAR\(256\)\s+%EXACT\s+PRIMARY KEY", body), (
        "docs.id is still a standalone primary key, so a node that exists in two "
        f"graphs can carry text in only one of them:\n{body}"
    )
    assert re.search(r"PRIMARY KEY\s*\(\s*graph_id\s*,\s*id\s*\)", body), (
        f"docs has no (graph_id, id) primary key:\n{body}"
    )


def test_docs_id_is_documented_as_a_node_id():
    """Nothing in SQL says what key space a VARCHAR holds, so the comment does.

    Without it the next reader has only the column name, and the name is the one
    that was wrong: `id` was a document ID for every release up to 4.0.0.
    """
    sql = GraphSchema.get_base_schema_sql(embedding_dimension=8)
    preamble = sql[: sql.index("CREATE TABLE Graph_KG.docs")]
    comment = preamble[preamble.rindex("\n\n") :]

    assert "node" in comment.lower(), (
        "the docs DDL has no comment saying `id` is a node ID, which is the whole "
        f"of FR-007's contract and is not expressible in the column type:\n{comment}"
    )


def test_the_ifind_index_survives_the_rekey():
    """`kg_TXT` reaches iFind through this index by name; losing it loses the leg."""
    sql = GraphSchema.get_base_schema_sql(embedding_dimension=8)

    assert "idx_docs_text_ifind" in sql, sql[-2000:]


def test_the_graph_index_is_declared_for_a_fresh_install():
    sql = GraphSchema.get_base_schema_sql(embedding_dimension=8)

    assert re.search(
        rf"CREATE INDEX (?:IF NOT EXISTS )?{GRAPH_INDEX} ON Graph_KG\.docs\s*\(\s*graph_id\s*\)",
        sql,
        re.IGNORECASE,
    ), f"no {GRAPH_INDEX} in the base schema"


def test_the_graph_index_is_declared_for_an_existing_install():
    """`ensure_indexes` is the only path an upgraded database takes."""
    names = [name for name, _ in _ensure_indexes_entries()]

    assert GRAPH_INDEX in names, (
        f"{GRAPH_INDEX} is missing from ensure_indexes, so an upgraded install "
        f"filters docs by graph without an index: {names}"
    )


def _ensure_indexes_entries():
    """The `(name, sql)` pairs `ensure_indexes` would execute, without a database."""
    executed = []

    class _Cursor:
        def execute(self, sql, params=None):
            executed.append(str(sql))

    GraphSchema.ensure_indexes(_Cursor())
    out = []
    for sql in executed:
        match = re.search(r"CREATE INDEX (?:IF NOT EXISTS )?(\w+)", sql, re.IGNORECASE)
        out.append((match.group(1) if match else "", sql))
    return out


def test_the_graph_index_names_the_graph_column():
    sql = dict((name, sql) for name, sql in _ensure_indexes_entries()).get(GRAPH_INDEX, "")

    assert "graph_id" in sql, f"{GRAPH_INDEX} does not index graph_id: {sql!r}"
