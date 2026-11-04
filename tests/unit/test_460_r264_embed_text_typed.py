"""4.6.0 R264: the properties `embed_nodes` hands a `text_fn` are read typed (as R218 reads `get_node`'s), so the text a
vector is built from does not depend on the driver.

`rdf_props.val` is VARCHAR, and a Cypher write stores a number there as an IRIS number. intersystems-irispython 5.4.0
reads `SET n.score = 0.95` as the float 0.95, 5.4.1 as IRIS's text `'.94999999999999995559'`, so before R264 the same
stored node gave `text_fn` two different values and the vector two different texts. `CY_TYPED(val)` marks a number
(`\\x01D.94999999999999995559`), `decode_typed_text` reads the mark, and both drivers give 0.95.

The fake cursor below answers the property read as the server would: through `CY_TYPED` the marked text, which neither
driver changes (it is a string); without it, what 5.4.1 decodes the plain column to. The E2E twin is
tests/e2e/test_460_r264_embed_text_typed_e2e.py, run under both drivers by scripts/dev/driver_matrix.sh.
"""

from __future__ import annotations

from unittest.mock import MagicMock

#: What CY_TYPED(val) answers for a Cypher-written 0.95, an integer 29, and an API-written string.
TYPED = {"score": "\x01D.94999999999999995559", "count": "\x01N29", "name": "BRCA1"}
#: What 5.4.1 answers for the plain column (the text a 4.5.x re-embed under 5.4.1 built from).
RAW_541 = {"score": ".94999999999999995559", "count": "29", "name": "BRCA1"}


class _Cursor:
    def __init__(self):
        self.sql: list[str] = []
        self._rows: list = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "rdf_props" in sql:
            source = TYPED if "CY_TYPED(val)" in sql else RAW_541
            self._rows = [("n1", k, v) for k, v in source.items()]
        elif "FROM" in sql and "nodes" in sql:
            self._rows = [("n1",)]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _engine(cursor):
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    conn.cursor.return_value = cursor
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng.embedder = None
    eng.embedding_config = None
    eng.embedding_dimension = 3
    eng._connection_params = None
    return eng


def test_text_fn_gets_the_stored_types_not_the_driver_text(monkeypatch):
    cursor = _Cursor()
    eng = _engine(cursor)
    seen: dict = {}
    texts: list[str] = []

    def text_fn(node_id, props):
        seen.update(props)
        return f"{props['name']} score={props['score']} count={props['count']}"

    monkeypatch.setattr(eng, "_route_for_read", lambda graph_id, model_key=None: (None, None))
    monkeypatch.setattr(eng, "embed_text", lambda t: texts.append(t) or [0.1, 0.2, 0.3])
    monkeypatch.setattr(eng, "store_embeddings", lambda items, **kw: len(items), raising=True)
    eng.embed_nodes(text_fn=text_fn, force=True)

    assert seen == {"score": 0.95, "count": 29, "name": "BRCA1"}
    assert [type(seen[k]) for k in ("score", "count", "name")] == [float, int, str]
    assert texts == ["BRCA1 score=0.95 count=29"]
    prop_reads = [s for s in cursor.sql if "rdf_props" in s]
    assert len(prop_reads) == 1 and "Graph_KG.CY_TYPED(val)" in prop_reads[0], prop_reads
