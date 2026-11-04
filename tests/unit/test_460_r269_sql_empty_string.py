"""4.6.0 R269, pinned against the source (no container): the SQL-callable methods that read SQL's `''` (which
arrives as `$Char(0)`) as a one-character string now read it as the empty string.

The answers are proven against the server in tests/e2e/test_460_r269_sql_empty_string_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

KG = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"


def _method(cls: str, name: str) -> str:
    text = (KG / cls).read_text()
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", text, re.S | re.M)
    assert m, f"no ClassMethod {name} in {cls}"
    return "\n".join(ln for ln in m.group(0).splitlines() if not ln.lstrip().startswith("//"))


#: (class, method): the arguments read as "" when they arrive as $Char(0)
FIXED = {
    ("EdgeScan.cls", "MatchEdges"): ("graph", "sourceId", "predicate"),
    ("EdgeScan.cls", "MatchEdgesAllGraphs"): ("sourceId", "predicate"),
    ("CypherFn.cls", "IVGREGEXMATCH"): ("s", "p"),
    ("CypherFn.cls", "IVGSTRSPLIT"): ("s",),
    ("CypherFn.cls", "IVGMAPGET"): ("key",),
    ("CypherFn.cls", "IVGJSONSET"): ("json", "key", "typ"),
    ("CypherFn.cls", "IVGJSONREMOVE"): ("key",),
}


@pytest.mark.parametrize("cls,method", sorted(FIXED))
def test_the_argument_reads_char0_as_empty(cls, method):
    body = _method(cls, method)
    for arg in FIXED[(cls, method)]:
        assert f"Set:{arg}=$Char(0) {arg} = \"\"" in body, f"{method}: {arg}"


def test_a_null_is_still_told_from_an_empty_string_where_cypher_tells_them_apart():
    """REGEXMATCH and STRSPLIT answer null ("") before $Char(0) becomes "": '' =~ 'a*' is true, null =~ x is not."""
    for method, first in (("IVGREGEXMATCH", 'If (s = "") || (p = "") { Return 0 }'),
                          ("IVGSTRSPLIT", 'If s = "" { Return "[]" }')):
        body = _method("CypherFn.cls", method)
        assert body.index(first) < body.index("Set:s=$Char(0)"), method


def test_split_by_the_empty_string_is_the_characters():
    body = _method("CypherFn.cls", "IVGSTRSPLIT")
    assert "If delim = $Char(0) {" in body and "$Extract(s, i)" in body


def test_json_set_keeps_an_empty_value_as_char0():
    """JSON_VALUE reads "\\u0000" back as SQL's '' and "" as NULL, so SET r.k = '' must store $Char(0)."""
    assert "Set:val=$Char(0)" not in _method("CypherFn.cls", "IVGJSONSET")


def test_json_remove_reads_an_empty_map_as_the_empty_map():
    assert '(json = $Char(0))' in _method("CypherFn.cls", "IVGJSONREMOVE")
