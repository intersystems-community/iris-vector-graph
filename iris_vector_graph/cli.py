from __future__ import annotations

import json
import os
import sys

from .constants import DEFAULT_EMBEDDING_DIMENSION

try:
    import click
    _HAS_CLICK = True
except ImportError:
    _HAS_CLICK = False


def _require_click():
    if not _HAS_CLICK:
        print("click is required for the IVG CLI: pip install 'iris-vector-graph[cli]'")
        sys.exit(1)


def _client(url: str, api_key: str | None):
    from iris_vector_graph.sdk import IVGClient
    return IVGClient(url, api_key=api_key)


def _engine_from_env(**overrides):
    """An engine connected straight to IRIS, not through the HTTP server.

    The inventory (spec 227, FR-038) is a question about this namespace's tables and
    class dictionary, and the operator asking it is usually asking *because* the server
    is not the thing they are unsure about. Everything else in this CLI goes through
    `IVGClient`; this one command does not, so it reads the same environment variables
    `server start` forwards.
    """
    from iris_vector_graph.engine import IRISGraphEngine

    host = overrides.get("host") or os.environ.get("IRIS_HOST")
    if not host:
        raise RuntimeError(
            "No IRIS connection: set IRIS_HOST (and IRIS_PORT / IRIS_NAMESPACE / "
            "IRIS_PASSWORD) or pass --iris-host."
        )
    port = int(overrides.get("port") or os.environ.get("IRIS_PORT", "1972"))
    namespace = overrides.get("namespace") or os.environ.get("IRIS_NAMESPACE", "USER")
    username = overrides.get("username") or os.environ.get("IRIS_USERNAME", "_SYSTEM")
    password = overrides.get("password") or os.environ.get("IRIS_PASSWORD", "SYS")

    import iris

    conn = iris.dbapi.connect(
        hostname=host,
        port=port,
        namespace=namespace,
        username=username,
        password=password,
    )
    return IRISGraphEngine(conn)


#: Column order of the printed inventory. `index_error` is not a column — it is too wide
#: for one — and is printed under the table for the routes that have one.
_INVENTORY_COLUMNS = (
    "graph_id",
    "model_key",
    "table_name",
    "dimension",
    "dtype",
    "row_count",
    "index_state",
    "index_name",
    "recall_measured",
)


def _inventory_payload(rows: list) -> dict:
    """The inventory as plain data, with the routed-table count FR-038 asks for.

    `routed_tables` counts rows that name a table, not rows: a graph with nodes and no
    route is reported (US4-3) and is not a routed table, and conflating the two makes the
    number an operator watches grow when nothing was created.
    """
    from dataclasses import asdict

    inventory = [asdict(row) for row in rows]
    return {
        "inventory": inventory,
        "routed_tables": sum(1 for row in inventory if row.get("table_name")),
    }


def _print_inventory(payload: dict):
    rows = payload["inventory"]
    table = [
        [
            row.get(column) if row.get(column) is not None else "-"
            for column in _INVENTORY_COLUMNS
        ]
        for row in rows
    ]
    if table:
        _print_table(list(_INVENTORY_COLUMNS), table)
    count = payload["routed_tables"]
    print(f"{count} routed table{'s' if count != 1 else ''}")
    for row in rows:
        if row.get("index_error"):
            print(f"  {row.get('table_name')}: {row['index_state']} — {row['index_error']}")


