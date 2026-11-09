"""`bulk_create_nodes` stores a property as the same text `create_node` does.

Found by running `docs/setup/QUICKSTART.md` against a stock container (2026-09-30):
after `bulk_create_nodes`, `RETURN d.name` answered `'"Olaparib"'`. The ObjectScript
fast path (`Graph.KG.EdgeScan.BulkIngestNodesSQL`) wrapped every string in JSON
quotes and stored a boolean as `1`, while `create_node` and the SQL fallback store
`prop_text(value)`: `Olaparib`, `true`. So Python serialises every value once, with
`prop_text`, and the class method stores the string it is given.

4.6.4: every value is `prop_value`'s, as `create_node` binds it: a number as the number (a float as its repr, named
in `doubles`, which the class stores as a $DOUBLE), anything else its `prop_text`. A string, numeric-looking or not, is
still stored as the string it is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from iris_vector_graph.engine import IRISGraphEngine

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/EdgeScan.cls"
PROPS = {"name": "Olaparib", "n": 1.5, "k": 7, "b": True, "tags": ["a", "b"], "q": 'say "hi"'}


def _payload():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    engine.capabilities.objectscript_deployed = True
    engine._iris_obj = MagicMock(return_value=MagicMock())
    with patch("iris_vector_graph.schema._call_classmethod_large", return_value=1) as large:
        engine.bulk_create_nodes([{"id": "d1", "labels": ["Drug"], "properties": PROPS}])
    return json.loads(large.call_args.args[3])


def test_every_value_arrives_as_create_node_binds_it():
    (node,) = _payload()
    # plus the id property, which the SQL path and create_node store too (R089)
    assert node["props"] == {
        "id": "d1", "name": "Olaparib", "n": "1.5", "k": 7, "b": "true", "tags": '["a", "b"]', "q": 'say "hi"'
    }
    assert node["doubles"] == ["n"]


def _string_branch():
    """The `Set valStr` line that stores a value the caller sent as text (not a number, not a `doubles` key)."""
    body = CLS.read_text().split("ClassMethod BulkIngestNodesSQL", 1)[1].split("\n}\n", 1)[0]
    (line,) = [ln for ln in body.splitlines() if "Set valStr" in ln and "$Select(" in ln]
    return body, line


def test_the_class_method_stores_the_string_it_is_given():
    body, line = _string_branch()
    assert '""""' not in line, f"strings are quoted again: {line.strip()}"
    assert "$ZConvert" not in line, f"strings are re-encoded: {line.strip()}"
    assert re.search(r"%GetTypeOf\(key\)|boolean", body), "a JSON boolean needs its own case"


def test_a_numeric_string_is_stored_as_a_string():
    # "1.5" came back as Decimal('1.5'): %GetNext hands a canonical number back as a
    # number, and embedded SQL stored it $LB-numeric. `val_""` keeps it a string.
    body, line = _string_branch()
    assert 'val_""' in line, line.strip()
    # 4.6.4: only a JSON number, or a `doubles` key's text, is stored as a number
    assert 'tType = "number"' in body and '(tType = "string") && $Data(tDoubles(key))' in body
