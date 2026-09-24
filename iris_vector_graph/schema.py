#!/usr/bin/env python3
"""
Domain-Agnostic Graph Schema Management

Provides RDF-style graph schema utilities that can be used across domains.
Extracted from the biomedical-specific implementation for reusability.
"""

import logging
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .capabilities import IRISCapabilities
from .constants import DEFAULT_EMBEDDING_DIMENSION, DEFAULT_EMBEDDING_TABLE
from .security import sanitize_identifier, validate_table_name

logger = logging.getLogger(__name__)

#: Every name a namespace may present ``rdf_edges`` under, in the order a migration
#: should try them. ``Graph_KG`` is the declared schema and the only one DDL can
#: reach; ``SQLUser.rdf_edges`` is the compatibility view IRIS generates over it;
#: the bare name resolves against the session's schema search path. Spec 230,
#: FR-004 — see :meth:`GraphSchema.tighten_graph_id_column`.
GRAPH_ID_TABLE_SPELLINGS = ("Graph_KG.rdf_edges", "SQLUser.rdf_edges", "rdf_edges")

#: The 4.0.0 ``rdf_edges`` declaration, named on its own because two callers need the
#: *same* one: the full DDL script below, and the rescue that re-creates the table
#: after deleting the v3.2.0 class that owned it. A second spelling would drift, and
#: the whole point of the rescue is that the rebuilt table matches a fresh install.
RDF_EDGES_DDL = """CREATE TABLE Graph_KG.rdf_edges(
  edge_id    BIGINT IDENTITY PRIMARY KEY,
  graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  s          VARCHAR(256) %EXACT NOT NULL,
  p          VARCHAR(128) %EXACT NOT NULL,
  o_id       VARCHAR(256) %EXACT NOT NULL,
  qualifiers %Library.DynamicObject,
  CONSTRAINT fk_edges_source FOREIGN KEY (graph_id, s) REFERENCES Graph_KG.nodes (graph_id, node_id),
  CONSTRAINT fk_edges_dest FOREIGN KEY (graph_id, o_id) REFERENCES Graph_KG.nodes (graph_id, node_id),
  CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id)
)"""

#: Where the edges wait while the class that owned them is deleted. DDL-owned, so
#: deleting ``Graph.KG.Edge`` cannot take it along; named for the version that
#: rescued them so an operator finding one left behind knows what it is.
RESCUE_STAGING_TABLE = "rdf_edges__ivg400rescue"

#: Where a rescued edge waits when it cannot go back into the 4.0.0 table: v3.2.0's FK
#: was graph-blind, so an edge may claim a graph in which its endpoint has no node row.
RESCUE_UNPLACED_TABLE = "rdf_edges__ivg400unplaced"

#: The classes that declared ``SqlTableName = rdf_edges`` and must not survive in a
#: namespace. ``Graph.KG.TestEdge`` went first; spec 227 deleted ``Graph.KG.Edge``.
STALE_EDGE_CLASSES = ("Graph.KG.Edge", "Graph.KG.TestEdge")


class RdfEdgesRescueError(RuntimeError):
    """A class-owned ``rdf_edges`` was emptied but its rows did not all come back.

    Its own type because ``initialize_schema``'s ObjectScript deploy is best-effort:
    that call site logs a failed deploy at DEBUG as "expected in Docker". A rescue
    failing there left a real 3.2.0 install with no ``Graph_KG.rdf_edges`` at all while
    ``initialize_schema`` still reported ``objectscript_deployed: True``, so this one
    exception is re-raised past the best-effort handler.
    """


def _call_classmethod(conn_or_cursor, class_name: str, method_name: str, *args) -> Any:
    if hasattr(conn_or_cursor, "_connection"):
        conn = conn_or_cursor._connection
    else:
        conn = conn_or_cursor
    import iris as _iris_pkg

    iris_obj = _iris_pkg.createIRIS(conn)
    return iris_obj.classMethodValue(class_name, method_name, *args)


def _read_large_chunks(iris_obj, cls: str, raw: str) -> str:
    """Reassemble a ``CHUNKED:<tag>:<n>`` reply from the class that staged it."""
    _, tag, n_str = raw.split(":", 2)
    n = int(n_str)
    return "".join(
        str(iris_obj.classMethodValue(cls, "ReadLargeOutChunk", tag, i)) for i in range(1, n + 1)
    )


#: The class that owns the staged BFS tree. ``^ArnoKG("bfs_r", tag, step, o)`` is read
#: back by ``Graph.KG.Traversal.ReadBFSResults``/``ReadBFSPage``, so the class that
#: answered with the marker (``Graph.KG.NKGAccel``, say) is not the class to ask.
_SORTED_BFS_READER = ("Graph.KG.Traversal", "ReadBFSResults")

#: The class that owns the staged k-hop frontier, ``^ArnoKG("khop_r", tag, dist, id)``.
_SORTED_KHOP_READER = ("Graph.KG.NKGAccel", "ReadKHopResults")


def _read_sorted_marker(iris_obj, marker: str) -> str:
    """Turn a ``SORTED:`` marker into the JSON its caller expected.

    Two producers stage their rows in a global and answer with a marker instead of
    the rows:

    ``SORTED:<tag>``
        A BFS tree in ``^ArnoKG("bfs_r", …)`` — ``NKGAccelTraversal.cls:387``,
        ``TraversalBFS.cls:294``.
    ``SORTED:<tag>:<total>:<seed>:<k>``
        A k-hop frontier in ``^ArnoKG("khop_r", …)`` — ``NKGAccelTraversal.cls:172``.
    ``SORTED:0``
        The seed has no ``^NKG`` index, so nothing was staged at all.

    Every caller of :func:`_call_classmethod_large` parses the result as JSON, so
    the marker is decoded here rather than at each call site — which is how the
    marker reached ``json.loads`` and raised ``Expecting value: line 1 column 1``.
    """
    body = marker[len("SORTED:") :]
    pieces = body.split(":")
    tag = pieces[0]
    if tag in ("", "0"):
        return "[]"

    if len(pieces) >= 4:
        # The seed may itself contain colons, so read the fixed fields from the ends.
        # `pieces[1]` is the producer's own count and is deliberately not used: see
        # `totalNodes` below.
        k_str = pieces[-1]
        seed = ":".join(pieces[2:-1])
        reader_cls, reader = _SORTED_KHOP_READER
        rows = str(iris_obj.classMethodValue(reader_cls, reader, tag))
        if rows.startswith("CHUNKED:"):
            rows = _read_large_chunks(iris_obj, reader_cls, rows)
        if rows.startswith("SORTED:"):
            rows = ""
        import json as _json

        nodes = _json.loads("[" + rows + "]") if rows.strip() else []
        try:
            hops = int(k_str)
        except ValueError:
            hops = 0
        # `totalNodes` counts the rows actually read back: the two producers disagree
        # about whether the seed counts (`NKGAccelTraversal.cls` returns `totalNodes`
        # at :78 and `totalNodes - 1` at :172), and the rows are the ground truth.
        return _json.dumps({"seed": seed, "hops": hops, "totalNodes": len(nodes), "nodes": nodes})

    reader_cls, reader = _SORTED_BFS_READER
    rows = str(iris_obj.classMethodValue(reader_cls, reader, tag))
    if rows.startswith("CHUNKED:"):
        rows = _read_large_chunks(iris_obj, reader_cls, rows)
    if rows.startswith("SORTED:") or not rows:
        # The staged tree is gone — a reader answering with a marker again is the
        # end-of-data signal `engine.py:122` breaks on, not something to decode.
        return "[]"
    return rows


def _call_classmethod_large(iris_obj, cls: str, method: str, *args) -> str:
    raw = str(iris_obj.classMethodValue(cls, method, *args))
    if raw.startswith("CHUNKED:"):
        return _read_large_chunks(iris_obj, cls, raw)
    if raw.startswith("SORTED:"):
        return _read_sorted_marker(iris_obj, raw)
    return raw


#: Every iFind index in one SQL schema, with the number of compiled helper classes it
#: can be searched through. `%FIND search_index(...)` calls a *generated* class —
#: `<owner>.<hash>`, `GeneratedBy = '<owner>.CLS'`, extending `%iFind.Find.Basic` — that
#: the compiler writes as a side effect of compiling the owner class. A second
#: `CompilePackage` over the owner's package deletes that compiled helper without
#: writing a new one, because the owner is already up to date, and the index definition
#: survives: nothing reports a problem until a query fails at Open with
#: `<CLASS DOES NOT EXIST>`. Spec 230, FR-030.
_IFIND_HELPER_SQL = (
    "SELECT i.parent, i.SqlName, "
    "(SELECT COUNT(*) FROM %Dictionary.CompiledClass c "
    " WHERE c.GeneratedBy = i.parent || '.CLS' AND c.Super LIKE '%iFind.Find%') AS helpers "
    "FROM %Dictionary.CompiledIndex i "
    "WHERE i.TypeClass LIKE '%iFind%' "
    "AND i.parent IN (SELECT Name FROM %Dictionary.CompiledClass WHERE SqlSchemaName = ?)"
)

_IFIND_HELPER_COUNT_SQL = (
    "SELECT COUNT(*) FROM %Dictionary.CompiledClass "
    "WHERE GeneratedBy = ? AND Super LIKE '%iFind.Find%'"
)


def _ifind_helper_count(cursor, owner: str) -> int:
    cursor.execute(_IFIND_HELPER_COUNT_SQL, [f"{owner}.CLS"])
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def repair_ifind_helpers(cursor, *, conn=None, schema: str = "Graph_KG") -> Dict[str, bool]:
    """Make every iFind index in `schema` searchable again, and say which are.

    Compiling the owner class on its own always regenerates the helper the package
    compile deleted, so the repair is one ``$SYSTEM.OBJ.Compile(owner, "ck-d")`` per
    index left without one, and the re-read afterwards — not the compiler's status — is
    the verdict. Runs after every deploy and after the ``^KG`` re-key, because both
    compile ``Graph.KG`` and either can leave `idx_docs_text_ifind` (the ``kg_TXT``
    leg) and `idx_props_val_ifind` (property text search) pointing at nothing.

    Args:
        cursor: A DB-API cursor, used for the two ``%Dictionary`` reads.
        conn: The connection the compile is ordered over. Pass it: a compile is a
            Native API call and ``createIRIS`` rejects a cursor outright
            (``TypeError: argument 1 must be irissdk.IRISConnection, not Cursor``),
            because a live DB-API cursor carries no reference to its connection. Only
            omit it for a wrapped cursor that does.
        schema: The SQL schema whose iFind indexes to check.

    Returns:
        ``{sql_index_name: searchable}``, one entry per iFind index found, empty when
        the server will not answer the question. Never raises: a namespace with no
        ``%Dictionary`` access, or a compile that cannot be ordered, is reported as a
        ``False`` rather than turning schema setup into a failure.
    """
    try:
        cursor.execute(_IFIND_HELPER_SQL, [schema])
        rows = list(cursor.fetchall() or [])
    except Exception as e:
        logger.debug("iFind helper check skipped: %s", e)
        return {}

    result: Dict[str, bool] = {}
    for row in rows:
        # A row this short is not an iFind index: it is a cursor answering some other
        # question, which is what a test fake or a connection replaying an earlier
        # statement hands back. Skipping beats an IndexError out of a function whose
        # contract is never to raise.
        if row is None or len(row) < 3:
            logger.debug("iFind helper check skipped a row of %r columns", row)
            continue
        owner, index_name = str(row[0]), str(row[1])
        if int(row[2] or 0):
            result[index_name] = True
            continue
        try:
            _call_classmethod(
                cursor if conn is None else conn, "%SYSTEM.OBJ", "Compile", owner, "ck-d"
            )
            searchable = _ifind_helper_count(cursor, owner) > 0
        except Exception as e:
            logger.warning(
                "%s.%s has no compiled iFind helper class and could not be repaired: %s. "
                "Text search through it fails with <CLASS DOES NOT EXIST> until %s is "
                "recompiled.",
                schema,
                index_name,
                e,
                owner,
            )
            result[index_name] = False
            continue
        if not searchable:
            logger.warning(
                "%s.%s still has no compiled iFind helper class after recompiling %s.",
                schema,
                index_name,
                owner,
            )
        result[index_name] = searchable
    return result