def _print_table(columns: list, rows: list):
    if not rows:
        print("(no results)")
        return
    widths = [max(len(str(c)), max((len(str(r[i])) for r in rows), default=0))
              for i, c in enumerate(columns)]
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    header = "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(columns)) + " |"
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        print("| " + " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)) + " |")
    print(sep)
    print(f"{len(rows)} row{'s' if len(rows) != 1 else ''}")


if _HAS_CLICK:
    @click.group()
    @click.option("--url", envvar="IVG_URL", default="http://localhost:8200", show_default=True)
    @click.option("--api-key", envvar="IVG_API_KEY", default=None)
    @click.pass_context
    def cli(ctx, url, api_key):
        ctx.ensure_object(dict)
        ctx.obj["url"] = url
        ctx.obj["api_key"] = api_key

    @cli.command()
    @click.pass_context
    def connect(ctx):
        c = _client(ctx.obj["url"], ctx.obj["api_key"])
        try:
            info = c.ping()
            print(f"Connected to {ctx.obj['url']}")
            print(json.dumps(info, indent=2))
        except Exception as e:
            print(f"Connection failed: {e}", file=sys.stderr)
            sys.exit(1)

    @cli.command()
    @click.argument("query")
    @click.option("--aql", is_flag=True, help="Treat query as AQL (ArangoDB syntax)")
    @click.option("--bind", "-b", multiple=True, help="Bind variable: key=value")
    @click.option("--json-output", is_flag=True, help="Output JSON instead of table")
    @click.pass_context
    def query(ctx, query, aql, bind, json_output):
        c = _client(ctx.obj["url"], ctx.obj["api_key"])
        params = {}
        for b in bind:
            k, v = b.split("=", 1)
            params[k] = v
        try:
            if aql:
                result = c.execute_aql(query, bind_vars=params or None)
            else:
                result = c.execute_cypher(query, parameters=params or None)
            if json_output:
                print(json.dumps({"columns": result.columns, "rows": result.rows}))
            else:
                _print_table(result.columns, result.rows)
        except Exception as e:
            print(f"Query failed: {e}", file=sys.stderr)
            sys.exit(1)

    @cli.command()
    @click.argument("path")
    @click.pass_context
    def load(ctx, path):
        c = _client(ctx.obj["url"], ctx.obj["api_key"])
        try:
            result = c.load_ndjson(path)
            print(json.dumps(result, indent=2))
        except Exception as e:
            print(f"Load failed: {e}", file=sys.stderr)
            sys.exit(1)

    @cli.command()
    @click.pass_context
    def status(ctx):
        c = _client(ctx.obj["url"], ctx.obj["api_key"])
        try:
            info = c.server_info()
            print(json.dumps(info, indent=2))
        except Exception as e:
            print(f"Status failed: {e}", file=sys.stderr)
            sys.exit(1)

    @cli.group()
    def schema():
        pass

    @schema.command("init")
    @click.option(
        "--embedding-dim", default=DEFAULT_EMBEDDING_DIMENSION, show_default=True
    )
    @click.pass_context
    def schema_init(ctx, embedding_dim):
        c = _client(ctx.obj["url"], ctx.obj["api_key"])
        try:
            import httpx
            resp = httpx.post(
                f"{ctx.obj['url']}/admin/schema/init",
                json={"embedding_dimension": embedding_dim},
                headers={"Authorization": f"Bearer {ctx.obj['api_key']}"} if ctx.obj["api_key"] else {},
            )
            resp.raise_for_status()
            print(json.dumps(resp.json(), indent=2))
        except Exception as e:
            print(f"Schema init failed: {e}", file=sys.stderr)
            sys.exit(1)

    @schema.command("status")
    @click.pass_context
    def schema_status(ctx):
        c = _client(ctx.obj["url"], ctx.obj["api_key"])
        try:
            info = c.schema()
            print(json.dumps(info, indent=2))
        except Exception as e:
            print(f"Schema status failed: {e}", file=sys.stderr)
            sys.exit(1)

    @cli.group()
    def embeddings():
        pass

    @embeddings.command("inventory")
    @click.option("--json-output", is_flag=True, help="Output JSON instead of a table")
    @click.option("--iris-host", envvar="IRIS_HOST", default=None)
    @click.option("--iris-port", envvar="IRIS_PORT", default=None)
    @click.option("--iris-namespace", envvar="IRIS_NAMESPACE", default=None)
    @click.option("--iris-username", envvar="IRIS_USERNAME", default=None)
    @click.option("--iris-password", envvar="IRIS_PASSWORD", default=None)
    def embeddings_inventory(
        json_output, iris_host, iris_port, iris_namespace, iris_username, iris_password
    ):
        """One line per embedding route, plus one per graph that has none (FR-038).

        Reads only. Never creates a route: a report that routed on being read would
        answer a question about routes by adding one.
        """
        try:
            engine = _engine_from_env(
                host=iris_host,
                port=iris_port,
                namespace=iris_namespace,
                username=iris_username,
                password=iris_password,
            )
            payload = _inventory_payload(engine.embedding_inventory())
        except Exception as e:
            print(f"Inventory failed: {e}", file=sys.stderr)
            sys.exit(1)
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_inventory(payload)

    def _iris_options(fn):
        for opt in reversed(
            (
                click.option("--iris-host", envvar="IRIS_HOST", default=None),
                click.option("--iris-port", envvar="IRIS_PORT", default=None),
                click.option("--iris-namespace", envvar="IRIS_NAMESPACE", default=None),
                click.option("--iris-username", envvar="IRIS_USERNAME", default=None),
                click.option("--iris-password", envvar="IRIS_PASSWORD", default=None),
            )
        ):
            fn = opt(fn)
        return fn

    def _fhir_run(iris, method, *args, **kwargs):
        """Call one engine method and print its JSON. Exit 1 on failure, 2 on busy.

        Direct to IRIS, like `embeddings inventory`: the graph lives in the FHIR
        namespace, and registering or repairing it is not a query for the server.
        """
        try:
            engine = _engine_from_env(
                host=iris["iris_host"],
                port=iris["iris_port"],
                namespace=iris["iris_namespace"],
                username=iris["iris_username"],
                password=iris["iris_password"],
            )
            out = getattr(engine, method)(*args, **kwargs)
        except Exception as e:
            print(f"fhir: {e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(out, indent=2, default=str))
        if isinstance(out, dict) and out.get("status") == "busy":
            sys.exit(2)

    @cli.group()
    def fhir():
        """The namespace's FHIR repository as a named graph (spec 231)."""

    @fhir.command("register")
    @click.option("--endpoint", default="", help="Endpoint path, when the namespace has more than one")
    @click.option("--deny", multiple=True, help="Type.param whose references are not edges")
    @click.option("--interval", default=60, show_default=True, help="Sync interval, seconds")
    @_iris_options
    def fhir_register(endpoint, deny, interval, **iris):
        _fhir_run(
            iris, "fhir_graph_register", endpoint=endpoint, denylist=list(deny), interval_s=interval
        )

    @fhir.command("rebuild")
    @click.argument("graph")
    @_iris_options
    def fhir_rebuild(graph, **iris):
        _fhir_run(iris, "fhir_graph_rebuild", graph)

    @fhir.command("sync")
    @click.argument("graph")
    @click.option("--once", is_flag=True, help="Apply every pending change, then exit")
    @_iris_options
    def fhir_sync(graph, once, **iris):
        """Exit status 2 means another sync or rebuild holds the graph."""
        if not once:
            raise click.UsageError(
                "periodic sync runs as a Task Manager task: use `ivg fhir schedule`, "
                "or pass --once"
            )
        _fhir_run(iris, "fhir_graph_sync", graph)

    @fhir.command("status")
    @click.argument("graph")
    @_iris_options
    def fhir_status(graph, **iris):
        _fhir_run(iris, "fhir_graph_status", graph)

    @fhir.command("schedule")
    @click.argument("graph")
    @click.option("--interval", type=int, default=None, help="Seconds; default keeps the registered one")
    @click.option("--remove", is_flag=True, help="Delete the graph's Task Manager task")
    @_iris_options
    def fhir_schedule(graph, interval, remove, **iris):
        if remove:
            _fhir_run(iris, "fhir_graph_unschedule", graph)
        else:
            _fhir_run(iris, "fhir_graph_schedule", graph, interval_s=interval)

    @cli.group()
    def server():
        pass

    @server.command("start")
    @click.option("--host", default="0.0.0.0", show_default=True)
    @click.option("--port", default=8200, show_default=True)
    @click.option("--workers", default=1, show_default=True)
    @click.option("--iris-host", envvar="IRIS_HOST")
    @click.option("--iris-port", envvar="IRIS_PORT", default=1972, show_default=True)
    @click.option("--iris-namespace", envvar="IRIS_NAMESPACE", default="USER", show_default=True)
    @click.option("--iris-password", envvar="IRIS_PASSWORD", default="SYS")
    def server_start(host, port, workers, iris_host, iris_port, iris_namespace, iris_password):
        if iris_host:
            os.environ["IRIS_HOST"] = iris_host
            os.environ["IRIS_PORT"] = str(iris_port)
            os.environ["IRIS_NAMESPACE"] = iris_namespace
            os.environ["IRIS_PASSWORD"] = iris_password
        try:
            import uvicorn
            from iris_vector_graph.cypher_api import app
            uvicorn.run(app, host=host, port=port, workers=workers)
        except ImportError:
            print("uvicorn required: pip install 'iris-vector-graph[full]'", file=sys.stderr)
            sys.exit(1)

    @cli.group()
    def indexes():
        pass

    @indexes.command("list")
    @click.pass_context
    def indexes_list(ctx):
        c = _client(ctx.obj["url"], ctx.obj["api_key"])
        try:
            data = c._get_client().get("/indexes").json()
            _print_table(data["columns"], data["indexes"])
        except Exception as e:
            print(f"Failed: {e}", file=sys.stderr)
            sys.exit(1)

    @indexes.command("rebuild")
    @click.pass_context
    def indexes_rebuild(ctx):
        import httpx
        try:
            resp = httpx.post(
                f"{ctx.obj['url']}/admin/indexes/rebuild",
                headers={"Authorization": f"Bearer {ctx.obj['api_key']}"} if ctx.obj["api_key"] else {},
            )
            resp.raise_for_status()
            print(json.dumps(resp.json(), indent=2))
        except Exception as e:
            print(f"Rebuild failed: {e}", file=sys.stderr)
            sys.exit(1)


def main():
    _require_click()
    cli()


if __name__ == "__main__":
    main()
