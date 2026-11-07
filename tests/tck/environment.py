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

    context._tck_target = (container_name, port)
    context.tck_reconnects = 0
    context.named_graphs = dict(NAMED_GRAPHS)
    # Cache: graph_name -> label, only when currently built in DB
    context._named_graph_labels = {}
    try:
        _session_setup(context)
    except Exception as exc:  # 4.6.0 R311: a connection lost during setup failed every scenario of the area
        if not _connection_lost(exc):
            raise
        context.tck_reconnects = 1
        logger.warning("TCK reconnect #1 during the session setup: %s", str(exc).splitlines()[0][:300])
        with contextlib.suppress(Exception):
            context.conn.close()
        _session_setup(context)


def _session_setup(context):
    context.conn = _connect(*context._tck_target)
    context.engine = _new_engine(context.conn)
    # A failed schema init raises: the run would score a namespace without IVG's tables.
    context.engine.initialize_schema(auto_deploy_objectscript=False)

    # Clean up any leftover data from previous test sessions
    _flush_all_tck_data(context)
    _enable_tck_multigraph(context)


# Tags that upstream uses to take a scenario out of the suite. The vendored TCK
# has one: Graph5 [2]. @skipGrammarCheck / @skipStyleCheck are for the
# openCypher grammar and style tooling, not for engines, so they still run.
UPSTREAM_IGNORE_TAGS = frozenset({"ignore"})


def _upstream_ignored(tags) -> bool:
    """True when a scenario is tagged @ignore upstream and should be skipped.

    ``IVG_TCK_RUN_IGNORED=1`` runs such scenarios anyway (scripts/tck/summarize.py
    still reports them as ignored, outside the denominator).
    """
    if os.environ.get("IVG_TCK_RUN_IGNORED") == "1":
        return False
    return bool(set(tags) & UPSTREAM_IGNORE_TAGS)


def before_scenario(context, scenario):
    if _upstream_ignored(getattr(scenario, "effective_tags", ())):
        scenario.skip(reason="tagged @ignore upstream")
    context.scenario_label = f"TCK_{uuid4().hex[:8]}"
    context.params = {}
    context.last_result = None
    context.last_error = None
    context.last_query = None
    context._capture_fields = {}
    context._used_named_graph = None  # track if this scenario uses a named graph
    context._tck_procedures = {}  # TCK test procedure registry (for CALL support)


def _new_engine(conn):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=768)


def _connection_lost(text) -> bool:
    """The driver's errors for a connection that is gone: every later statement on it fails the same way."""
    text = str(text)
    return "<COMMUNICATION" in text or "Connection closed" in text


def _set_session(context, name, value) -> None:
    """Set a session attribute from inside a scenario: behave keeps what a hook sets during a scenario in that
    scenario's layer and pops it at the scenario's end, so the fresh connection went with the first scenario that
    reconnected (every later one reconnected again). The root layer holds before_all's attributes."""
    stack = getattr(context, "_stack", None)
    if not stack:
        setattr(context, name, value)
        return
    for frame in stack[:-1]:
        frame.pop(name, None)
    stack[-1][name] = value


def _reconnect(context, scenario, reason: str) -> None:
    """A fresh connection and engine for the rest of the run, logged (4.6.0 R311: one lost connection failed every
    later scenario on its shard in teardown, 81 false failures)."""
    with contextlib.suppress(Exception):
        context.conn.close()  # it is gone already; closing it frees the driver's side
    conn = _connect(*context._tck_target)
    _set_session(context, "conn", conn)
    _set_session(context, "engine", _new_engine(conn))
    _enable_tck_multigraph(context)
    _set_session(context, "tck_reconnects", getattr(context, "tck_reconnects", 0) + 1)
    logger.warning("TCK reconnect #%d after %s: %s", context.tck_reconnects, getattr(scenario, "name", scenario),
                   reason.splitlines()[0][:300])


def _teardown_or_reconnect(context, scenario, label: str) -> None:
    """Tear the scenario down; when its connection is lost, reconnect, tear down again on the fresh one, and still
    fail this scenario (once) so the run reports it."""
    try:
        _teardown_label(context, label)
    except TeardownFailed as exc:
        if not _connection_lost(exc):
            raise
        _reconnect(context, scenario, str(exc))
        try:
            _teardown_label(context, label)
        except TeardownFailed as again:
            raise TeardownFailed(f"{exc}\n  reconnected; the teardown failed again:\n  {again}") from again
        raise TeardownFailed(f"{exc}\n  reconnected; the teardown then succeeded on the fresh connection") from exc


def after_scenario(context, scenario):
    from tests.tck import capture
    capture.flush(context, scenario)

    label = getattr(context, "scenario_label", None)
    if label:
        _teardown_or_reconnect(context, scenario, label)
        if label.startswith("TCK_BINARY_TREE") and hasattr(context, "_named_graph_labels"):
            # Named-graph scenario: remove it from the built-cache so the next scenario recreates it
            context._named_graph_labels = {
                k: v for k, v in context._named_graph_labels.items()
                if v != label
            }


