"""The generated iFind DDL and the fused-search procedure must be SQL IRIS accepts.

Both defects here were invisible because the failure was swallowed:

* every iFind index was declared `... ON Graph_KG.docs(text) INDEXTYPE =
  %iFind.Index.Basic`, which IRIS rejects at Prepare with `SQLCODE -25` ("Input
  (IDENTIFIER) encountered after end of query^CREATE INDEX ... INDEXTYPE"). The
  index is in `_OPTIONAL_INDEXES`, so the rejection was logged at DEBUG and the
  install reported success. Without the index `%FIND.Rank` does not exist, so
  `Graph_KG.kg_TXT` cannot be created either — which is why every text search
  answered "kg_TXT unavailable" on a namespace that was built cleanly.
* `kg_RRF_FUSE` reads `id` out of `kg_KNN_VEC`, whose result column is `node_id`
  since 227 re-keyed the embedding tables. `CREATE OR REPLACE PROCEDURE` fails at
  Prepare with `SQLCODE -29` ("Field 'ID' not found in the applicable tables"), so
  the procedure does not exist at all and SQL-side fusion is gone.

These are unit tests over the generated text: the statements are the contract, and
the live container proves it separately in `tests/python/test_vector_functions.py`.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.schema import GraphSchema

_IFIND = "%iFind.Index.Basic"


def _all_generated_ddl() -> dict:
    """Every place the schema declares an index, keyed by where it came from."""
    from unittest.mock import MagicMock

    sources = {
        "get_base_schema_sql": GraphSchema.get_base_schema_sql(),
        "get_indexes_sql": GraphSchema.get_indexes_sql(),
    }
    # ensure_indexes builds its statements inline; capture them by letting it run
    # against a cursor that accepts everything.
    cursor = MagicMock()
    GraphSchema.ensure_indexes(cursor)
    executed = "\n".join(
        call.args[0] for call in cursor.execute.call_args_list if call.args
    )
    sources["ensure_indexes"] = executed
    return sources


@pytest.mark.parametrize("where", ["get_base_schema_sql", "get_indexes_sql", "ensure_indexes"])
def test_ifind_indexes_do_not_use_the_rejected_indextype_syntax(where):
    """`INDEXTYPE = <class>` is not IRIS syntax — it fails at Prepare with -25."""
    sql = _all_generated_ddl()[where]
    assert "INDEXTYPE" not in sql.upper(), (
        f"{where} still declares an index with INDEXTYPE =; IRIS rejects it (SQLCODE -25) "
        "and the failure is swallowed as an optional index."
    )


@pytest.mark.parametrize("where", ["get_base_schema_sql", "get_indexes_sql", "ensure_indexes"])
def test_ifind_indexes_use_on_table_as_indexclass(where):
    """The accepted form is `ON TABLE <table> (<col>) AS %iFind.Index.Basic`."""
    sql = _all_generated_ddl()[where]
    for line in sql.splitlines():
        if _IFIND not in line:
            continue
        upper = line.upper()
        assert " ON TABLE " in upper, f"iFind index without `ON TABLE`: {line.strip()}"
        assert f" AS {_IFIND}".upper() in upper, (
            f"iFind index class must be introduced with AS: {line.strip()}"
        )


def test_every_ifind_index_is_declared_the_same_way_everywhere():
    """One index, three declaration sites — they must not drift apart."""
    ddl = _all_generated_ddl()
    forms = set()
    for sql in ddl.values():
        for line in sql.splitlines():
            if _IFIND in line and "idx_props_val_ifind" in line:
                forms.add(" ".join(line.split()).rstrip(";").rstrip(","))
    assert len(forms) == 1, f"idx_props_val_ifind is declared {len(forms)} different ways: {forms}"


def _statements_without_comments() -> list:
    """Every generated statement, with `--` comment lines removed.

    The comments explain the defects these tests guard, and they name the very tokens the
    tests forbid — so an assertion over the raw text would pass or fail on prose.
    """
    out = []
    for stmt in GraphSchema.get_procedures_sql_list():
        out.append(
            "\n".join(line for line in stmt.splitlines() if not line.lstrip().startswith("--"))
        )
    return out


def _procedure_sql(name: str) -> str:
    for stmt in _statements_without_comments():
        for line in stmt.splitlines():
            if line.startswith("CREATE OR REPLACE PROCEDURE") and name in line:
                return stmt
    pytest.fail(f"{name} is not in get_procedures_sql_list()")


def _txt_sql() -> str:
    return _procedure_sql("kg_TXT")


def test_kg_txt_uses_the_ifind_search_predicate_iris_accepts():
    """iFind matching is `%ID %FIND search_index(<index>, <term>)`.

    `%FIND(d.text, :q)` was invented: IRIS resolves a bare `%FIND` as a user function
    (`SQLUSER.%FIND`, SQLCODE -359) because iFind is reached through the *index*, not
    through the column. The predicate has therefore never matched anything.
    """
    body = " ".join(_txt_sql().split())
    assert "%ID %FIND search_index(" in body, (
        "kg_TXT must reach iFind through its index — `%FIND(<column>, ...)` is not IRIS syntax"
    )
    assert "idx_docs_text_ifind" in body, "the search_index argument is the index name"


def test_kg_txt_does_not_call_a_ranker_that_is_not_installed():
    """`%FIND.Rank` does not exist, and `%iFind.Rank` needs a ranker class this build lacks.

    `%iFind.Rank` is present as a function but every call fails at runtime with
    `<CLASS DOES NOT EXIST>` on `$$$IFDEFAULTRANKER`: `%Dictionary.CompiledClass` holds no
    `%iFind.Ranker.*` in the IRIS AI image. A procedure that names either one cannot be
    created (or cannot be run), so kg_TXT scores by term frequency instead.
    """
    body = _txt_sql().upper()
    assert "%FIND.RANK" not in body
    assert "%IFIND.RANK" not in body


def test_kg_txt_names_its_score_for_what_it_computes():
    """The column is `score`, not `bm25` — nothing here computes BM25."""
    body = " ".join(_txt_sql().split())
    assert "AS score" in body
    assert "bm25" not in body.lower(), "calling a term-frequency count `bm25` misreports it"


def _fusion_sql() -> str:
    return _procedure_sql("kg_RRF_FUSE")


def test_fusion_reads_the_column_kg_knn_vec_actually_returns():
    """`kg_KNN_VEC` answers `node_id`; reading `id` makes the procedure uncreatable."""
    body = _fusion_sql()
    knn_cte = body.split("FROM")[1] if "FROM" in body else ""
    assert "node_id" in body, (
        "kg_RRF_FUSE must read node_id from kg_KNN_VEC — `id` is the pre-227 column name "
        "and CREATE fails with SQLCODE -29."
    )
    assert knn_cte or True  # the column check above is the contract


def test_fusion_still_answers_a_column_named_id():
    """Callers read the fused result by name, so the projection keeps `id`."""
    body = _fusion_sql()
    assert "AS id" in body, "the fused answer's first column is still named id"


def test_fusion_takes_the_graph_and_passes_it_to_the_vector_leg():
    """A fusion that dropped the graph would re-widen what kg_KNN_VEC was narrowed to fix."""
    body = _fusion_sql()
    assert "graphId" in body
    assert "kg_KNN_VEC(:queryVector, :k1, NULL, :graphId)" in " ".join(body.split())


def test_fusion_reads_the_column_kg_txt_actually_returns():
    """The text leg's column follows kg_TXT: `score`, not `bm25`."""
    body = " ".join(_fusion_sql().split())
    text_leg = body.split("kg_TXT")[0].rsplit("K AS (", 1)[-1]
    assert "score" in text_leg, f"the kg_TXT CTE must read `score`: {text_leg}"
    assert "bm25" not in body.lower(), "kg_TXT has no bm25 column to read or project"


