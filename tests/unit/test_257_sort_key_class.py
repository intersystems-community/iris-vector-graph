"""Spec 257: the native executor's sort key is SQLUser.CY_SORT_KEY's code, in a class of its own.

The SQL path orders by the UDF CY_SORT_KEY; the executor computed the same key through the UDF's generated class,
`##class(User.funcCYSORTKEY).CYSORTKEY`, and the call costs 44.7 us where the same code as a plain class method costs
1.8 us (SF1, an SNB timestamp): IC2 files ~10,000 matches under that key. Graph.KG.Cypher.SortKey.Key holds the UDF's
body; this test holds the two to the same code, outside string literals token for token, so a change to one that is
not made to the other fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ddl_body() -> str:
    src = (ROOT / "iris_vector_graph" / "schema.py").read_text()
    line = next(x for x in src.splitlines() if "FUNCTION SQLUser.CY_SORT_KEY(" in x)
    body = line[line.index("LANGUAGE OBJECTSCRIPT {") + len("LANGUAGE OBJECTSCRIPT {") : line.rindex("}")]
    return body.replace("##class(User.funcCYSORTKEY).CYSORTKEY", "..Key")


def _class_body() -> str:
    src = (ROOT / "iris_src" / "src" / "Graph" / "KG" / "Cypher" / "SortKey.cls").read_text()
    m = re.search(r"ClassMethod Key\(v As %String, t As %String\) As %String\s*\{\n(.*?)\n\}\n", src, re.S)
    assert m, "Graph.KG.Cypher.SortKey.Key not found"
    return "\n".join(x for x in m.group(1).splitlines() if not x.strip().startswith("//"))


def _tokens(code: str) -> list:
    """String literals kept whole; everything else with its whitespace removed."""
    out, i = [], 0
    while i < len(code):
        if code[i] == '"':
            j = i + 1
            while j < len(code):
                if code[j] == '"' and code[j + 1 : j + 2] == '"':
                    j += 2
                    continue
                if code[j] == '"':
                    break
                j += 1
            out.append(code[i : j + 1])
            i = j + 1
            continue
        if not code[i].isspace():
            out.append(code[i])
        i += 1
    return out


def test_the_class_holds_the_udfs_code():
    assert _tokens(_class_body()) == _tokens(_ddl_body())


def test_the_executor_calls_the_class_not_the_udf():
    src = (ROOT / "iris_src" / "src" / "Graph" / "KG" / "Cypher" / "Exec.cls").read_text()
    assert "User.funcCYSORTKEY" not in src
    assert src.count("##class(Graph.KG.Cypher.SortKey).Key(") >= 3