def after_all(context):
    if getattr(context, "tck_reconnects", 0):
        logger.warning("TCK run needed %d reconnect(s); see 'TCK reconnect' above", context.tck_reconnects)
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
    # raises: a run that could not turn the mode on would report the single-edge figure as the multigraph one
    context.engine.set_multigraph(None, True)


def _flush_all_tck_data(context):
    """Delete all TCK data from the DB (for session start/end cleanup).

    Delete order must respect FK constraints: rdf_props/rdf_labels/rdf_edges
    must be deleted before nodes (FK rdf_labels.s -> nodes.node_id).
    """
    # raises: rows a failed flush leaves are visible to every later `MATCH ()`
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


class TeardownFailed(AssertionError):
    """A scenario's rows could not all be removed: they would change the next scenarios' answers."""


def _teardown_label(context, label: str):
    # The Cypher delete can leave rows behind (e.g. a node keeps its other labels
    # while its isolation label goes), and a leftover label name hides the next
    # scenario's +labels (spec 229). So the ids of the nodes carrying the isolation
    # label are read before the Cypher delete and swept from every table after it.
    # Ids are bound, not correlated: a DELETE correlated on its own table can silently
    # delete nothing on IRIS; rdf_labels goes before nodes (FK rdf_labels.s -> nodes).
    # Every sweep is tried; any that failed raises TeardownFailed at the end (4.6.0 R310:
    # they were each under suppress(Exception), so leftover rows went unreported).
    store = getattr(getattr(context, "engine", None), "_store", None)
    schema = _db_schema(context)
    failures: list = []

    def attempt(what, fn):
        try:
            return fn()
        except Exception as exc:
            failures.append(f"{what}: {type(exc).__name__}: {exc}")
            return None

    def read_ids():
        cursor = store.conn.cursor()
        cursor.execute(f"SELECT DISTINCT s FROM {schema}.rdf_labels WHERE label = ?", [label])
        return [r[0] for r in cursor.fetchall()]

    def sweep_ids():
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

    def sweep_unlabelled():
        # Orphaned nodes (no labels) left by anonymous CREATE endpoints in main test
        # queries that weren't given the isolation label. Their relationships go first:
        # rdf_edges references nodes, so one `CREATE ()-[:R]->()` failed the whole node
        # sweep (-124) and every later scenario's unlabelled nodes piled up, visible to `MATCH ()`.
        cursor = store.conn.cursor()
        unlabelled = (
            f"SELECT node_id FROM {schema}.nodes WHERE node_id NOT IN "
            f"(SELECT DISTINCT s FROM {schema}.rdf_labels)"
        )
        cursor.execute(f"DELETE FROM {schema}.rdf_edges WHERE s IN ({unlabelled}) OR o_id IN ({unlabelled})")
        cursor.execute(
            f"DELETE FROM {schema}.nodes WHERE node_id NOT IN (SELECT DISTINCT s FROM {schema}.rdf_labels)"
        )
        store.conn.commit()

    def sweep_orphan_props():
        # rdf_props has no FK to nodes: drop properties the node sweep above orphaned,
        # so side-effect snapshots (spec 229) do not grow across the run.
        cursor = store.conn.cursor()
        cursor.execute(f"DELETE FROM {schema}.rdf_props WHERE s NOT IN (SELECT node_id FROM {schema}.nodes)")
        store.conn.commit()

    has_store = bool(store and hasattr(store, "conn"))
    ids = (attempt("reading the label's node ids", read_ids) or []) if has_store else []
    attempt(
        "the Cypher DETACH DELETE",
        lambda: context.engine.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", {}, raise_on_error=True),
    )
    if has_store:
        if ids:
            attempt("the label-scoped sweep", sweep_ids)
        attempt("the unlabelled-node sweep", sweep_unlabelled)
        attempt("the orphaned-property sweep", sweep_orphan_props)
    if failures:
        raise TeardownFailed(f"teardown of {label} failed:\n  " + "\n  ".join(failures))


def _ensure_named_graph(context, graph_name: str, label: str):
    """Build a named graph fixture under its session label if not already present.

    Raises SetupFailed when the count or the build fails: until 4.6.0 R310 a failed
    count was dropped and a failed build only logged, so the scenario ran on no graph.
    """
    from tests.tck.steps.graph_setup import SetupFailed, _inject_label, _sync_kg, run_setup_query

    cql = {"binary-tree-1": _BINARY_TREE_1_CQL, "binary-tree-2": _BINARY_TREE_2_CQL}[graph_name]
    count_q = f"MATCH (n:{label}) RETURN count(n) AS cnt"
    try:
        result = context.engine.execute_cypher(count_q, {}, raise_on_error=True)
    except Exception as exc:
        raise SetupFailed(f"counting named graph {graph_name!r} failed: {type(exc).__name__}: {exc}") from exc
    row0 = result.rows[0] if result.rows else None
    # rows may be a list or dict depending on IVGResult format
    cnt = (row0.get("cnt", 0) if isinstance(row0, dict) else row0[0]) if row0 else 0
    if cnt > 0:
        return  # already built
    run_setup_query(context, _inject_label(cql.strip(), label), what=f"building named graph {graph_name!r}")
    # Rebuild ^KG so variable-length path queries work on this named graph.
    _sync_kg(context)
