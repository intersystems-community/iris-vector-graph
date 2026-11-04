"""4.6.0 R166: a graph-algorithm SQL function whose method answers `ERROR:...` raises it, instead of returning it.

`CALL ivg.kcore({}) YIELD node, coreness` reads `kg_KCore()` through `JSON_TABLE`. With `^NKG` not built,
`Graph.KG.Communities.KCoreJson` answers the text `ERROR:^NKG not built`, which is not JSON, and `JSON_TABLE` made one
row of NULLs of it: the CALL answered `[[None, None]]` and no error. kcore, scc, triangleCount, leiden, betweenness,
closeness and eigenvector answer that way. Each such function now throws the text, so the statement fails with it.

Which functions those are is read from the source here, not listed by hand: each `kg_*` function's DDL names the
class method it calls, and the method (or one it delegates to in `iris_src`) is searched for `"ERROR:`. A new
function over such a method fails `test_every_function_over_an_error_answer_raises` until it raises too.

The live answer (kcore with `^NKG` dropped) is in `tests/e2e/test_460_algo_function_errors_e2e.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris_vector_graph.schema import GraphSchema

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"

#: what the source reading below finds; pinned so that a change to the reading itself is seen
EXPECTED = {"kg_Betweenness", "kg_Closeness", "kg_Eigenvector", "kg_Leiden", "kg_TriangleCount", "kg_SCC", "kg_KCore"}

RAISES = '##class(%Exception.General).%New("IVG","5001",,result)'


def _functions() -> dict:
    out = {}
    for ddl in GraphSchema.get_procedures_sql_list():
        m = re.search(r"CREATE OR REPLACE FUNCTION \S+?\.(kg_\w+)\(", ddl)
        if m:
            out[m.group(1)] = ddl
    return out


def _method_body(cls: str, method: str) -> str:
    path = SRC / (cls.replace(".", "/") + ".cls")
    text = path.read_text()
    m = re.search(rf"^ClassMethod {method}\(.*?^\}}", text, re.M | re.S)
    assert m, f"{cls}.{method} not found in {path}"
    return m.group(0)


def _can_answer_error(cls: str, method: str, depth: int = 3) -> bool:
    body = _method_body(cls, method)
    if '"ERROR:' in body:
        return True
    if depth == 0:
        return False
    calls = [(cls, name) for name in re.findall(r"\.\.(\w+)\(", body)]
    calls += re.findall(r"##class\(([\w.]+)\)\.(\w+)\(", body)
    return any(_can_answer_error(c, n, depth - 1) for c, n in calls if (SRC / (c.replace(".", "/") + ".cls")).exists())


def _answers_error(ddl: str) -> bool:
    # the method whose answer the function returns (4.6.0 R196 put a RefuseMergedGraphs call before it in seven)
    call = re.search(r"set result = ##class\(([\w.]+)\)\.(\w+)\(", ddl)
    return bool(call) and _can_answer_error(call.group(1), call.group(2))


def test_the_source_reading_finds_the_seven():
    assert {name for name, ddl in _functions().items() if _answers_error(ddl)} == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_function_over_an_error_answer_raises(name):
    ddl = _functions()[name]
    assert '$extract(result,1,6)="ERROR:"' in ddl and RAISES in ddl, ddl
    # raised before the "OK:" prefix is stripped and the text returned
    assert ddl.index(RAISES) < ddl.index("quit result"), ddl


@pytest.mark.parametrize("name", sorted(set(_functions()) - EXPECTED))
def test_a_function_that_cannot_answer_an_error_is_unchanged(name):
    assert RAISES not in _functions()[name]


#: what IRIS answers for `SELECT kg_KCore(10)` with ^NKG dropped, once the function throws (IVG460FXC, 2026-11-04)
THROWN = (
    "<SQL ERROR>; Details: [SQLCODE: <-149>:<SQL Function encountered an error>]\n[Location: <ServerLoop - Query "
    "Open()>]\n[%msg: <SQL Function GRAPH_KG.KG_KCORE failed with error:  SQLCODE=-400,%msg=ERROR:^NKG not built>]"
)


def _engine_whose_kcore_throws():
    from unittest.mock import MagicMock

    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None

    def execute(sql, *args):
        if "kg_KCore" in sql:
            raise RuntimeError(THROWN)  # the trigger: the statement fails with the function's text

    cursor.execute.side_effect = execute
    return IRISGraphEngine(conn, embedding_dimension=4)


def test_the_thrown_text_is_the_cypher_error():
    r = _engine_whose_kcore_throws().execute_cypher("CALL ivg.kcore({}) YIELD node, coreness RETURN node, coreness")
    assert r.rows == [] and "ERROR:^NKG not built" in (r.error or ""), (r.rows, r.error)


def test_raise_on_error_raises_the_thrown_text():
    with pytest.raises(RuntimeError, match=r"ERROR:\^NKG not built"):
        _engine_whose_kcore_throws().execute_cypher(
            "CALL ivg.kcore({}) YIELD node, coreness RETURN node, coreness", raise_on_error=True
        )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_raise_is_ddl_the_dbapi_accepts(name):
    """A `'`, `?` or `:<digit>` in a LANGUAGE OBJECTSCRIPT body breaks DB-API DDL tokenizing (")QC" errors)."""
    ddl = _functions()[name]
    body = ddl[ddl.index("LANGUAGE OBJECTSCRIPT"):]
    assert "'" not in body and "?" not in body and not re.search(r":\d", body), body
