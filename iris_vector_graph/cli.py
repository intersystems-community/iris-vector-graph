from __future__ import annotations

import json
import os
import sys

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
    @click.option("--embedding-dim", default=768, show_default=True)
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
