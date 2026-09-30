#!/usr/bin/env python3
"""Start IRIS for Vector Graph development and initialize the schema.

    python scripts/setup_iris.py                     # docker compose up, then initialize
    python scripts/setup_iris.py --no-start --port N # an IRIS that is already running

Starts the service in the repo's ``docker-compose.yml`` (the one the README starts),
runs ``IRISGraphEngine.initialize_schema()``, and writes the connection settings to
``.env``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _connect(host, port, namespace, user, password):
    import iris

    return iris.connect(host, port, namespace, user, password)


def _engine(conn):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=768)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-start", action="store_true", help="do not run docker compose")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1972)
    ap.add_argument("--namespace", default="USER")
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    args = ap.parse_args(argv)

    if not args.no_start:
        print(f"Starting IRIS: docker compose -f {COMPOSE} up -d --wait")
        r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--wait"])
        if r.returncode != 0:
            print("docker compose up failed; see its output above.", file=sys.stderr)
            return 1

    print(f"Initializing the schema on {args.host}:{args.port}/{args.namespace} ...")
    conn = _connect(args.host, args.port, args.namespace, "_SYSTEM", "SYS")
    _engine(conn).initialize_schema()

    args.env_file.write_text(
        f"IRIS_HOST={args.host}\n"
        f"IRIS_PORT={args.port}\n"
        f"IRIS_NAMESPACE={args.namespace}\n"
        "IRIS_USER=_SYSTEM\n"
        "IRIS_PASSWORD=SYS\n"
    )
    print(f"Schema ready. Connection settings written to {args.env_file}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
