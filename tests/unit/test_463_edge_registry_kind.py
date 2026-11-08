"""4.6.3: a registry row written for `kg_EdgeEmbeddings` says kind `edge`, whichever call writes it.

4.6.0 (R316) fixed setup's adoption. `_insert_identity`, which every registry insert goes through
(`set_embedding_identity`, `enforce_embedding_identity`'s first claim, adoption, routed tables), still defaulted the
kind to `node` and named the column only for an edge route, so `set_embedding_identity(identity, "kg_EdgeEmbeddings")`
and an engine declaring a model claiming that table with no row there recorded a node route. The fake cursor below
keeps the registry's rows as IRIS would (the `kind` column defaulting to `node`), and each test reads the row back.
The live check is tests/e2e/test_463_edge_registry_kind_e2e.py.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from iris_vector_graph.embedding_identity import EmbeddingIdentity
from iris_vector_graph.engine import IRISGraphEngine

_INSERT = re.compile(r"INSERT INTO \S+ \(([^)]*)\) VALUES", re.I)


class _Registry:
    """The registry table: INSERT keeps a row (an unnamed `kind` takes the column's DEFAULT 'node')."""

    def __init__(self):
        self.rows = []

    def cursor(self):
        reg = self

        class _C:
            def execute(self, sql, params=None):
                m = _INSERT.search(sql)
                if m:
                    row = dict(zip([c.strip() for c in m.group(1).split(",")], params))
                    row.setdefault("kind", "node")
                    reg.rows.append(row)

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            def close(self):
                pass

        return _C()


IDENTITY = EmbeddingIdentity(mechanism="external", model_key="m463", declared_config=None, dimension=8,
                             dtype="DOUBLE")


def _engine():
    conn = MagicMock()
    conn.cursor.return_value = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    return IRISGraphEngine(conn, embedding_dimension=8)


def _kind_written(table: str, **kw) -> str:
    eng, reg = _engine(), _Registry()
    eng._insert_identity(reg.cursor(), table, "", IDENTITY, "claimed", **kw)
    assert len(reg.rows) == 1 and reg.rows[0]["table_name"] == table
    return reg.rows[0]["kind"]


def test_a_claim_on_the_edge_table_records_an_edge_route():
    assert _kind_written("kg_EdgeEmbeddings") == "edge"


def test_a_claim_on_a_node_table_still_records_a_node_route():
    assert _kind_written("kg_NodeEmbeddings") == "node"
    assert _kind_written("kg_NodeEmbeddings_optimized") == "node"


def test_a_routed_tables_kind_is_the_callers():
    assert _kind_written("emb_g1_m1", kind="edge") == "edge"
    assert _kind_written("emb_g1_m1", kind="node") == "node"
