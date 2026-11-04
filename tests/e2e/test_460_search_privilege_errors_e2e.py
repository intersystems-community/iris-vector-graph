"""4.6.0 R143: a `/kg` search by a user who may not read one of the tables it reads raises, naming the table.

`kg_KNN_VEC`, `kg_TXT` and `kg_RRF_FUSE` read with the caller's privileges, and IRIS checks a table the procedure
reads when the first row is fetched: `%Next` returns 0 with SQLCODE -99 ("User X is not privileged for the
operation", no table named). `Graph.KG.PyOps.VectorSearch` and `HybridSearch` read the rows through `iris.sql.exec`,
whose iteration stops there as if no row were left and leaves the -99 on `ResultSet.%SQLCODE`, so the route answered
`[]`. A user without SELECT on `kg_NodeEmbeddings` got "Query embedding dimension 8 does not match expected 768"
instead: INFORMATION_SCHEMA shows the table's column to no one without a privilege on it, and the width fell back to
768. Both now raise `PermissionError` naming what the user lacks. Over DB-API the procedures already raised -99 (at
fetch), so the procedure bodies swallow nothing; that is checked too.

Also: `VectorSearch` with no label passed Python `None` as the label, which `iris.sql.exec` binds so that
`:labelFilter IS NULL` is false and `L.label = :labelFilter` matches nothing, so `/vectorSearch` without a label
answered `[]` for every user. It passes "".

Data, by hand, in the default graph of the namespace emptied first (IVG Schema RAG, width 8): r143:a [1,0,...],
r143:b [0,1,0,...], r143:c [1,1,0,...], all labelled R143; one document, r143:b's, "zebra crossing".
- VectorSearch([1,0,...], k 2, label R143 or none): cosines a 1, c 1/sqrt(2), b 0: [a 1.0, c 0.707107].
- HybridSearch([1,0,...], "zebra", k 3, c 60): vector ranks a 1, c 2, b 3; text ranks b 1. A row missing from a leg
  ranks 1e9. rrf b = 1/63 + 1/61, a = 1/61 + 1/(60 + 1e9), c = 1/62 + 1/(60 + 1e9): b, a, c.

The readers (tests/e2e/readers460.py) hold the `/kg` role's grants, or all of them but one table.
"""

from __future__ import annotations

import contextlib
import json
import math

import pytest

from tests.e2e import ns460alt as alt
from tests.e2e import readers460 as readers

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

VECTORS = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [1.0, 1.0]}
MISSING = 1 / (60 + 1_000_000_000)
VECTOR_SEARCH = '##class(Graph.KG.PyOps).VectorSearch(v,2,"R143","")'
VECTOR_SEARCH_NO_LABEL = '##class(Graph.KG.PyOps).VectorSearch(v,2,"","")'
HYBRID_SEARCH = '##class(Graph.KG.PyOps).HybridSearch(v,"zebra",3,60,"")'
VECTOR_ANSWER = [("r143:a", 1.0), ("r143:c", round(1 / math.sqrt(2), 6))]
HYBRID_ANSWER = [("r143:b", round(1 / 63 + 1 / 61, 9)), ("r143:a", round(1 / 61 + MISSING, 9)),
                 ("r143:c", round(1 / 62 + MISSING, 9))]
#: The tables each search reads, so the ones whose missing grant it must name.
READS = {VECTOR_SEARCH: ("kg_NodeEmbeddings", "rdf_labels"), HYBRID_SEARCH: ("kg_NodeEmbeddings", "rdf_labels", "docs")}
OMITTED = ("kg_NodeEmbeddings", "rdf_labels", "docs")


def _tag(table: str) -> str:
    return "R143_NO_" + table.upper()


@pytest.fixture(scope="module")
def data():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for k, vec in VECTORS.items():
        engine.create_node(f"r143:{k}", labels=["R143"])
        assert engine.store_embedding(f"r143:{k}", vec + [0.0] * (alt.DIM - 2))
    conn.cursor().execute(f"INSERT INTO {alt.SCHEMA}.docs (graph_id, id, text) VALUES ('', 'r143:b', 'zebra crossing')")
    conn.commit()
    users = {"full": readers.make("R143_FULL")}
    users.update({t: readers.make(_tag(t), omit=(t,)) for t in OMITTED})
    yield users
    for tag in ["R143_FULL", *(_tag(t) for t in OMITTED)]:
        readers.drop(tag)
    conn.cursor().execute(f"DELETE FROM {alt.SCHEMA}.docs WHERE id = 'r143:b'")
    for k in VECTORS:
        with contextlib.suppress(Exception):
            engine.delete_node(f"r143:{k}", graph=None)
    conn.commit()
    conn.close()


def _rows(text: str, digits: int) -> list:
    assert not text.startswith("EXC"), text
    return [(r["id"], round(r["score"], digits)) for r in json.loads(text)]


def test_a_reader_with_every_grant_gets_the_vector_answer(data):
    assert _rows(readers.call(data["full"], VECTOR_SEARCH), 6) == VECTOR_ANSWER


def test_a_vector_search_with_no_label_gets_the_same_answer(data):
    assert _rows(readers.call(data["full"], VECTOR_SEARCH_NO_LABEL), 6) == VECTOR_ANSWER


def test_a_reader_with_every_grant_gets_the_hybrid_answer(data):
    assert _rows(readers.call(data["full"], HYBRID_SEARCH), 9) == HYBRID_ANSWER


@pytest.mark.parametrize("table", OMITTED)
@pytest.mark.parametrize("search", [VECTOR_SEARCH, HYBRID_SEARCH], ids=["vectorSearch", "hybridSearch"])
def test_a_reader_missing_one_grant_gets_a_privilege_error_naming_the_table(data, search, table):
    out = readers.call(data[table], search)
    if table not in READS[search]:
        # A table the search does not read: its answer is unchanged.
        assert _rows(out, 6) == VECTOR_ANSWER, out
        return
    assert out.startswith("EXC"), out
    assert "PermissionError" in out, out
    assert f"SELECT on {alt.SCHEMA}.{table}" in out, out


def test_over_db_api_the_procedure_itself_reports_the_missing_grant(data):
    """The procedure body swallows nothing: the -99 reaches a DB-API caller at fetch."""
    import iris

    _, user = readers.names(_tag("docs"))
    conn = iris.connect("localhost", alt.PORT, alt.NS, user, readers.PASSWORD)
    try:
        cur = conn.cursor()
        with pytest.raises(Exception, match="-99"):
            cur.execute(f"SELECT id, score FROM {alt.SCHEMA}.kg_TXT('zebra', 2, 0, '')")
            cur.fetchall()
    finally:
        conn.close()