class GraphSchema:
    """Domain-agnostic RDF-style graph schema management"""

    @staticmethod
    def get_base_schema_sql(
        embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
    ) -> str:
        """Get SQL for base schema. Using explicit Graph_KG schema qualification and robust types.

        Args:
            embedding_dimension: Dimension of the vector embeddings. Defaults to
                                 ``DEFAULT_EMBEDDING_DIMENSION`` (768) for backward
                                 compatibility, but should be set to match your
                                 actual embedding model (e.g. 384 for all-MiniLM-L6-v2).
        """
        return f"""
-- Spec 227: a node ID is unique per graph, not per namespace.  Every table that
-- points at a node therefore carries graph_id and references the composite key
-- (graph_id, node_id) in that column order — the referenced constraint's own
-- order, which IRIS requires and otherwise refuses with SQLCODE -121.
--
-- The two legacy embedding tables are the default route, so they declare the same
-- composite reference every generated route declares alongside its own vector column
-- (contracts/sql-schema.md §2).  3.2.0's single-column `fk_emb_node` had nothing
-- unique left to point at once a node ID stopped being unique on its own; it is
-- re-declared composite here rather than dropped, because without it an embedding
-- can be written for a node that does not exist and a node holding one can be
-- deleted (FR-008, data-model.md §2).
CREATE TABLE Graph_KG.nodes(
  node_id    VARCHAR(256) %EXACT NOT NULL,
  graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT pk_nodes_graph PRIMARY KEY (node_id, graph_id),
  CONSTRAINT uq_nodes_graph_node UNIQUE (graph_id, node_id)
);

CREATE TABLE Graph_KG.rdf_labels(
  graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  s          VARCHAR(256) %EXACT NOT NULL,
  label      VARCHAR(128) %EXACT NOT NULL,
  CONSTRAINT pk_labels PRIMARY KEY (graph_id, s, label),
  CONSTRAINT fk_labels_node FOREIGN KEY (graph_id, s) REFERENCES Graph_KG.nodes (graph_id, node_id)
);

CREATE TABLE Graph_KG.rdf_props(
  graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  s        VARCHAR(256) %EXACT NOT NULL,
  key      VARCHAR(128) %EXACT NOT NULL,
  val      VARCHAR(64000) %EXACT,
  CONSTRAINT pk_props PRIMARY KEY (graph_id, s, key)
);

{RDF_EDGES_DDL};

-- The two legacy embedding tables are the *default route* under 4.0.0, keyed the
-- same way a generated route is (contracts/sql-schema.md §2) so that the
-- kg_KNN_VEC procedure and a statement generated against a route name the same
-- columns.  `id` becomes `node_id` because a node ID is no longer unique on its
-- own, and the primary key becomes a single integer identity because IRIS only
-- accepts an HNSW index on a table shaped that way (research R3).  Uniqueness
-- moves to (graph_id, node_id): one vector per node per route.
CREATE TABLE Graph_KG.kg_NodeEmbeddings (
    emb_rowid BIGINT IDENTITY PRIMARY KEY,
    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    node_id VARCHAR(256) %EXACT NOT NULL,
    emb VECTOR(DOUBLE, {embedding_dimension}),
    metadata %Library.DynamicObject,
    CONSTRAINT uq_emb_graph_node UNIQUE (graph_id, node_id),
    CONSTRAINT fk_emb_node FOREIGN KEY (graph_id, node_id) REFERENCES Graph_KG.nodes (graph_id, node_id)
);

CREATE TABLE Graph_KG.kg_NodeEmbeddings_optimized (
    emb_rowid BIGINT IDENTITY PRIMARY KEY,
    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    node_id VARCHAR(256) %EXACT NOT NULL,
    emb VECTOR(DOUBLE, {embedding_dimension}),
    metadata %Library.DynamicObject,
    CONSTRAINT uq_emb_opt_graph_node UNIQUE (graph_id, node_id),
    CONSTRAINT fk_emb_node_opt FOREIGN KEY (graph_id, node_id) REFERENCES Graph_KG.nodes (graph_id, node_id)
);

-- `kg_EdgeEmbeddings` is the *default edge route* under 4.0.0 (spec 230, FR-006),
-- shaped like a generated edge route so one INSERT serves both.  Before 4.0.0 the
-- primary key was the triple alone, so two graphs asserting the same edge shared one
-- row and the second write replaced the first; the key is now (graph_id, s, p, o_id)
-- and the primary key a single integer identity, which is the only shape IRIS accepts
-- an HNSW index on (research R3).  `metadata` exists because the routed table has it.
CREATE TABLE IF NOT EXISTS Graph_KG.kg_EdgeEmbeddings (
    emb_rowid BIGINT IDENTITY PRIMARY KEY,
    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    s    VARCHAR(256) %EXACT NOT NULL,
    p    VARCHAR(512) %EXACT NOT NULL,
    o_id VARCHAR(256) %EXACT NOT NULL,
    emb  VECTOR(DOUBLE, {embedding_dimension}),
    metadata VARCHAR(4000),
    CONSTRAINT uq_edge_emb_graph_spo UNIQUE (graph_id, s, p, o_id)
);

-- One document per node per graph, and `id` is a NODE ID: the same key space as
-- `Graph_KG.nodes.node_id`, not a document ID chosen by whoever wrote the row.
-- That is what lets `kg_RRF_FUSE` fuse at all — its vector leg answers node IDs,
-- and a FULL OUTER JOIN between node IDs and free-form document IDs matches
-- nothing, so every "fused" row carried one leg and NULLs for the other.
-- No foreign key to `nodes`: a caller may write a document before the node
-- exists, and the 4.0.0 migration has to be able to quarantine a row whose ID
-- names no node rather than be refused by a constraint (FR-007, FR-018).
CREATE TABLE Graph_KG.docs(
  graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  id       VARCHAR(256) %EXACT NOT NULL,
  text     VARCHAR(4000) %EXACT,
  CONSTRAINT pk_docs PRIMARY KEY (graph_id, id)
);

CREATE INDEX idx_docs_graph ON Graph_KG.docs (graph_id);
CREATE INDEX idx_docs_text_ifind ON TABLE Graph_KG.docs (text) AS %iFind.Index.Basic;

CREATE TABLE IF NOT EXISTS Graph_KG.fhir_bridges (
    fhir_code        VARCHAR(64) %EXACT NOT NULL,
    kg_node_id       VARCHAR(256) %EXACT NOT NULL,
    fhir_code_system VARCHAR(128) NOT NULL DEFAULT 'ICD10CM',
    bridge_type      VARCHAR(64) NOT NULL DEFAULT 'icd10_to_mesh',
    confidence       FLOAT DEFAULT 1.0,
    source_cui       VARCHAR(16),
    CONSTRAINT pk_bridge PRIMARY KEY (fhir_code, kg_node_id)
);

CREATE INDEX idx_bridges_code_type ON Graph_KG.fhir_bridges (fhir_code, bridge_type);
CREATE INDEX idx_bridges_kg_node ON Graph_KG.fhir_bridges (kg_node_id);
CREATE INDEX idx_bridges_type ON Graph_KG.fhir_bridges (bridge_type);

-- spec 231: one row per FHIR repository projected as a named graph. The watermarks
-- are the highest Rsrc.ID and RsrcVer.ID a sync has consumed; they move inside the
-- same transaction as the edges they account for.
CREATE TABLE IF NOT EXISTS Graph_KG.fhir_graphs (
    graph_id      VARCHAR(256) %EXACT NOT NULL,
    repo_id       VARCHAR(64) NOT NULL,
    rsrc_schema   VARCHAR(128) NOT NULL,
    search_schema VARCHAR(128) NOT NULL,
    ver_schema    VARCHAR(128) NOT NULL,
    endpoints     VARCHAR(4000) DEFAULT '[]',
    denylist      VARCHAR(4000) DEFAULT '[]',
    interval_s    INTEGER DEFAULT 60,
    wm_rsrc       BIGINT DEFAULT 0,
    wm_ver        BIGINT DEFAULT 0,
    task_id       VARCHAR(64),
    last_sync     TIMESTAMP,
    last_rebuild  TIMESTAMP,
    last_error    VARCHAR(4000),
    last_counts   VARCHAR(32000),
    json_links    VARCHAR(8000),
    CONSTRAINT pk_fhir_graphs PRIMARY KEY (graph_id)
);

-- spec 231: a reference that did not become an edge, and why (external, missing,
-- deleted; spec 232 adds version-not-found, ambiguous, no-definition). Owned by its
-- source key: a resync replaces the source's rows.
CREATE TABLE IF NOT EXISTS Graph_KG.fhir_unresolved (
    graph_id VARCHAR(256) %EXACT NOT NULL,
    source   VARCHAR(256) %EXACT NOT NULL,
    param    VARCHAR(128) %EXACT NOT NULL,
    target   VARCHAR(512) %EXACT NOT NULL,
    reason   VARCHAR(32) NOT NULL
);

CREATE INDEX idx_fhir_unres_source ON Graph_KG.fhir_unresolved (graph_id, source);
CREATE INDEX idx_fhir_unres_target ON Graph_KG.fhir_unresolved (graph_id, target);

-- spec 232: the canonical url and version each definitional key declared at its last
-- sync, so a url edit can find the referrers of the old url. The repository index
-- keeps 220 characters of a url or version, so these columns do too.
CREATE TABLE IF NOT EXISTS Graph_KG.fhir_definitions (
    graph_id VARCHAR(256) %EXACT NOT NULL,
    rsrc_key VARCHAR(256) %EXACT NOT NULL,
    url      VARCHAR(220) %EXACT NOT NULL,
    version  VARCHAR(220) %EXACT,
    CONSTRAINT pk_fhir_definitions PRIMARY KEY (graph_id, rsrc_key)
);

CREATE INDEX idx_fhir_def_url ON Graph_KG.fhir_definitions (graph_id, url);

-- spec 232: every canonical (and extension Reference) link a source carries, resolved
-- or not. origin is 'index' or the json link entry; kind is canonical or reference.
-- Owned by its source key: a resync replaces the source's rows.
CREATE TABLE IF NOT EXISTS Graph_KG.fhir_canonical_refs (
    graph_id VARCHAR(256) %EXACT NOT NULL,
    source   VARCHAR(256) %EXACT NOT NULL,
    param    VARCHAR(128) %EXACT NOT NULL,
    url      VARCHAR(512) %EXACT NOT NULL,
    version  VARCHAR(220) %EXACT,
    origin   VARCHAR(1024) %EXACT NOT NULL,
    kind     VARCHAR(16) NOT NULL
);

CREATE INDEX idx_fhir_cref_source ON Graph_KG.fhir_canonical_refs (graph_id, source);
CREATE INDEX idx_fhir_cref_url ON Graph_KG.fhir_canonical_refs (graph_id, url);

-- spec 231: a code in a code system maps to a node in a graph. Supersedes fhir_bridges,
-- which is migrated in with relation 'related' and source 'fhir_bridges'.
CREATE TABLE IF NOT EXISTS Graph_KG.code_crosswalk (
    code_system_uri VARCHAR(256) %EXACT NOT NULL,
    code            VARCHAR(128) %EXACT NOT NULL,
    target_graph    VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    target_node_id  VARCHAR(256) %EXACT NOT NULL,
    relation        VARCHAR(16) NOT NULL DEFAULT 'exact',
    source          VARCHAR(128),
    source_version  VARCHAR(128),
    confidence      DOUBLE DEFAULT 1.0,
    CONSTRAINT pk_code_crosswalk PRIMARY KEY (target_graph, target_node_id, code_system_uri, code)
);

CREATE INDEX idx_crosswalk_code ON Graph_KG.code_crosswalk (code_system_uri, code);

CREATE TABLE IF NOT EXISTS Graph_KG.rdf_reifications (
    reifier_id VARCHAR(256) %EXACT NOT NULL,
    edge_id BIGINT NOT NULL,
    CONSTRAINT pk_reifications PRIMARY KEY (reifier_id)
);

CREATE INDEX idx_reif_edge ON Graph_KG.rdf_reifications (edge_id);

CREATE TABLE IF NOT EXISTS Graph_KG.table_mappings (
    label         VARCHAR(255) NOT NULL PRIMARY KEY,
    sql_table     VARCHAR(500) NOT NULL,
    id_column     VARCHAR(255) NOT NULL,
    prop_columns  VARCHAR(4000),
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Graph_KG.relationship_mappings (
    predicate    VARCHAR(255) NOT NULL,
    source_label VARCHAR(255) NOT NULL,
    target_label VARCHAR(255) NOT NULL,
    target_fk    VARCHAR(255),
    via_table    VARCHAR(500),
    via_source   VARCHAR(255),
    via_target   VARCHAR(255),
    CONSTRAINT pk_rel_mappings PRIMARY KEY (source_label, predicate, target_label)
);

CREATE TABLE IF NOT EXISTS Graph_KG.rdf_namespaces (
    prefix  VARCHAR(64)  NOT NULL,
    uri     VARCHAR(512) NOT NULL,
    CONSTRAINT pk_rdf_namespaces PRIMARY KEY (prefix)
);

-- Indexes for graph traversal performance (based on TrustGraph patterns)
-- Single-column indexes for basic lookups
CREATE INDEX idx_labels_s ON Graph_KG.rdf_labels (s);
CREATE INDEX idx_labels_label ON Graph_KG.rdf_labels (label);
CREATE INDEX idx_props_s ON Graph_KG.rdf_props (s);
CREATE INDEX idx_props_key ON Graph_KG.rdf_props (key);
CREATE INDEX idx_edges_s ON Graph_KG.rdf_edges (s);
CREATE INDEX idx_edges_oid ON Graph_KG.rdf_edges (o_id);
CREATE INDEX idx_edges_p ON Graph_KG.rdf_edges (p);

-- Composite indexes for common query patterns
CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props (s, key);
CREATE INDEX idx_edges_s_p ON Graph_KG.rdf_edges (s, p);
CREATE INDEX idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id);
CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels (s, label);

-- Substring indexing on rdf_props.val for fast property lookups
CREATE INDEX idx_props_val_ifind ON TABLE Graph_KG.rdf_props (val) AS %iFind.Index.Basic;

-- Functional index on edge confidence for fast filtering
CREATE INDEX idx_edges_confidence ON Graph_KG.rdf_edges(JSON_VALUE(qualifiers, '$.confidence' RETURNING INTEGER));
"""

    @staticmethod
    def get_indexes_sql() -> str:
        """Get SQL to create performance indexes. Safe to run on existing databases."""
        return """
-- Single-column indexes
CREATE INDEX IF NOT EXISTS idx_labels_s ON Graph_KG.rdf_labels (s);
CREATE INDEX IF NOT EXISTS idx_labels_label ON Graph_KG.rdf_labels (label);
CREATE INDEX IF NOT EXISTS idx_props_s ON Graph_KG.rdf_props (s);
CREATE INDEX IF NOT EXISTS idx_props_key ON Graph_KG.rdf_props (key);
CREATE INDEX IF NOT EXISTS idx_edges_s ON Graph_KG.rdf_edges (s);
CREATE INDEX IF NOT EXISTS idx_edges_oid ON Graph_KG.rdf_edges (o_id);
CREATE INDEX IF NOT EXISTS idx_edges_p ON Graph_KG.rdf_edges (p);
-- Composite indexes for common patterns
CREATE INDEX IF NOT EXISTS idx_props_s_key ON Graph_KG.rdf_props (s, key);
CREATE INDEX IF NOT EXISTS idx_edges_s_p ON Graph_KG.rdf_edges (s, p);
CREATE INDEX IF NOT EXISTS idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id);
CREATE INDEX IF NOT EXISTS idx_labels_s_label ON Graph_KG.rdf_labels (s, label);

-- Substring indexing on rdf_props.val for fast property lookups
-- Uses IRIS iFind for high-performance substring/text search
CREATE INDEX idx_props_val_ifind ON TABLE Graph_KG.rdf_props (val) AS %iFind.Index.Basic;

-- Functional index on edge confidence for fast filtering
-- Optimizes JSON processing by indexing the value inside the DynamicObject
CREATE INDEX idx_edges_confidence ON Graph_KG.rdf_edges(JSON_VALUE(qualifiers, '$.confidence' RETURNING INTEGER));
"""

    @staticmethod
    def ensure_indexes(cursor) -> Dict[str, bool]:
        """
        Create performance indexes if they don't exist. Safe for existing databases.

        Returns:
            Dict mapping index name to success status
        """
        indexes = [
            # Single-column indexes
            ("idx_labels_s", "CREATE INDEX idx_labels_s ON Graph_KG.rdf_labels (s)"),
            (
                "idx_labels_label",
                "CREATE INDEX idx_labels_label ON Graph_KG.rdf_labels (label)",
            ),
            ("idx_props_s", "CREATE INDEX idx_props_s ON Graph_KG.rdf_props (s)"),
            ("idx_props_key", "CREATE INDEX idx_props_key ON Graph_KG.rdf_props (key)"),
            ("idx_edges_s", "CREATE INDEX idx_edges_s ON Graph_KG.rdf_edges (s)"),
            (
                "idx_edges_oid",
                "CREATE INDEX idx_edges_oid ON Graph_KG.rdf_edges (o_id)",
            ),
            ("idx_edges_p", "CREATE INDEX idx_edges_p ON Graph_KG.rdf_edges (p)"),
            # Composite indexes for common patterns
            (
                "idx_props_s_key",
                "CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props (s, key)",
            ),
            (
                "idx_edges_s_p",
                "CREATE INDEX idx_edges_s_p ON Graph_KG.rdf_edges (s, p)",
            ),
            (
                "idx_edges_p_oid",
                "CREATE INDEX idx_edges_p_oid ON Graph_KG.rdf_edges (p, o_id)",
            ),
            (
                "idx_labels_s_label",
                "CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels (s, label)",
            ),
            (
                "idx_props_val_ifind",
                "CREATE INDEX idx_props_val_ifind ON TABLE Graph_KG.rdf_props (val) AS %iFind.Index.Basic",
            ),
            (
                "idx_docs_graph",
                "CREATE INDEX idx_docs_graph ON Graph_KG.docs (graph_id)",
            ),
            (
                "idx_docs_text_ifind",
                "CREATE INDEX idx_docs_text_ifind ON TABLE Graph_KG.docs (text) AS %iFind.Index.Basic",
            ),
            (
                "idx_ledger_correlation",
                "CREATE INDEX idx_ledger_correlation ON Graph_KG.ledger_revisions (correlation_id)",
            ),
            (
                "idx_edges_confidence",
                "CREATE INDEX idx_edges_confidence ON Graph_KG.rdf_edges(JSON_VALUE(qualifiers, '$.confidence' RETURNING INTEGER))",
            ),
            # Drop problematic indexes
            ("drop_idx_props_key_val", "DROP INDEX idx_props_key_val"),
        ]

        _OPTIONAL_INDEXES = {"idx_props_val_ifind", "idx_docs_text_ifind", "idx_edges_confidence", "idx_ledger_correlation"}

        status = {}
        for name, sql in indexes:
            try:
                cursor.execute(sql)
                status[name] = True
            except Exception as e:
                err = str(e).lower()
                sqlcode = ""
                import re as _re

                m = _re.search(r"sqlcode.*?<(-?\d+)>", err)
                if m:
                    sqlcode = m.group(1)

                if "already exists" in err or "already has" in err or "already has index" in err:
                    status[name] = True
                elif sqlcode == "-400" and "rdf_edges" in sql.lower():
                    alt_sql = GraphSchema._create_index_alter_table(name, sql)
                    if alt_sql:
                        try:
                            cursor.execute(alt_sql)
                            status[name] = True
                            continue
                        except Exception as e2:
                            e2_err = str(e2).lower()
                            if "already exists" in e2_err or "already has" in e2_err:
                                status[name] = True
                            else:
                                logger.debug("Index %s ALTER TABLE fallback failed: %s", name, e2)
                                status[name] = False
                    else:
                        logger.debug(
                            "Index %s skipped (-400, no ALTER TABLE equivalent): %s",
                            name,
                            e,
                        )
                        status[name] = False
                elif name in _OPTIONAL_INDEXES or sqlcode == "-400":
                    logger.debug("Optional/fatal index %s skipped: %s", name, e)
                    status[name] = False
                else:
                    status[name] = False

        status["upgrade_val_column"] = GraphSchema.upgrade_val_column(cursor)
        status["add_graph_id_column"] = GraphSchema.add_graph_id_column(cursor)
        status["add_graph_id_index"] = GraphSchema.add_graph_id_index(cursor)
        # The repair itself is unguarded inside the migration — a caller that runs it
        # directly must hear that the default graph still has two spellings. Schema
        # setup is not that caller: it records the failure and carries on, the same
        # way every migration above it does.
        try:
            status["tighten_graph_id_column"] = GraphSchema.tighten_graph_id_column(cursor)
        except Exception as e:
            logger.debug("graph_id tightening skipped: %s", e)
            status["tighten_graph_id_column"] = {"error": str(e)}
        status["update_spo_unique_constraint"] = GraphSchema.update_spo_unique_constraint(cursor)
        status["add_graph_id_to_nodes"] = GraphSchema.add_graph_id_to_nodes(cursor)

        return status

    #: The tables whose statistics decide whether a graph-scoped lookup reads an
    #: index or scans the extent. `nodes` is the one that matters most: both of
    #: `rdf_edges`' composite foreign keys resolve through it on every edge insert.
    TUNABLE_TABLES = (
        "nodes",
        "rdf_edges",
        "rdf_labels",
        "rdf_props",
        "rdf_reifications",
        "docs",
        "embedding_registry",
        "ledger_revisions",
    )

    @staticmethod
    def tune_tables(
        cursor,
        tables: Optional[Sequence[str]] = None,
        schema: str = "Graph_KG",
    ) -> Dict[str, bool]:
        """Collect table statistics so the optimizer can use the graph-scoped indexes.

        IVG ran `TUNE TABLE` nowhere, and on an install that spec 227 had re-keyed,
        `WHERE node_id = ? AND graph_id = ?` planned as

            Read master map Graph_KG.nodes.IDKEY, looping on ID

        — a scan, measured at 5.3ms against 46,343 rows. Since spec 227 that shape is
        on every graph-scoped read and inside both of `rdf_edges`' composite foreign
        keys, so an untuned install paid the scan twice per edge insert: 10.2ms,
        against 0.23ms after one `TUNE TABLE Graph_KG.nodes`, which moved the plan to
        `Read index map Graph_KG.nodes.pk_nodes_graph`.

        `TUNE TABLE` does two things a re-keyed install needs, and this call wants
        both: it measures the rows that are actually there, and it discards the
        cached plans prepared before the current indexes existed. So it runs at the
        end of schema setup and again after the 4.0.0 migration, which builds each
        re-keyed table by copying rows into a staging table and dropping the source
        over it.

        Args:
            cursor: A DB-API cursor on the namespace to tune.
            tables: Tune these instead of :data:`TUNABLE_TABLES`. A name with no
                schema is read as belonging to `schema`; a qualified one is used
                as given, which is how the registry names a routed table.
            schema: The schema unqualified names belong to.

        Returns:
            Qualified table name → whether `TUNE TABLE` succeeded. A table this
            install does not have reports False rather than raising: tuning is
            worth attempting on every table and required on none.
        """
        names = list(tables) if tables is not None else list(GraphSchema.TUNABLE_TABLES)
        status: Dict[str, bool] = {}
        for name in names:
            qualified = name if "." in name else f"{schema}.{name}"
            try:
                cursor.execute(f"TUNE TABLE {qualified}")
                status[qualified] = True
            except Exception as e:
                logger.debug("TUNE TABLE %s skipped: %s", qualified, e)
                status[qualified] = False
        return status

    @staticmethod
    def update_spo_unique_constraint(cursor) -> bool:
        try:
            cursor.execute("ALTER TABLE Graph_KG.rdf_edges DROP CONSTRAINT u_spo")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE Graph_KG.rdf_edges DROP CONSTRAINT uspo")
        except Exception:
            pass
        try:
            cursor.execute(
                "ALTER TABLE Graph_KG.rdf_edges ADD CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id)"
            )
            return True
        except Exception as e:
            if "already exists" in str(e).lower():
                return True
            return False

    @staticmethod
    def add_graph_id_index(cursor) -> bool:
        try:
            cursor.execute("CREATE INDEX idx_edges_graph_id ON Graph_KG.rdf_edges (graph_id)")
            return True
        except Exception as e:
            if "already exists" in str(e).lower() or "already has" in str(e).lower():
                return True
            return False

    @staticmethod
    def _create_index_alter_table(name: str, create_sql: str) -> Optional[str]:
        import re as _re

        m = _re.match(
            r"CREATE\s+INDEX\s+(\w+)\s+ON\s+([\w\.]+)\s*\(([^)]+)\)",
            create_sql.strip(),
            _re.IGNORECASE,
        )
        if not m:
            return None
        idx_name, table, cols = m.group(1), m.group(2), m.group(3)
        return f"ALTER TABLE {table} ADD INDEX {idx_name} ({cols})"

    @staticmethod
    def add_graph_id_column(cursor) -> bool:
        try:
            cursor.execute(
                "ALTER TABLE Graph_KG.rdf_edges ADD COLUMN graph_id VARCHAR(256) %EXACT NULL"
            )
            return True
        except Exception as e:
            err_lower = str(e).lower()
            if "already exists" in err_lower or "duplicate" in err_lower or "unique" in err_lower:
                return True
            return False

    @staticmethod
    def tighten_graph_id_column(cursor) -> Dict[str, Any]:
        """Give the default graph one spelling in ``rdf_edges``, and keep it that way.

        A fresh install creates ``graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT ''``.
        ``add_graph_id_column`` adds it ``NULL`` with no default, so an upgraded
        database holds both spellings of the default graph at once and every reader
        needs both predicates forever.

        Two steps, reported separately because they fail separately:

        1. ``UPDATE ... SET graph_id = '' WHERE graph_id IS NULL``.  This runs first:
           the ALTER cannot succeed while a single NULL row remains.
        2. ``ALTER COLUMN graph_id NOT NULL`` so nothing writes the second spelling
           again.  The type is deliberately *not* restated — ``ALTER COLUMN`` has no
           way to say ``%EXACT``, so a form that repeated ``VARCHAR(256)`` would drop
           the column back to the default collation and graph keys would stop
           comparing exactly.  ``SET DEFAULT ''`` follows so an INSERT that omits the
           column lands in the default graph rather than failing.

        The DDL half needs IRIS to resolve the table, which it cannot do while a
        second class also claims ``SqlTableName = rdf_edges``.  When that happens the
        repair has still happened, and the result says so rather than claiming a
        tightening it did not achieve.  Every writer in the package names ``graph_id``
        explicitly, so the repair alone is enough to keep the second spelling out.

        Note that ``rdf_edges`` legitimately appears twice in the catalog: IRIS
        auto-generates a compatibility view ``SQLUser.rdf_edges`` over
        ``Graph_KG.rdf_edges``.  Probe with ``TABLE_SCHEMA = 'Graph_KG'``; the view
        carries no column defaults, so reading its row makes a required ``graph_id``
        look nullable.  This migration only reaches a namespace with the
        ``Graph.KG.*`` classes deployed — a namespace whose schema was built by DDL
        alone keeps the nullable column forever.

        The table name is resolved rather than assumed (spec 230, FR-004).  Every
        statement here used to say ``Graph_KG.rdf_edges`` and nothing else, so a
        namespace presenting the table under any other name got a migration that
        raised ``SQLCODE -30`` on its first statement and — because
        :meth:`ensure_indexes` records the failure and carries on — a result
        indistinguishable from a database that needed no repair.  The declared
        schema is tried first: the ``SQLUser`` name is a *view*, so resolving it
        ahead of the base table would repair the rows and then silently skip the
        ``NOT NULL`` half against something no ALTER can reach.

        Returns:
            ``{"rows_repaired": int, "not_null": bool, "default_set": bool,
            "resolved_table": str | None, "spellings_tried": tuple[str, ...]}``.
            ``resolved_table`` is ``None`` when no spelling resolved, which is the
            one outcome the old shape could not express.
        """
        result: Dict[str, Any] = {
            "rows_repaired": 0,
            "not_null": False,
            "default_set": False,
            "resolved_table": None,
            "spellings_tried": GRAPH_ID_TABLE_SPELLINGS,
        }

        last_error: Optional[Exception] = None
        for table in GRAPH_ID_TABLE_SPELLINGS:
            try:
                cursor.execute(f"UPDATE {table} SET graph_id = '' WHERE graph_id IS NULL")
            except Exception as e:  # unresolvable under this name; try the next
                last_error = e
                logger.debug("graph_id repair could not resolve %s: %s", table, e)
                continue
            result["resolved_table"] = table
            break

        if result["resolved_table"] is None:
            logger.debug(
                "graph_id tightening found no table under %s: %s",
                ", ".join(GRAPH_ID_TABLE_SPELLINGS),
                last_error,
            )
            return result

        table = result["resolved_table"]
        rowcount = getattr(cursor, "rowcount", 0)
        try:
            result["rows_repaired"] = max(0, int(rowcount))
        except (TypeError, ValueError):
            result["rows_repaired"] = 0

        try:
            cursor.execute(f"ALTER TABLE {table} ALTER COLUMN graph_id NOT NULL")
            result["not_null"] = True
        except Exception as e:
            if "already" in str(e).lower():
                result["not_null"] = True
            else:
                logger.debug("graph_id could not be made NOT NULL: %s", e)

        try:
            cursor.execute(f"ALTER TABLE {table} ALTER COLUMN graph_id SET DEFAULT ''")
            result["default_set"] = True
        except Exception as e:
            logger.debug("graph_id default could not be set: %s", e)

        return result

    @staticmethod
    def get_graph_scope_migration_sql() -> List[str]:
        """Statements that re-key a 3.2.0 schema so a node ID is unique per graph.

        Spec 227, ``contracts/sql-schema.md`` §4.  The order is not interchangeable
        and the list is returned rather than executed so the order can be asserted
        without a database:

        1. **Drop the five dependents.**  Each foreign key holds
           ``uq_nodes_nodeid`` in place; dropping the unique first fails.
        2. **Swap the unique constraint** on ``nodes`` for
           ``(graph_id, node_id)``.  Column order matters: it is the order every
           composite reference below has to repeat, because IRIS names referenced
           columns in the referenced constraint's own order and refuses any other
           with ``SQLCODE -121`` (research R1).
        3. **Give labels and props their graph column**, following data-model.md
           §3: ``ADD COLUMN`` nullable → backfill from the node's graph →
           ``ALTER COLUMN ... NOT NULL`` → ``SET DEFAULT ''`` → re-key the primary
           key.  ``ALTER COLUMN`` never restates the type: the restated form is
           ``SQLCODE -25``, and restating it without ``%EXACT`` would silently drop
           the column to default collation, which is worse than an error because
           graph keys would stop comparing exactly (see
           :meth:`tighten_graph_id_column`).
        4. **Re-point the three structural dependents** at the composite key.  The
           two embedding foreign keys are not re-added *here* because the upgrade
           rebuilds both default-route tables from scratch
           (:meth:`~iris_vector_graph.migrations.graph_scoped_embeddings._create_embedding_table`)
           and the composite reference is declared in that ``CREATE TABLE``, exactly as
           a generated route declares its own (§2).  An ``ALTER`` here would also run
           before the rows have a graph, so it could only fail.

        The backfill resolves a child row's graph through ``nodes`` and claims only a
        row whose node sits in exactly one graph, so the column stays ``NULL`` when the
        node is absent or lives in more than one.  That is deliberate: step 3's
        ``NOT NULL`` then fails loudly instead of assigning an orphan to the default
        graph or a shared node ID to whichever graph sorts first (FR-036).  The
        migration in :mod:`iris_vector_graph.migrations` counts those rows before
        running any of these statements and refuses the whole re-key when it finds
        one, reporting the rows rather than repairing them.

        Returns:
            Statements in execution order, with no trailing semicolons (IRIS
            rejects one on a statement sent through the DB-API).
        """
        statements: List[str] = [
            # 1 — the five dependents that hold uq_nodes_nodeid in place
            "ALTER TABLE Graph_KG.rdf_labels DROP CONSTRAINT fk_labels_node",
            "ALTER TABLE Graph_KG.rdf_edges DROP CONSTRAINT fk_edges_source",
            "ALTER TABLE Graph_KG.rdf_edges DROP CONSTRAINT fk_edges_dest",
            "ALTER TABLE Graph_KG.kg_NodeEmbeddings DROP CONSTRAINT fk_emb_node",
            "ALTER TABLE Graph_KG.kg_NodeEmbeddings_optimized DROP CONSTRAINT fk_emb_node_opt",
            # 2 — the swap that makes a node ID unique per graph
            "ALTER TABLE Graph_KG.nodes DROP CONSTRAINT uq_nodes_nodeid",
            "ALTER TABLE Graph_KG.nodes ADD CONSTRAINT uq_nodes_graph_node "
            "UNIQUE (graph_id, node_id)",
        ]

        # 3 — labels and props gain graph_id, in data-model.md §3's order
        for table, pk_name, tail in (
            ("rdf_labels", "pk_labels", "label"),
            ("rdf_props", "pk_props", '"key"'),
        ):
            qualified = f"Graph_KG.{table}"
            statements.extend(
                [
                    f"ALTER TABLE {qualified} ADD COLUMN graph_id VARCHAR(256) %EXACT NULL",
                    f"UPDATE {qualified} c SET c.graph_id = "
                    f"(SELECT MIN(n.graph_id) FROM Graph_KG.nodes n WHERE n.node_id = c.s) "
                    f"WHERE (SELECT COUNT(DISTINCT n.graph_id) FROM Graph_KG.nodes n "
                    f"WHERE n.node_id = c.s) = 1",
                    f"ALTER TABLE {qualified} ALTER COLUMN graph_id NOT NULL",
                    f"ALTER TABLE {qualified} ALTER COLUMN graph_id SET DEFAULT ''",
                    f"ALTER TABLE {qualified} DROP CONSTRAINT {pk_name}",
                    f"ALTER TABLE {qualified} ADD CONSTRAINT {pk_name} "
                    f"PRIMARY KEY (graph_id, s, {tail})",
                ]
            )

        # 4 — re-point the structural dependents; embedding routes declare their own
        statements.extend(
            [
                "ALTER TABLE Graph_KG.rdf_labels ADD CONSTRAINT fk_labels_node "
                "FOREIGN KEY (graph_id, s) REFERENCES Graph_KG.nodes (graph_id, node_id)",
                "ALTER TABLE Graph_KG.rdf_edges ADD CONSTRAINT fk_edges_source "
                "FOREIGN KEY (graph_id, s) REFERENCES Graph_KG.nodes (graph_id, node_id)",
                "ALTER TABLE Graph_KG.rdf_edges ADD CONSTRAINT fk_edges_dest "
                "FOREIGN KEY (graph_id, o_id) REFERENCES Graph_KG.nodes (graph_id, node_id)",
            ]
        )
        return statements

    @staticmethod
    def add_graph_id_to_nodes(cursor, recreate_pk: bool = False) -> Dict[str, Any]:
        """Spec 214: add graph_id column to nodes; migrate __graph pseudo-props.

        This migration is safe to run on a live cluster.  It is additive-only by
        default: it adds the column and migrates data without touching the primary key.
        The compound PK recreation (step 4+) is gated behind ``recreate_pk=True``
        and is intentionally NOT called from ``ensure_indexes`` because the IRIS
        ``RENAME TABLE`` syntax is ``ALTER TABLE old RENAME new`` (without schema
        qualification on the new name), and the intermediate ``DROP TABLE nodes``
        step is destructive and non-recoverable on connection loss.

        Step 1 (DDL, auto-commits in IRIS):
          ALTER TABLE nodes ADD COLUMN graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT ''
        Step 2-3 (DML transaction): migrate __graph rows; delete them from rdf_props.
        Step 4+ (optional, recreate_pk=True only): recreate nodes with compound PK
          (node_id, graph_id) and UNIQUE (graph_id, node_id).  Requires explicit opt-in because
          it drops the existing nodes table — only safe on a fresh schema or after a
          verified full backup.  IRIS rename syntax: ALTER TABLE nodes_new RENAME nodes
        """
        result: Dict[str, Any] = {"column_added": False, "rows_migrated": 0, "pk_recreated": False}

        # Step 1 — DDL (auto-commits in IRIS)
        try:
            cursor.execute(
                "ALTER TABLE Graph_KG.nodes ADD COLUMN graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT ''"
            )
            result["column_added"] = True
        except Exception as e:
            err = str(e).lower()
            if "already" in err or "duplicate" in err:
                result["column_added"] = True
            else:
                logger.warning("add_graph_id_to_nodes step1 failed: %s", e)
                return result

        # Step 2-3 — DML: migrate existing __graph pseudo-prop values
        try:
            cursor.execute(
                "UPDATE Graph_KG.nodes n SET n.graph_id = "
                "(SELECT p.val FROM Graph_KG.rdf_props p "
                "WHERE p.s = n.node_id AND p.\"key\" = '__graph') "
                "WHERE EXISTS (SELECT 1 FROM Graph_KG.rdf_props p "
                "WHERE p.s = n.node_id AND p.\"key\" = '__graph')"
            )
            migrated = cursor.rowcount if cursor.rowcount is not None else 0
            cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE \"key\" = '__graph'")
            try:
                cursor.connection.commit()
            except AttributeError:
                pass
            result["rows_migrated"] = migrated
        except Exception as e:
            logger.warning("add_graph_id_to_nodes migration DML failed: %s", e)

        if not recreate_pk:
            # Default path: additive migration only. The compound PK is only required
            # for strict multi-tenant isolation and must be opted into explicitly via
            # recreate_pk=True after a full backup. Spec 227's unique swap is a
            # separate migration (get_graph_scope_migration_sql) because it also
            # re-points every dependent key.
            return result

        # Step 4+ — optional compound-PK recreation (recreate_pk=True only).
        # Check if it already exists first — safe to skip if so.
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA='Graph_KG' AND TABLE_NAME='nodes' "
                "AND CONSTRAINT_NAME='pk_nodes_graph'"
            )
            row = cursor.fetchone()
            if row and int(row[0]) > 0:
                result["pk_recreated"] = True
                return result
        except Exception:
            pass

        try:
            cursor.execute(
                """
                CREATE TABLE Graph_KG.nodes_new (
                    node_id    VARCHAR(256) %EXACT NOT NULL,
                    graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT pk_nodes_graph PRIMARY KEY (node_id, graph_id),
                    CONSTRAINT uq_nodes_graph_node UNIQUE (graph_id, node_id)
                )
            """
            )
            cursor.execute(
                "INSERT INTO Graph_KG.nodes_new (node_id, graph_id, created_at) "
                "SELECT node_id, graph_id, created_at FROM Graph_KG.nodes"
            )
            cursor.execute("DROP TABLE Graph_KG.nodes")
            # IRIS rename syntax: ALTER TABLE old_name RENAME new_name
            # The new name must NOT be schema-qualified.
            try:
                cursor.execute("ALTER TABLE Graph_KG.nodes_new RENAME nodes")
                result["pk_recreated"] = True
            except Exception as rename_err:
                # Rename failed after DROP — data is in nodes_new, nodes is gone.
                # Log loudly; caller must run ALTER TABLE Graph_KG.nodes_new RENAME nodes
                # manually to recover.
                logger.error(
                    "add_graph_id_to_nodes: DROP succeeded but RENAME failed (%s). "
                    "Data is in Graph_KG.nodes_new. Run: "
                    "ALTER TABLE Graph_KG.nodes_new RENAME nodes",
                    rename_err,
                )
            try:
                conn = getattr(cursor, "connection", None)
                if conn:
                    conn.commit()
            except Exception:
                pass
        except Exception as e:
            logger.warning("add_graph_id_to_nodes table recreation failed: %s", e)
            try:
                conn = getattr(cursor, "connection", None)
                if conn:
                    conn.rollback()
            except Exception:
                pass
        return result

    @staticmethod
    def disable_indexes(cursor) -> Dict[str, bool]:
        """
        Disable indexes for bulk loading. Re-enable with rebuild_indexes() after loading.

        This dramatically speeds up bulk INSERT operations by skipping index maintenance.

        Returns:
            Dict mapping index name to success status
        """
        # Only drop indexes on tables that support DDL from a cursor connection.
        # rdf_edges is left out: DROP INDEX on it via a cursor has returned SQLCODE
        # -400, which corrupts the connection's parameter binding state for every
        # later call on that cursor. This was once explained by `Graph.KG.Edge`
        # owning the table as a persistent class; spec 227 deleted that class, so the
        # table is DDL-declared like the others and the explanation no longer holds.
        # The omission stays because it is measured behaviour and costs only the bulk
        # speed-up on one table — not because the reason is understood.
        indexes = [
            "idx_labels_s",
            "idx_labels_label",
            "idx_labels_s_label",
            "idx_props_s",
            "idx_props_key",
            "idx_props_s_key",
        ]

        status = {}
        for name in indexes:
            try:
                safe_name = sanitize_identifier(name)
                cursor.execute(f"DROP INDEX {safe_name}")
                status[name] = True
            except Exception as e:
                err = str(e).lower()
                if "does not exist" in err or "not found" in err:
                    status[name] = True  # Already gone
                else:
                    status[name] = False
        return status

    # The exact CREATE INDEX statements for indexes that disable_indexes() drops.
    # Must stay in sync with the list in disable_indexes().
    # rdf_edges indexes are NOT included, matching disable_indexes(): DDL on that
    # table via a cursor has returned SQLCODE -400 and corrupted the connection's
    # parameter binding state. See disable_indexes() for why the old explanation
    # (Graph.KG.Edge owning the table) no longer applies.
    _BULK_INDEX_DDL = [
        ("idx_labels_s", "CREATE INDEX idx_labels_s ON Graph_KG.rdf_labels (s)"),
        ("idx_labels_label", "CREATE INDEX idx_labels_label ON Graph_KG.rdf_labels (label)"),
        ("idx_labels_s_label", "CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels (s, label)"),
        ("idx_props_s", "CREATE INDEX idx_props_s ON Graph_KG.rdf_props (s)"),
        ("idx_props_key", "CREATE INDEX idx_props_key ON Graph_KG.rdf_props (key)"),
        ("idx_props_s_key", "CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props (s, key)"),
    ]

    @staticmethod
    def rebuild_indexes(cursor) -> Dict[str, bool]:
        """
        Rebuild only the indexes that disable_indexes() dropped. Does NOT run schema
        migrations (ALTER TABLE, ADD COLUMN, etc.) — callers rely on this being
        a pure CREATE INDEX operation that doesn't corrupt driver parameter state.

        Returns:
            Dict mapping index name to success status
        """
        status = {}
        for name, sql in GraphSchema._BULK_INDEX_DDL:
            try:
                safe_name = sanitize_identifier(name)
                cursor.execute(sql)
                status[name] = True
            except Exception as e:
                err = str(e).lower()
                if "already exists" in err or "already has" in err:
                    status[name] = True
                elif "sqlcode: <-400>" in err or "<-400>" in err:
                    # IRIS: try ALTER TABLE ADD INDEX fallback for rdf_edges
                    alt = GraphSchema._create_index_alter_table(name, sql)
                    if alt:
                        try:
                            cursor.execute(alt)
                            status[name] = True
                            continue
                        except Exception as e2:
                            e2_err = str(e2).lower()
                            if "already exists" in e2_err or "already has" in e2_err:
                                status[name] = True
                            else:
                                status[name] = False
                    else:
                        status[name] = False
                else:
                    status[name] = False
        return status

    @staticmethod
    def get_bulk_insert_sql(table: str) -> str:
        """
        Get INSERT statement with %NOINDEX hint for bulk loading.

        Args:
            table: Table name ('nodes', 'rdf_labels', 'rdf_props', 'rdf_edges', 'kg_NodeEmbeddings')

        Returns:
            INSERT SQL with %NOINDEX for fast bulk loading

        Example:
            sql = GraphSchema.get_bulk_insert_sql('rdf_labels')
            cursor.execute(sql, [node_id, label])

        Every guard that names a graph compares ``COALESCE(graph_id, '')`` against
        ``COALESCE(?, '')``. Both halves matter: a database upgraded rather than
        created stores half the default graph as NULL, so a guard reading the column
        directly reports "nothing there" and the load writes a second copy of every
        row it already has; and a caller passing ``None`` for the default graph would
        otherwise be looking for the literal NULL graph rather than the default one.
        One parameter says all of it (spec 230, FR-005).

        The unscoped ``nodes`` / ``rdf_labels`` / ``rdf_props`` templates name no
        graph at all on purpose: they are the fallback for a pre-214 schema where the
        column does not exist, and naming it there is SQLCODE -29. Their graph-aware
        counterparts carry ``_with_graph`` in the name; ``rdf_edges`` has had the
        column for long enough that its unscoped form writes ``''`` explicitly.
        """
        templates = {
            "nodes": "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)",
            # The guard names the graph for the same reason the children's do: a
            # node ID can live in two graphs after the re-key, so probing on
            # `node_id` alone found another graph's row, skipped the insert, and
            # still reported the node created (spec 227 FR-034).
            "nodes_with_graph": "INSERT INTO Graph_KG.nodes (node_id, graph_id) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id = ? AND COALESCE(graph_id, '') = COALESCE(?, ''))",
            "rdf_labels": "INSERT INTO Graph_KG.rdf_labels (s, label) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels WHERE s = ? AND label = ?)",
            "rdf_props": 'INSERT INTO Graph_KG.rdf_props (s, "key", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ?)',
            # Spec 227 (FR-034): the child tables re-keyed on (graph_id, ...), so
            # both the row and its guard name the graph. Unscoped, the guard finds
            # another graph's row for the same node ID, reports "already there",
            # and the insert is skipped with nothing written and no error.
            "rdf_labels_with_graph": "INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels WHERE COALESCE(graph_id, '') = COALESCE(?, '') AND s = ? AND label = ?)",
            "rdf_props_with_graph": 'INSERT INTO Graph_KG.rdf_props (graph_id, s, "key", val) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_props WHERE COALESCE(graph_id, \'\') = COALESCE(?, \'\') AND s = ? AND "key" = ?)',
            # graph_id is named explicitly, not left to the column default: on a
            # database upgraded from before spec-214 the column is nullable with no
            # default, and an omitted graph_id lands in the NULL spelling of the
            # default graph that no graph-aware reader looks at. The guard names the
            # same graph the row does — unscoped, a default-graph load deduped
            # against every graph in the namespace, so a tenant already holding
            # `(s, p, o)` made the default graph's copy of that edge unwritable
            # (spec 230, FR-005).
            "rdf_edges": "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) SELECT ?, ?, ?, '' WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND COALESCE(graph_id, '') = '')",
            "rdf_edges_with_graph": "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND COALESCE(graph_id, '') = COALESCE(?, ''))",
            # `node_id`, not `id`: spec 227 re-keyed the embedding tables on
            # (graph_id, node_id) with emb_rowid as the identity, so the old
            # column name is SQLCODE -29 on the first row of any bulk load.
            "kg_NodeEmbeddings": "INSERT INTO Graph_KG.kg_NodeEmbeddings (graph_id, node_id, emb, metadata) SELECT ?, ?, TO_VECTOR(?), ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings WHERE COALESCE(graph_id, '') = COALESCE(?, '') AND node_id = ?)",
        }
        if table not in templates:
            raise ValueError(f"Unknown table: {table}. Valid: {list(templates.keys())}")
        return templates[table]

    @staticmethod
    def upgrade_val_column(cursor) -> bool:
        """
        Upgrade rdf_props.val from VARCHAR(4000) to VARCHAR(64000) for large value support.

        Safe to run on existing databases - will alter column type if needed.
        VARCHAR(64000) supports values up to 64KB while keeping REPLACE function compatibility.

        Returns:
            True if upgraded or already large enough, False on error
        """
        try:
            cursor.execute("ALTER TABLE Graph_KG.rdf_props ALTER COLUMN val VARCHAR(64000)")
            return True
        except Exception as e:
            # Already correct size or other issue
            if "already" in str(e).lower() or "same" in str(e).lower():
                return True
            return False

    @staticmethod
    def validate_schema(cursor) -> Dict[str, bool]:
        """
        Validates that required schema tables exist
        """
        required_tables = [
            "Graph_KG.rdf_labels",
            "Graph_KG.rdf_props",
            "Graph_KG.rdf_edges",
            "Graph_KG.kg_NodeEmbeddings",
            "Graph_KG.kg_NodeEmbeddings_optimized",
            "Graph_KG.kg_EdgeEmbeddings",
            "Graph_KG.docs",
        ]

        status = {}
        for table in required_tables:
            try:
                # Validate table name against allowlist
                safe_table = validate_table_name(table)
                cursor.execute(f"SELECT TOP 1 * FROM {safe_table}")
                status[table] = True
            except Exception:
                status[table] = False

        return status

    @staticmethod
    def _split_table_name(table_name: str) -> tuple:
        """Split ``Graph_KG.kg_EdgeEmbeddings`` into ``("Graph_KG", "kg_EdgeEmbeddings")``.

        An unqualified name is taken to be in the Graph_KG schema.
        """
        schema, _, table = table_name.rpartition(".")
        return (schema or "Graph_KG"), table

    @staticmethod
    def derive_class_name(table_name: str) -> str:
        """Derive the persistent class name IRIS generates for a SQL table name.

        Schema underscores become package dots, table underscores are dropped:
        ``Graph_KG.kg_NodeEmbeddings`` -> ``Graph.KG.kgNodeEmbeddings``, which is
        exactly what ``iris_src/src/Graph/KG/kgNodeEmbeddings.cls`` declares via
        ``SqlTableName``.

        This is a *guess*, used only when the class dictionary cannot be read.
        A DDL-created table (``kg_EdgeEmbeddings`` has no hand-written .cls) gets
        whatever class name IRIS chose, which may not follow this rule — prefer
        :meth:`resolve_table_class`.
        """
        schema, table = GraphSchema._split_table_name(table_name)
        return f"{schema.replace('_', '.')}.{table.replace('_', '')}"

    @staticmethod
    def hnsw_indexes(cursor, table_name: str) -> List[tuple]:
        """The HNSW indexes IRIS holds on ``table_name``, as ``(sql_name, properties)``.

        The one place that answers "does this table have an ANN index", so the two index
        reports (``AdminMixin._show_indexes`` and ``IRISGraphStore.list_indexes``) cannot
        drift apart again — both used to synthesize a row from a table's row count
        (spec 226, FR-018).

        An ANN index is distinguished by ``TypeClass`` (``%SQL.Index.HNSW``), not by
        ``Type``, which reads ``index`` for a plain index and an HNSW one alike;
        ``INFORMATION_SCHEMA.INDEXES`` carries no index type at all.

        Empty when the table has no such index, has no projecting class, or does not exist:
        ``kg_NodeEmbeddings_optimized`` is absent in DDL-only namespaces, and an index
        report has to survive that rather than raise from the middle of itself.
        """
        class_name = GraphSchema.resolve_table_class(cursor, table_name)
        if not class_name:
            return []
        try:
            # Literal interpolation, as in `resolve_table_class`: parameterised binds do
            # not match reliably against the `%Dictionary.*` tables. `class_name` came from
            # the dictionary itself, so it is not caller input.
            cursor.execute(
                "SELECT SqlName, Properties FROM %Dictionary.CompiledIndex "
                f"WHERE parent = '{class_name}' AND TypeClass LIKE '%HNSW%'"
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as e:
            logger.debug("Could not read HNSW indexes for %s: %s", table_name, e)
            return []

    @staticmethod
    def resolve_table_class(cursor, table_name: str) -> Optional[str]:
        """Ask IRIS which class projects to ``table_name``. None if no class does.

        The class dictionary is keyed by class name, not SQL name, so anything
        reading ``%Dictionary.CompiledProperty`` for a table has to make this
        hop first. Note the literal interpolation: parameterised binds do not
        match reliably against ``%Dictionary.CompiledClass``.
        """
        schema, table = GraphSchema._split_table_name(table_name)
        try:
            safe_schema = sanitize_identifier(schema)
            safe_table = sanitize_identifier(table)
        except ValueError:
            logger.warning("Invalid table name for class lookup: %r", table_name)
            return None

        try:
            cursor.execute(
                "SELECT Name FROM %Dictionary.CompiledClass "
                f"WHERE SqlSchemaName = '{safe_schema}' AND SqlTableName = '{safe_table}'"
            )
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception as e:
            logger.debug("Could not resolve class for %s: %s", table_name, e)
        return None

    @staticmethod
    def get_embedding_dimension(
        cursor, table_name: str = DEFAULT_EMBEDDING_TABLE
    ) -> Optional[int]:
        """Detect the VECTOR width of ``table_name``'s ``emb`` column, or None.

        None means "this table has no ``emb`` column with a declared width" —
        either the table does not exist, or the column was created untyped. It
        does **not** mean "use the default": before 3.1.0 this method ignored
        ``table_name`` entirely and answered about ``Graph.KG.kgNodeEmbeddings``
        whatever it was asked, so a caller checking the edge column was handed
        the node column's width and read a broken schema as healthy.
        """
        class_name = GraphSchema.resolve_table_class(
            cursor, table_name
        ) or GraphSchema.derive_class_name(table_name)

        try:
            safe_class = sanitize_identifier(class_name)
        except ValueError:
            logger.warning("Invalid table name for dimension lookup: %r", table_name)
            return None

        try:
            cursor.execute(
                "SELECT Parameters FROM %Dictionary.CompiledProperty "
                f"WHERE Name = 'emb' AND Parent = '{safe_class}'"
            )
            rows = cursor.fetchall()
            for row in rows:
                params = str(row[0])
                if "LEN," in params:
                    # Parse 'LEN,768' out of the flat Parameters blob
                    parts = params.split(",")
                    for i, p in enumerate(parts):
                        if p == "LEN" and i + 1 < len(parts):
                            return int(parts[i + 1])
        except Exception:
            pass

        # Fallback to INFORMATION_SCHEMA (though IRIS often reports VECTOR as VARCHAR there)
        schema, table = GraphSchema._split_table_name(table_name)

        try:
            result = None
            for s_name, t_name in [(schema, table), (schema.upper(), table.upper())]:
                cursor.execute(
                    """
                    SELECT DTD_IDENTIFIER, COLUMN_TYPE, DATA_TYPE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = ?
                      AND TABLE_NAME = ?
                      AND COLUMN_NAME = 'emb'
                    """,
                    [s_name, t_name],
                )
                result = cursor.fetchone()
                if result:
                    break

            if result:
                for val in result:
                    if not val:
                        continue
                    val_str = str(val)
                    import re

                    matches = re.findall(r"(\d+)\s*\)", val_str)
                    if matches:
                        return int(matches[-1])
                    digits = "".join(ch for ch in val_str if ch.isdigit())
                    if digits:
                        return int(digits)
        except Exception:
            pass

        return None

    @staticmethod
    def get_procedures_sql_list(
        table_schema: str = "Graph_KG",
    ) -> List[str]:
        """
        Get a list of SQL statements to install retrieval stored procedures.

        The 3.2.0 ``embedding_dimension`` parameter is **removed** in 4.0.0
        (FR-024). It was accepted and ignored, which is worse than absent: it read
        like it controlled the vector width. It never did, and it must not, because
        the generated ``kg_KNN_VEC`` converts the query vector with
        ``TO_VECTOR(:queryInput, DOUBLE)`` — no length — on purpose. A declared
        length makes IRIS pad or truncate the query vector to it and score the
        reshaped value, so a six-element query against a four-wide column returned a
        perfect-match 1.0 instead of an error. Unlengthed, IRIS compares the two
        widths and raises ``SQLCODE -257``. See ADR-0005; the measurement is pinned
        by ``tests/integration/test_to_vector_width_regression_e2e.py``.

        Widths are enforced where that is useful: on the column declaration, and
        against ``Graph_KG.embedding_registry``.

        Args:
            table_schema: SQL schema containing the data tables (e.g. "Graph_KG").

        Returns:
            List of SQL DDL strings in execution order. Each is a complete
            statement suitable for cursor.execute().
        """
        return [
            "CREATE SCHEMA iris_vector_graph",
            """
CREATE OR REPLACE FUNCTION SQLUser.JSON_ARRAYLENGTH(j VARCHAR(32000)) RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Set a = ##class(%Library.DynamicArray).%FromJSON(j) Quit a.%Size() }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.JSON_ARRAYGET(j VARCHAR(32000), i INTEGER) RETURNS VARCHAR(4000) LANGUAGE OBJECTSCRIPT { Set a = ##class(%Library.DynamicArray).%FromJSON(j), val = a.%Get(i) If $IsObject(val) { Quit val.%ToJSON() } Quit val _ "" }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.JSON_VALUE(j VARCHAR(32000), p VARCHAR(1000)) RETURNS VARCHAR(4000) LANGUAGE OBJECTSCRIPT { Set obj = ##class(%Library.DynamicObject).%FromJSON(j), key = $Extract(p, 3, *), val = obj.%Get(key) If $IsObject(val) { Quit val.%ToJSON() } Quit val _ "" }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.RAND() RETURNS DOUBLE LANGUAGE OBJECTSCRIPT { Quit $RANDOM(1000000) / 1000000.0 }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.NEWID() RETURNS VARCHAR(36) LANGUAGE OBJECTSCRIPT { Quit $SYSTEM.Util.CreateGUID() }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.LIST_REVERSE(j VARCHAR(32000)) RETURNS VARCHAR(32000) LANGUAGE OBJECTSCRIPT { Set arr = ##class(%Library.DynamicArray).%FromJSON(j), out = ##class(%Library.DynamicArray).%New(), n = arr.%Size(), i = n-1 While i >= 0 { Do out.%Push(arr.%Get(i)) Set i = i-1 } Quit out.%ToJSON() }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.LIST_TAIL(j VARCHAR(32000)) RETURNS VARCHAR(32000) LANGUAGE OBJECTSCRIPT { Set arr = ##class(%Library.DynamicArray).%FromJSON(j), out = ##class(%Library.DynamicArray).%New(), n = arr.%Size(), i = 1 While i < n { Do out.%Push(arr.%Get(i)) Set i = i+1 } Quit out.%ToJSON() }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.REGEX_MATCH(s VARCHAR(4000), p VARCHAR(4000)) RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit $MATCH(s, p) }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.LIST_HEAD(j VARCHAR(32000)) RETURNS VARCHAR(4000) LANGUAGE OBJECTSCRIPT { Set arr = ##class(%Library.DynamicArray).%FromJSON(j), t = arr.%GetTypeOf(0) If t = "unassigned" Quit "" If t = "number" Quit arr.%Get(0) If t = "boolean" Quit arr.%Get(0) If t = "string" Quit arr.%Get(0) Set obj = arr.%Get(0) Quit obj.%ToJSON() }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.LIST_LAST(j VARCHAR(32000)) RETURNS VARCHAR(4000) LANGUAGE OBJECTSCRIPT { Set arr = ##class(%Library.DynamicArray).%FromJSON(j), n = arr.%Size()-1, t = arr.%GetTypeOf(n) If t = "unassigned" Quit "" If t = "number" Quit arr.%Get(n) If t = "boolean" Quit arr.%Get(n) If t = "string" Quit arr.%Get(n) Set obj = arr.%Get(n) Quit obj.%ToJSON() }
""",
            """
CREATE OR REPLACE FUNCTION SQLUser.STR_SPLIT(str VARCHAR(4000), delim VARCHAR(100)) RETURNS VARCHAR(32000) LANGUAGE OBJECTSCRIPT { Set out = ##class(%Library.DynamicArray).%New(), n = $LENGTH(str, delim), i = 1  While i <= n { Do out.%Push($PIECE(str, delim, i))  Set i = i + 1 } Quit out.%ToJSON() }
""",
            f"""
CREATE OR REPLACE PROCEDURE {table_schema}.kg_KNN_VEC(
  IN queryInput VARCHAR(32000),
  IN k INT,
  IN labelFilter VARCHAR(128),
  -- The fourth argument was embeddingConfig, accepted and ignored. In 4.0.0 it is the
  -- graph, and it is load-bearing. The slot's *meaning* changed while its position and
  -- type did not, so a 3.2.0 caller still compiles and now searches a graph named after
  -- their model — which is why this is called out in the changelog rather than left to
  -- a deprecation warning nobody would see (FR-022).
  IN graphId VARCHAR(256)
)
LANGUAGE SQL
BEGIN
  -- The query vector is converted with no length, deliberately: a declared length makes
  -- IRIS pad or truncate the query to it and score the reshaped value, turning a wrong
  -- width into a plausible number instead of SQLCODE -257. See ADR-0005.
  --
  -- COALESCE on both sides of every graph comparison. Left, because graph_id arrives by
  -- ADD COLUMN on an upgraded install and is nullable there. Right, because IRIS binds
  -- an empty host variable as SQL NULL, so `= :graphId` for the default graph would
  -- compile to `= NULL` and match nothing, quietly, with SQLCODE 100 (FR-005).
  --
  -- The label join carries the same predicate: without it a label belonging to another
  -- graph's copy of this node ID satisfies the join and the label filter becomes the
  -- leak rather than the filter (FR-035).
  --
  -- This procedure serves the default route only — its FROM is fixed. A search against
  -- another route goes through resolve_route in Python and a generated statement of this
  -- same shape; both must return identical scope for the same graph.
  -- graph_id is projected as well as filtered on. kg_RRF_FUSE joins this leg to the
  -- text leg and both legs have to agree on the graph, not just the id; a leg that
  -- knows its graph and will not say it forces its caller to assume one.
  SELECT TOP :k n.node_id, VECTOR_COSINE(n.emb, TO_VECTOR(:queryInput, DOUBLE)) AS score,
         n.graph_id
  FROM {table_schema}.kg_NodeEmbeddings n
  LEFT JOIN {table_schema}.rdf_labels L
    ON L.s = n.node_id
   AND COALESCE(L.graph_id, '') = COALESCE(:graphId, '')
  WHERE COALESCE(n.graph_id, '') = COALESCE(:graphId, '')
    AND (:labelFilter IS NULL OR :labelFilter = '' OR L.label = :labelFilter)
  ORDER BY score DESC;
END
""",
            f"""
CREATE OR REPLACE PROCEDURE {table_schema}.kg_TXT(
  IN q VARCHAR(4000),
  IN k INT,
  -- Minimum number of occurrences of the search string in the document, matching the
  -- score below. It used to be documented as an iFind rank (0-1000), a scale nothing
  -- ever produced.
  IN minOccurrences INT,
  -- The graph whose documents may answer. Required, not defaulted: a three-argument
  -- 3.2.0 call now fails on arity rather than being narrowed to the default graph
  -- without saying so, and an unscoped text leg is what made a "scoped" fusion
  -- return every graph's documents (FR-008).
  IN graphId VARCHAR(256)
)
LANGUAGE SQL
BEGIN
  -- iFind is reached through the index, never through the column: `%FIND(d.text, :q)`
  -- resolves as a user function (`SQLUSER.%FIND`, SQLCODE -359) and this procedure could
  -- not be created at all, which took kg_RRF_FUSE down with it.
  --
  -- The score is a term-frequency count, not BM25. `%FIND.Rank` does not exist, and
  -- `%iFind.Rank` fails at runtime with <CLASS DOES NOT EXIST> because the IRIS AI image
  -- carries no `%iFind.Ranker.*` class for $$$IFDEFAULTRANKER to resolve. iFind still
  -- chooses the candidates (word-aware, indexed); the count only orders them.
  --
  -- COALESCE on both sides of the graph comparison, for the same two reasons as
  -- kg_KNN_VEC: graph_id arrives by ADD COLUMN on an upgraded install and is nullable
  -- there, and IRIS binds an empty host variable as SQL NULL, so `= :graphId` for the
  -- default graph would compile to `= NULL` and match nothing (FR-005).
  SELECT TOP :k d.id,
         (CHAR_LENGTH(UPPER(d.text)) - CHAR_LENGTH(REPLACE(UPPER(d.text), UPPER(:q), '')))
         / CHAR_LENGTH(:q) AS score,
         d.graph_id
  FROM {table_schema}.docs d
  WHERE %ID %FIND search_index(idx_docs_text_ifind, :q)
    AND COALESCE(d.graph_id, '') = COALESCE(:graphId, '')
    AND (CHAR_LENGTH(UPPER(d.text)) - CHAR_LENGTH(REPLACE(UPPER(d.text), UPPER(:q), '')))
        / CHAR_LENGTH(:q) >= :minOccurrences
  ORDER BY score DESC;
END
""",
            f"""
CREATE OR REPLACE PROCEDURE {table_schema}.kg_RRF_FUSE(
  IN k INT,
  IN k1 INT,
  IN k2 INT,
  IN c INT,
  IN queryVector VARCHAR(32000),
  IN qtext VARCHAR(4000),
  -- Added with the graph scope, not optional. The old call passed NULL into
  -- kg_KNN_VEC's fourth slot to satisfy its arity; leaving that NULL would now mean
  -- "the default graph" by accident, and a fusion that ranked every graph's vectors
  -- together would re-widen exactly what kg_KNN_VEC was narrowed to fix (FR-023).
  IN graphId VARCHAR(256)
)
LANGUAGE SQL
BEGIN
  WITH V AS (
    -- kg_KNN_VEC answers `node_id`, not `id`: 227 re-keyed the embedding tables and the
    -- procedure projects `n.node_id`. Reading `id` here made this CREATE fail at Prepare
    -- with SQLCODE -29 ("Field 'ID' not found in the applicable tables"), so the
    -- procedure did not exist at all and SQL-side fusion was silently absent.
    SELECT ROW_NUMBER() OVER (ORDER BY score DESC) AS r, node_id AS id, graph_id, score AS vs
    FROM {table_schema}.kg_KNN_VEC(:queryVector, :k1, NULL, :graphId)
  ),
  K AS (
    -- kg_TXT answers `score`, a term-frequency count. It was read as `bm25` here, a
    -- column kg_TXT never had and a ranking it never computed.
    --
    -- :graphId reaches this leg, and `docs.id` is a node id (FR-007). Both changes are
    -- load-bearing: without the graph the leg answered every graph's documents, and
    -- without the shared key space the join below matched nothing, so every "fused" row
    -- carried one leg's score and NULL for the other.
    SELECT ROW_NUMBER() OVER (ORDER BY score DESC) AS r, id, graph_id, score AS ts
    FROM {table_schema}.kg_TXT(:qtext, :k2, 0, :graphId)
  ),
  F AS (
    SELECT COALESCE(V.id, K.id) AS id,
           COALESCE(V.graph_id, K.graph_id) AS graph_id,
           (1.0/(:c + COALESCE(V.r, 1000000000))) +
           (1.0/(:c + COALESCE(K.r, 1000000000))) AS rrf,
           V.vs, K.ts
    -- Joined on the graph as well as the id. Both legs are already restricted to
    -- :graphId, so this cannot change which rows pair up today — it is here because the
    -- next leg added to this fusion will not be, and a join on id alone would then fuse
    -- one graph's vector score with another graph's text score into a single row.
    FROM V FULL OUTER JOIN K
      ON V.id = K.id
     AND COALESCE(V.graph_id, '') = COALESCE(K.graph_id, '')
  )
  SELECT TOP :k id, rrf, vs, ts
  FROM F
  ORDER BY rrf DESC;
END
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_PPR(
  seedEntities VARCHAR(32000),
  dampingFactor DOUBLE DEFAULT 0.85,
  maxIterations INT DEFAULT 100,
  bidirectional INT DEFAULT 0,
  reverseEdgeWeight DOUBLE DEFAULT 1.0,
  graphId VARCHAR(256) DEFAULT ''
)
RETURNS VARCHAR(8000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.PageRank).RunJson(seedEntities, dampingFactor, maxIterations, bidirectional, reverseEdgeWeight, graphId)
    quit result
}}
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_DegreeCentrality(
  direction VARCHAR(8) DEFAULT 'out',
  predicate VARCHAR(256) DEFAULT '',
  topK INT DEFAULT 10000
)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.Centrality).DegreeCentralityJson(direction, predicate, topK)
    quit result
}}
""",
            # The sample-size default is an `if`, not `$SELECT(sampleSize>0:sampleSize,
            # 1:200)`. A comma inside a colon-clause list is DDL the parser rejects —
            # `<PARAMETER ERROR> Parameter Name error, First value cannot be a digit: 2`
            # — so this function silently did not exist: the failure landed in
            # `_install_procedures`'s non-core branch as a debug log, and calling it gave
            # `SQLCODE -359 ... 'GRAPH_KG.KG_BETWEENNESS' does not exist`. `$S(...)` and
            # `$CASE(...)` fail the same way; a single-clause `$SELECT(a:b)` is fine.
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_Betweenness(
  sampleSize INT DEFAULT 0,
  direction VARCHAR(8) DEFAULT 'out',
  maxHops INT DEFAULT 0,
  topK INT DEFAULT 10000,
  memBudgetMB INT DEFAULT 256
)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{{
    set samples = 200
    if sampleSize > 0 {{ set samples = sampleSize }}
    set result = ##class(Graph.KG.NKGAccel).BetweennessGlobal(sampleSize, topK, samples)
    if $extract(result,1,3)="OK:" {{ set result = $extract(result,4,*) }}
    quit result
}}
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_Closeness(
  formula VARCHAR(16) DEFAULT 'harmonic',
  direction VARCHAR(8) DEFAULT 'out',
  maxHops INT DEFAULT 0,
  topK INT DEFAULT 10000
)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.NKGAccel).ClosenessGlobal(formula, direction, maxHops, topK)
    if $extract(result,1,3)="OK:" {{ set result = $extract(result,4,*) }}
    quit result
}}
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_Eigenvector(
  maxIter INT DEFAULT 30,
  tol DOUBLE DEFAULT 0.000001,
  topK INT DEFAULT 10000
)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.NKGAccel).EigenvectorGlobal(maxIter, tol, topK)
    if $extract(result,1,3)="OK:" {{ set result = $extract(result,4,*) }}
    quit result
}}
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_Leiden(
  maxLevels INT DEFAULT 10,
  gamma DOUBLE DEFAULT 1.0,
  tol DOUBLE DEFAULT 0.0001,
  topK INT DEFAULT 10000,
  memBudgetMB INT DEFAULT 256,
  randomSeed INT DEFAULT -1
)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.Communities).LeidenJsonAuto(maxLevels, gamma, tol, topK, memBudgetMB, randomSeed)
    if $extract(result,1,3)="OK:" {{ set result = $extract(result,4,*) }}
    quit result
}}
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_TriangleCount(
  topK INT DEFAULT 10000
)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.Communities).TriangleCountJson(topK)
    if $extract(result,1,3)="OK:" {{ set result = $extract(result,4,*) }}
    quit result
}}
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_SCC(
  topK INT DEFAULT 10000
)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.Communities).SCCJson(topK)
    if $extract(result,1,3)="OK:" {{ set result = $extract(result,4,*) }}
    quit result
}}
""",
            f"""
