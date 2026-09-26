"""behave environment hooks for TCK harness."""
from __future__ import annotations

import contextlib
import logging
import os
import subprocess
from uuid import uuid4

logger = logging.getLogger(__name__)

# Named graphs: scenario-scoped fixtures rebuilt as needed.
NAMED_GRAPHS = {
    "binary-tree-1": "TCK_BINARY_TREE_1",
    "binary-tree-2": "TCK_BINARY_TREE_2",
}

# Binary tree structures — exact copies of vendor/opencypher/tck/graphs/binary-tree-*/
# These must match the graphs used by the TCK feature files exactly.
_BINARY_TREE_1_CQL = """
CREATE (a:A {name: 'a'}),
       (b1:X {name: 'b1'}),
       (b2:X {name: 'b2'}),
       (b3:X {name: 'b3'}),
       (b4:X {name: 'b4'}),
       (c11:X {name: 'c11'}),
       (c12:X {name: 'c12'}),
       (c21:X {name: 'c21'}),
       (c22:X {name: 'c22'}),
       (c31:X {name: 'c31'}),
       (c32:X {name: 'c32'}),
       (c41:X {name: 'c41'}),
       (c42:X {name: 'c42'})
CREATE (a)-[:KNOWS]->(b1),
       (a)-[:KNOWS]->(b2),
       (a)-[:FOLLOWS]->(b3),
       (a)-[:FOLLOWS]->(b4)
CREATE (b1)-[:FRIEND]->(c11),
       (b1)-[:FRIEND]->(c12),
       (b2)-[:FRIEND]->(c21),
       (b2)-[:FRIEND]->(c22),
       (b3)-[:FRIEND]->(c31),
       (b3)-[:FRIEND]->(c32),
       (b4)-[:FRIEND]->(c41),
       (b4)-[:FRIEND]->(c42)
CREATE (b1)-[:FRIEND]->(b2),
       (b2)-[:FRIEND]->(b3),
       (b3)-[:FRIEND]->(b4),
       (b4)-[:FRIEND]->(b1)
"""

_BINARY_TREE_2_CQL = """
CREATE (a:A {name: 'a'}),
       (b1:X {name: 'b1'}),
       (b2:X {name: 'b2'}),
       (b3:X {name: 'b3'}),
       (b4:X {name: 'b4'}),
       (c11:X {name: 'c11'}),
       (c12:Y {name: 'c12'}),
       (c21:X {name: 'c21'}),
       (c22:Y {name: 'c22'}),
       (c31:X {name: 'c31'}),
       (c32:Y {name: 'c32'}),
       (c41:X {name: 'c41'}),
       (c42:Y {name: 'c42'})
CREATE (a)-[:KNOWS]->(b1),
       (a)-[:KNOWS]->(b2),
       (a)-[:FOLLOWS]->(b3),
       (a)-[:FOLLOWS]->(b4)
CREATE (b1)-[:FRIEND]->(c11),
       (b1)-[:FRIEND]->(c12),
       (b2)-[:FRIEND]->(c21),
       (b2)-[:FRIEND]->(c22),
       (b3)-[:FRIEND]->(c31),
       (b3)-[:FRIEND]->(c32),
       (b4)-[:FRIEND]->(c41),
       (b4)-[:FRIEND]->(c42)
CREATE (b1)-[:FRIEND]->(b2),
       (b2)-[:FRIEND]->(b3),
       (b3)-[:FRIEND]->(b4),
       (b4)-[:FRIEND]->(b1)
"""


def before_all(context):
    """Session setup: connect to ivg-iris, initialize schema only."""
    container_name = os.environ.get("IVG_TEST_CONTAINER") or os.environ.get("IVG_CONTAINER", "ivg-iris")
    port = int(os.environ.get("IVG_PORT", "21972"))

    conn = _connect(container_name, port)
    context.conn = conn
    context.named_graphs = dict(NAMED_GRAPHS)
    # Cache: graph_name -> label, only when currently built in DB
    context._named_graph_labels = {}

    from iris_vector_graph.engine import IRISGraphEngine
    context.engine = IRISGraphEngine(conn, embedding_dimension=768)
    schema_ok = True
    try:
        context.engine.initialize_schema(auto_deploy_objectscript=False)
    except Exception as e:
        logger.warning("Schema init: %s", e)
        schema_ok = False

    # If initialize_schema caused a stale/broken connection, reconnect before proceeding.
    if not schema_ok:
        try:
            conn2 = _connect(container_name, port)
            context.conn = conn2
            context.engine = IRISGraphEngine(conn2, embedding_dimension=768)
            logger.info("Reconnected after schema init failure")
        except Exception as e2:
            logger.warning("Reconnect after schema init failure: %s", e2)

    # Clean up any leftover data from previous test sessions
    _flush_all_tck_data(context)
    _enable_tck_multigraph(context)