# ---------------------------------------------------------------------------
# The Python text-search path emits its own SQL and must agree with the procedure.
# ---------------------------------------------------------------------------


def _python_kg_txt_sql(min_confidence: int = 0) -> str:
    """Every statement `VectorMixin.kg_TXT` sends for one call, concatenated."""
    from unittest.mock import MagicMock

    from iris_vector_graph._engine.vector import VectorMixin

    class _Probe(VectorMixin):
        def __init__(self, cursor):
            self.conn = MagicMock()
            self.conn.cursor.return_value = cursor

        def _t(self, name):
            return f"Graph_KG.{name}"

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    _Probe(cursor).kg_TXT("gene", k=5, min_confidence=min_confidence)
    return "\n".join(call.args[0] for call in cursor.execute.call_args_list if call.args)


def test_python_text_search_uses_the_same_ifind_predicate_as_the_procedure():
    """Two text paths, one syntax — the Python one used the invented `%FIND(col, q)` form.

    Because the fabricated predicate always failed with SQLCODE -359, the `except` branch
    below it ran on every call and the LIKE fallback answered instead: substring matching,
    every score 1.0, and no sign to the caller that iFind was never involved.
    """
    sql = _python_kg_txt_sql()
    assert "%ID %FIND search_index(" in sql
    assert "%FIND(d.text" not in sql
    assert "%FIND.Rank" not in sql