CREATE OR REPLACE FUNCTION {table_schema}.kg_KCore(
  topK INT DEFAULT 10000
)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{{
    set result = ##class(Graph.KG.Communities).KCoreJson(topK)
    if $extract(result,1,3)="OK:" {{ set result = $extract(result,4,*) }}
    quit result
}}
""",
        ]

    # ------------------------------------------------------------------
    # ObjectScript (.cls) deployment helpers
    # ------------------------------------------------------------------

    @staticmethod
    def check_objectscript_classes(cursor, conn=None) -> "IRISCapabilities":
        """
        Detect which ObjectScript classes are compiled in IRIS.

        Uses %Dictionary.ClassDefinition (fast); falls back to $CLASSMETHOD
        %Exists if the dictionary query fails (e.g. restricted namespace).

        Args:
            cursor: Active IRIS dbapi cursor (for SQL queries).
            conn:   Optional connection object for native API calls via
                    createIRIS(). When None, the cursor itself is tried.

        Returns:
            IRISCapabilities with objectscript_deployed, graphoperators_deployed,
            and kg_built flags set.
        """
        # Resolve the native-API handle: prefer explicit conn, else try cursor
        _native_target = conn if conn is not None else cursor

        caps = IRISCapabilities()

        # --- Graph.KG.PageRank (sentinel: reliably compiles; Edge conflicts with existing DDL table) ---
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM %Dictionary.ClassDefinition " "WHERE Name='Graph.KG.PageRank'"
            )
            row = cursor.fetchone()
            caps.objectscript_deployed = bool(row and row[0])
        except Exception:
            try:
                result = _call_classmethod(_native_target, "Graph.KG.PageRank", "%Exists", 1)
                caps.objectscript_deployed = bool(result)
            except Exception:
                caps.objectscript_deployed = False

        # --- iris.vector.graph.GraphOperators ---
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM %Dictionary.ClassDefinition "
                "WHERE Name='iris.vector.graph.GraphOperators'"
            )
            row = cursor.fetchone()
            caps.graphoperators_deployed = bool(row and row[0])
        except Exception:
            try:
                result = _call_classmethod(
                    _native_target, "iris.vector.graph.GraphOperators", "%Exists", 1
                )
                caps.graphoperators_deployed = bool(result)
            except Exception:
                caps.graphoperators_deployed = False

        # --- ^KG bootstrap marker via native API ---
        try:
            result = _call_classmethod(_native_target, "Graph.KG.Meta", "IsSet", "kg_built")
            caps.kg_built = bool(result)
        except Exception:
            caps.kg_built = False

        try:
            result = _call_classmethod(_native_target, "Graph.KG.Meta", "IsSet", "nkg_built")
            caps.nkg_built = bool(result)
        except Exception:
            caps.nkg_built = False

        return caps

    @staticmethod
    def deploy_objectscript_classes(cursor, iris_src_path: Path, conn=None) -> "IRISCapabilities":
        """
        Deploy ObjectScript .cls files to IRIS and return capability flags.

        Attempts to load .cls files via $system.OBJ.LoadDir through the native
        API bridge. If files are not accessible from the IRIS server filesystem,
        falls back to detection-only (check what's already compiled).

        Args:
            cursor:        Active IRIS dbapi cursor.
            iris_src_path: Path to iris_src/src/ directory containing .cls files.
            conn:          Connection for native API calls.

        Returns:
            IRISCapabilities reflecting what is compiled in IRIS after deployment.
        """
        _native = conn if conn is not None else cursor

        # Classes that must not exist in the namespace. Both declared
        # `SqlTableName = rdf_edges`; `Graph.KG.TestEdge` went first, and spec 227
        # deleted `Graph.KG.Edge` once the live container showed what a class-owned
        # rdf_edges actually is: `(ID, s, p, o_id, qualifiers, graph_id)` — no
        # `edge_id`, so the reification cascade in `_engine/nodes_edges.py` and every
        # read in `_engine/prov.py` get SQLCODE -29; no composite FK to
        # `nodes (graph_id, node_id)`; and a graph-blind `UNIQUE (s, p, o_id)`.
        # Whichever declaration compiles last owns the table, so this is not
        # housekeeping.
        #
        # Deleting the source file is only half of it. The server's source directory
        # is never pruned, so a container that once held the file keeps compiling it,
        # and the compiled class survives in the namespace either way. Delete the
        # file before LoadDir and the class after.
        # On a real v3.2.0 install `Graph.KG.Edge` *owns* Graph_KG.rdf_edges, so the
        # delete below takes every edge in the graph with it. The rows move to a
        # DDL-owned staging table first and come back into a rebuilt table after.
        _STALE_CLASSES = STALE_EDGE_CLASSES
        try:
            staged = GraphSchema.stage_class_owned_rdf_edges(cursor)
        except Exception as exc:
            logger.warning("rdf_edges rescue probe failed: %s", exc)
            staged = None

        src_dir = str(iris_src_path / "src") if iris_src_path else ""
        deployed = False
        for candidate_dir in ["/tmp/src/", "/irisdev/app/iris_src/src/", src_dir]:
            if not candidate_dir:
                continue
            try:
                for cls_name in _STALE_CLASSES:
                    file_path = f"{candidate_dir}/{cls_name.replace('.', '/')}.cls"
                    try:
                        _call_classmethod(_native, "%Library.File", "Delete", file_path)
                    except Exception:
                        pass
                try:
                    _call_classmethod(
                        _native, "%SYSTEM.OBJ", "Compile", "Graph.KG.GraphIndex", "ck-d"
                    )
                except Exception:
                    pass
                _call_classmethod(_native, "%SYSTEM.OBJ", "LoadDir", candidate_dir, "ck", "", 1)
                for cls_name in _STALE_CLASSES:
                    try:
                        _call_classmethod(_native, "%SYSTEM.OBJ", "Delete", cls_name, "ef")
                    except Exception:
                        pass
                logger.debug("ObjectScript classes loaded from %s", candidate_dir)
                deployed = True
                break
            except Exception:
                continue

        # After the delete, whether or not LoadDir found a directory: the class is
        # gone either way on the path that reached it, and a rescue left un-restored
        # is an install with no rdf_edges at all.
        GraphSchema.restore_rescued_rdf_edges(cursor, staged)

        if not deployed:
            logger.warning(
                "Could not load .cls files from any known path. "
                "Classes may need to be pre-loaded via docker cp + $system.OBJ.LoadDir."
            )

        try:
            return GraphSchema.check_objectscript_classes(cursor, conn=conn)
        except Exception as exc:
            logger.warning("check_objectscript_classes failed: %s", exc)
            return IRISCapabilities()

    @staticmethod
    def stage_class_owned_rdf_edges(cursor) -> Optional[int]:
        """Copy a class-owned ``rdf_edges`` into a DDL-owned table, before the delete.

        v3.2.0 shipped ``Graph.KG.Edge`` as
        ``[ Final, DdlAllowed, SqlTableName = rdf_edges ]``, so on a real 3.2.0
        install the class *owns* the table: the rows are that class's extent, and
        ``initialize_schema``'s ``CREATE TABLE`` was declined as already present.
        Deleting the class therefore deletes every edge in the graph — measured
        against a 3.2.0 install, ``Graph_KG.rdf_edges`` came back SQLCODE -30 and
        ``^Graph.KG.EdgeD`` was gone.

        So the rows move out first, into :data:`RESCUE_STAGING_TABLE`, which is
        DDL-owned and survives the delete.

        Returns:
            The number of rows staged, or ``None`` when there is nothing to rescue —
            a fresh install, or one already carrying a DDL-owned table.
        """
        if not GraphSchema._stale_edge_class_exists(cursor):
            return None

        staging = f"Graph_KG.{RESCUE_STAGING_TABLE}"
        try:
            cursor.execute(f"DROP TABLE {staging}")
        except Exception:
            pass
        cursor.execute(
            f"CREATE TABLE {staging} (\n"
            "  s          VARCHAR(256) %EXACT,\n"
            "  p          VARCHAR(128) %EXACT,\n"
            "  o_id       VARCHAR(256) %EXACT,\n"
            "  qualifiers VARCHAR(64000),\n"
            "  graph_id   VARCHAR(256) %EXACT\n"
            ")"
        )
        # The class-owned table has no `edge_id` — that column is spec 227's, and its
        # absence is why the class had to go. v3.2.0 declared
        # `graph_id NOT NULL DEFAULT $c(0)`, so a default-graph edge reaches here as
        # NULL or as CHAR(0); both mean the default graph rather than a third one, and
        # both have to read as '' because that is the spelling 4.0.0's FK to
        # `nodes (graph_id, node_id)` compares against.
        cursor.execute(
            f"INSERT INTO {staging} (s, p, o_id, qualifiers, graph_id) "
            "SELECT s, p, o_id, qualifiers, COALESCE(NULLIF(graph_id, CHAR(0)), '') "
            "FROM Graph_KG.rdf_edges"
        )
        cursor.execute(f"SELECT COUNT(*) FROM {staging}")
        row = cursor.fetchone()
        staged = int(row[0]) if row and row[0] is not None else 0
        logger.warning(
            "Graph_KG.rdf_edges is owned by a v3.2.0 class; %s row(s) staged into %s "
            "before the class is deleted",
            staged,
            staging,
        )
        return staged

    @staticmethod
    def restore_rescued_rdf_edges(cursor, staged: Optional[int]) -> None:
        """Re-create ``rdf_edges`` from the canonical DDL and put the rows back.

        v3.2.0's FK was ``nodes (node_id)`` alone while its rows already carried a
        graph, so a 3.2.0 install can legally hold an edge in graph ``g`` whose
        endpoint row sits in the default graph. 4.0.0's FK is composite, so that edge
        cannot go back in: it fails ``fk_edges_dest`` with SQLCODE -121 and used to
        take the whole restore with it. Spec 227 Story 5's rule decides what happens
        instead — place what resolves, quarantine what does not, delete nothing, and
        never place a row by guessing at its graph.

        Args:
            cursor: Active IRIS dbapi cursor.
            staged: What :meth:`stage_class_owned_rdf_edges` returned. ``None`` means
                nothing was rescued and this is a no-op.

        Raises:
            RdfEdgesRescueError: when the placed and quarantined rows together do not
                account for every staged row, and for any error raised along the way.
                The staging table is deliberately left in place — it is the only
                surviving copy. The error type matters: the deploy call site re-raises
                this one and logs every other exception at DEBUG as "expected in
                Docker", which is how a failed rebuild once produced an install with no
                edge table and a success report.
        """
        if staged is None:
            return

        try:
            GraphSchema._restore_rescued_rdf_edges(cursor, staged)
        except RdfEdgesRescueError:
            raise
        except Exception as exc:
            raise RdfEdgesRescueError(
                f"Graph_KG.rdf_edges could not be rebuilt after rescuing {staged} "
                f"row(s) out of the v3.2.0 class-owned table: {exc}. The rows are still "
                f"in Graph_KG.{RESCUE_STAGING_TABLE}, which is left in place on purpose "
                "— nothing has been deleted. Re-run initialize_schema() once the cause "
                "is cleared, or copy them back by hand."
            ) from exc

    @staticmethod
    def _restore_rescued_rdf_edges(cursor, staged: int) -> None:
        """The body of :meth:`restore_rescued_rdf_edges`, which wraps what this raises."""
        staging = f"Graph_KG.{RESCUE_STAGING_TABLE}"
        unplaced = f"Graph_KG.{RESCUE_UNPLACED_TABLE}"
        endpoint_resolves = (
            "EXISTS (SELECT 1 FROM Graph_KG.nodes n1 "
            f"       WHERE n1.graph_id = {staging}.graph_id AND n1.node_id = {staging}.s) "
            "AND EXISTS (SELECT 1 FROM Graph_KG.nodes n2 "
            f"       WHERE n2.graph_id = {staging}.graph_id AND n2.node_id = {staging}.o_id)"
        )

        # The rebuilt table's two foreign keys point at `nodes (graph_id, node_id)`, and
        # IRIS refuses a foreign key whose referenced columns carry no unique key:
        # SQLCODE -314, measured against a real 3.2.0 install, whose `nodes` declares
        # `uq_nodes_nodeid UNIQUE (node_id)` and nothing composite. That key is added by
        # the embeddings migration's `_rekey_children`, which `upgrade_to_4_0_0` runs
        # *after* `initialize_schema` deleted the class — too late to be of any use here.
        # So the restore adds it itself. Additive and tolerated: v3.2.0's `node_id` was
        # globally unique, so `(graph_id, node_id)` cannot collide, `uq_nodes_nodeid`
        # stays until `_rekey_children` drops it, and on an install that already has the
        # composite key the ALTER simply fails as a duplicate.
        try:
            cursor.execute(
                "ALTER TABLE Graph_KG.nodes ADD CONSTRAINT uq_nodes_graph_node "
                "UNIQUE (graph_id, node_id)"
            )
        except Exception as exc:
            logger.debug("uq_nodes_graph_node already present or not addable: %s", exc)

        cursor.execute(RDF_EDGES_DDL)
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) "
            f"SELECT s, p, o_id, qualifiers, graph_id FROM {staging} "
            f"WHERE {endpoint_resolves}"
        )
        cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
        row = cursor.fetchone()
        restored = int(row[0]) if row and row[0] is not None else 0

        quarantined = 0
        if restored != staged:
            try:
                cursor.execute(f"DROP TABLE {unplaced}")
            except Exception:
                pass
            cursor.execute(
                f"CREATE TABLE {unplaced} (\n"
                "  s          VARCHAR(256) %EXACT,\n"
                "  p          VARCHAR(128) %EXACT,\n"
                "  o_id       VARCHAR(256) %EXACT,\n"
                "  qualifiers VARCHAR(64000),\n"
                "  graph_id   VARCHAR(256) %EXACT\n"
                ")"
            )
            cursor.execute(
                f"INSERT INTO {unplaced} (s, p, o_id, qualifiers, graph_id) "
                f"SELECT s, p, o_id, qualifiers, graph_id FROM {staging} "
                f"WHERE NOT ({endpoint_resolves})"
            )
            cursor.execute(f"SELECT COUNT(*) FROM {unplaced}")
            row = cursor.fetchone()
            quarantined = int(row[0]) if row and row[0] is not None else 0

        if restored + quarantined != staged:
            raise RdfEdgesRescueError(
                f"rescued {staged} row(s) out of a class-owned Graph_KG.rdf_edges but "
                f"placed {restored} and quarantined {quarantined}. The rows are still "
                f"in {staging}, which is left in place on purpose — copy them back "
                "before writing anything else."
            )

        cursor.execute(f"DROP TABLE {staging}")
        if quarantined:
            logger.warning(
                "Graph_KG.rdf_edges rebuilt; %s row(s) restored and %s row(s) held in "
                "%s because an endpoint has no node row in the graph the edge claims. "
                "Nothing was deleted: place them by creating the missing node rows, or "
                "by correcting the edge's graph_id, then copy them back",
                restored,
                quarantined,
                unplaced,
            )
        else:
            logger.warning("Graph_KG.rdf_edges rebuilt; %s row(s) restored", restored)

    @staticmethod
    def _stale_edge_class_exists(cursor) -> bool:
        """Is one of the v3.2.0 classes that owned ``rdf_edges`` still compiled?"""
        names = ", ".join(f"'{name}'" for name in STALE_EDGE_CLASSES)
        cursor.execute(
            f"SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name IN ({names})"
        )
        row = cursor.fetchone()
        return bool(row and row[0])

    @staticmethod
    def bootstrap_kg_global(cursor, conn=None) -> bool:
        """
        Backfill the ^KG global from existing SQL edge data using BuildKG().

        Only runs once — records completion in Graph.KG.Meta so subsequent
        calls are no-ops.  Safe to call on an empty database (returns False).

        Args:
            cursor: Active IRIS dbapi cursor (for SQL queries).
            conn:   Optional connection for native API calls via createIRIS().
                    When None, the cursor itself is tried.

        Returns:
            True if BuildKG() was called, False if already done or no edges.
        """
        _native = conn if conn is not None else cursor

        # Already done?
        try:
            result = _call_classmethod(_native, "Graph.KG.Meta", "IsSet", "kg_built")
            if result:
                return False
        except Exception:
            return False

        # Any edges to backfill?
        try:
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
            row = cursor.fetchone()
            if not row or not row[0]:
                return False
        except Exception:
            return False

        # Run BuildKG via native API
        try:
            _call_classmethod(_native, "Graph.KG.Traversal", "BuildKG")
        except Exception as exc:
            logger.warning("BuildKG() failed: %s", exc)
            return False

        # Record completion
        try:
            _call_classmethod(_native, "Graph.KG.Meta", "Set", "kg_built", "1")
            _call_classmethod(_native, "Graph.KG.Meta", "Set", "nkg_built", "1")
        except Exception as exc:
            logger.warning("Could not record kg_built in Graph.KG.Meta: %s", exc)

        logger.info("^KG and ^NKG globals bootstrapped from existing rdf_edges rows")
        return True