def before_scenario(context, scenario):
    context.scenario_label = f"TCK_{uuid4().hex[:8]}"
    context.params = {}
    context.last_result = None
    context.last_error = None
    context._used_named_graph = None  # track if this scenario uses a named graph
    context._tck_procedures = {}  # TCK test procedure registry (for CALL support)


def after_scenario(context, scenario):
    label = getattr(context, "scenario_label", None)
    if label:
        if label.startswith("TCK_BINARY_TREE"):
            # Named-graph scenario: tear down this scenario's named graph data
            _teardown_label(context, label)
            # Remove from the built-cache so next scenario recreates if needed
            if hasattr(context, "_named_graph_labels"):
                context._named_graph_labels = {
                    k: v for k, v in context._named_graph_labels.items()
                    if v != label
                }
        else:
            _teardown_label(context, label)


def after_all(context):
    # Final cleanup
    _flush_all_tck_data(context)
    # Leave the default graph as other suites expect it: single-edge.
    if os.environ.get("IVG_TCK_MULTIGRAPH") != "0":
        with contextlib.suppress(Exception):
            context.engine.set_multigraph(None, False)
    with contextlib.suppress(Exception):
        context.conn.close()


def _tck_namespace() -> str:
    """Namespace the harness connects to: IVG_TCK_NAMESPACE, default USER."""
    return (os.environ.get("IVG_TCK_NAMESPACE") or "").strip() or "USER"


def _connect(container_name: str, port: int):
    """Connect to the IRIS container.

    Strategy (in order):
    1. OrbStack DNS: {container}.orb.local resolves to a routable IP — use :1972 directly.
    2. Socat proxy / host port forwarding (port != 21972).
    3. iris_devtester fallback.
    """
    import socket as _socket
    _orb_host = f"{container_name}.orb.local"
    try:
        _orb_ip = _socket.gethostbyname(_orb_host)
        import iris.dbapi as _dbapi
        try:
            conn = _dbapi.connect(
                hostname=_orb_ip, port=1972, namespace=_tck_namespace(),
                username="_SYSTEM", password="SYS",
            )
            logger.info("TCK connected to %s via OrbStack %s (%s):1972", container_name, _orb_host, _orb_ip)
            return conn
        except Exception as e:
            logger.warning("OrbStack %s:1972 failed (%s) — falling back", _orb_ip, e)
    except _socket.gaierror:
        pass  # not OrbStack

    if port != 21972:
        import iris.dbapi as _dbapi
        try:
            conn = _dbapi.connect(
                hostname="localhost", port=port, namespace=_tck_namespace(),
                username="_SYSTEM", password="SYS",
            )
            logger.info("TCK harness connected to %s via localhost:%s", container_name, port)
            return conn
        except Exception as e:
            logger.warning("Direct connect to localhost:%s failed (%s) — trying iris_devtester", port, e)

    if _tck_namespace() != "USER":
        # iris_devtester attaches to USER only; never fall back to a namespace
        # other than the one requested.
        raise RuntimeError(f"Cannot connect to namespace {_tck_namespace()} on '{container_name}'")

    try:
        from iris_devtester import IRISContainer as IRC
        fresh = IRC.attach(container_name)
        fresh._connection = None
        conn = fresh.get_connection()
        logger.info("TCK harness connected to %s via iris_devtester", container_name)
        return conn
    except Exception as e:
        raise RuntimeError(
            f"Cannot connect to IRIS container '{container_name}' (port {port}). "
            f"Start it with: scripts/test-container.sh up\n"
            f"Original error: {e}"
        ) from e


def _db_schema(context) -> str:
    """Return the schema prefix used by this engine (e.g. 'Graph_KG' or 'SQLUser')."""
    engine = getattr(context, "engine", None)
    if engine is None:
        return "SQLUser"
    return getattr(engine, "_schema_prefix", "SQLUser")


def _enable_tck_multigraph(context):
    """Turn multigraph mode on for the default graph (spec 234 SC-001).

    The TCK assumes a property graph, where two relationships may share type and
    endpoints. ``IVG_TCK_MULTIGRAPH=0`` runs the suite with the mode off.
    """
    if os.environ.get("IVG_TCK_MULTIGRAPH") == "0":
        return
    try:
        context.engine.set_multigraph(None, True)
    except Exception as e:
        logger.warning("Multigraph mode not enabled for the TCK: %s", e)


def _flush_all_tck_data(context):
    """Delete all TCK data from the DB (for session start/end cleanup).

    Delete order must respect FK constraints: rdf_props/rdf_labels/rdf_edges
    must be deleted before nodes (FK rdf_labels.s -> nodes.node_id).
    """
    with contextlib.suppress(Exception):
        store = getattr(context.engine, "_store", None)
        if store and hasattr(store, "conn"):
            schema = _db_schema(context)
            cursor = store.conn.cursor()
            # Delete in FK-safe order: dependents first
            cursor.execute(f"DELETE FROM {schema}.rdf_props")
            cursor.execute(f"DELETE FROM {schema}.rdf_labels")
            cursor.execute(f"DELETE FROM {schema}.rdf_edges")
            cursor.execute(f"DELETE FROM {schema}.nodes")
            store.conn.commit()
            cursor.close()


def _teardown_label(context, label: str):
    # The Cypher delete can leave rows behind (e.g. a node keeps its other labels
    # while its isolation label goes), and a leftover label name hides the next
    # scenario's +labels (spec 229). So the ids of the nodes carrying the isolation
    # label are read before the Cypher delete and swept from every table after it.
    # Ids are bound, not correlated: a DELETE correlated on its own table can silently
    # delete nothing on IRIS; rdf_labels goes before nodes (FK rdf_labels.s -> nodes).
    ids: list = []
    store = getattr(getattr(context, "engine", None), "_store", None)
    schema = _db_schema(context)
    with contextlib.suppress(Exception):
        if store and hasattr(store, "conn"):
            cursor = store.conn.cursor()
            cursor.execute(f"SELECT DISTINCT s FROM {schema}.rdf_labels WHERE label = ?", [label])
            ids = [r[0] for r in cursor.fetchall()]
    with contextlib.suppress(Exception):
        context.engine.execute_cypher(
            f"MATCH (n:{label}) DETACH DELETE n", {}
        )
    with contextlib.suppress(Exception):
        if ids:
            cursor = store.conn.cursor()
            for i in range(0, len(ids), 200):
                chunk = ids[i:i + 200]
                marks = ", ".join("?" for _ in chunk)
                cursor.execute(
                    f"DELETE FROM {schema}.rdf_edges WHERE s IN ({marks}) OR o_id IN ({marks})",
                    chunk + chunk,
                )
                cursor.execute(f"DELETE FROM {schema}.rdf_props WHERE s IN ({marks})", chunk)
                cursor.execute(f"DELETE FROM {schema}.rdf_labels WHERE s IN ({marks})", chunk)
                cursor.execute(f"DELETE FROM {schema}.nodes WHERE node_id IN ({marks})", chunk)
            store.conn.commit()
    # Clean up orphaned nodes (no labels) left by anonymous CREATE endpoints
    # in main test queries that weren't given the isolation label.
    with contextlib.suppress(Exception):
        store = getattr(context.engine, "_store", None)
        if store and hasattr(store, "conn"):
            schema = _db_schema(context)
            cursor = store.conn.cursor()
            cursor.execute(
                f"DELETE FROM {schema}.nodes WHERE node_id NOT IN "
                f"(SELECT DISTINCT s FROM {schema}.rdf_labels)"
            )
            store.conn.commit()
    # rdf_props has no FK to nodes: drop properties the node sweep above orphaned,
    # so side-effect snapshots (spec 229) do not grow across the run.
    with contextlib.suppress(Exception):
        store = getattr(context.engine, "_store", None)
        if store and hasattr(store, "conn"):
            schema = _db_schema(context)
            cursor = store.conn.cursor()
            cursor.execute(
                f"DELETE FROM {schema}.rdf_props WHERE s NOT IN "
                f"(SELECT node_id FROM {schema}.nodes)"
            )
            store.conn.commit()


def _ensure_named_graph(context, graph_name: str, label: str):
    """Build a named graph fixture under its session label if not already present."""
    try:
        result = context.engine.execute_cypher(
            f"MATCH (n:{label}) RETURN count(n) AS cnt", {}
        )
        row0 = result.rows[0] if result.rows else None
        if row0 is not None:
            # rows may be a list or dict depending on IVGResult format
            cnt = row0.get("cnt", 0) if isinstance(row0, dict) else (row0[0] if row0 else 0)
        else:
            cnt = 0
        if cnt > 0:
            return  # already built
    except Exception:
        pass

    cql_map = {
        "binary-tree-1": _BINARY_TREE_1_CQL,
        "binary-tree-2": _BINARY_TREE_2_CQL,
    }
    cql = cql_map.get(graph_name)
    if not cql:
        return

    # Inject session label into CREATE
    from tests.tck.steps.graph_setup import _inject_label, _sync_kg
    labeled_cql = _inject_label(cql.strip(), label)
    try:
        context.engine.execute_cypher(labeled_cql, {})
    except Exception as e:
        logger.warning("Failed to create named graph '%s': %s", graph_name, e)
    # Rebuild ^KG so variable-length path queries work on this named graph.
    _sync_kg(context)
