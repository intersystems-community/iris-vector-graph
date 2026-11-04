"""Deploy the development-only ObjectScript classes a test needs (spec 262 T056, FR-015; 4.6.0 R080).

`iris_src/dev` holds what the wheel does not ship: `Graph.KG.Benchmark` and `Graph.KG.BenchFormat` (benchmark code),
`User.Exec` (runs `Xecute` on any string) and `iris.vector.graph.GraphOperators` (SQL procedures nothing calls). A
test that calls one deploys it with :func:`deploy_dev_classes`, so a production namespace never holds them and the
suite still passes from the installed wheel (the dev sources come from the checkout, as the tests do).

The sources are rewritten for the namespace's IVG Schema the way the packaged classes are, and loaded and compiled
without touching the deploy fingerprint (`^IVG.Deploy("fingerprint")`), which records the packaged set alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from iris_vector_graph._engine.class_deploy import (
    STREAM_CHUNK,
    _error_text,
    _invoke,
    _ok,
    read_class_sources,
    rewrite_sources,
)

DEV_DIR = Path(__file__).resolve().parents[2] / "iris_src" / "dev"
BENCHMARK = ("Graph.KG.Benchmark", "Graph.KG.BenchFormat")
EXEC = ("User.Exec",)
GRAPH_OPERATORS = ("iris.vector.graph.GraphOperators",)


def dev_sources() -> dict:
    return read_class_sources(DEV_DIR)


def _native(conn):
    """`conn` itself when it is a Native API handle (``iris.IRIS``), else one made from the DB-API connection."""
    import iris

    return conn if hasattr(conn, "classMethodValue") else iris.createIRIS(getattr(conn, "_connection", conn))


def deploy_dev_classes(conn, names: Optional[Iterable[str]] = None, schema: str = "Graph_KG") -> List[str]:
    """Load and compile `names` (all of `iris_src/dev` by default) into `conn`'s namespace; return what compiled.

    `conn` is a DB-API connection or a Native API handle (``iris.IRIS``). Raises ``RuntimeError`` naming each class
    that failed to load or compile, so a test never runs against a half-deployed helper."""
    native = _native(conn)
    sources = rewrite_sources(dev_sources(), schema)
    wanted = sorted(sources) if names is None else sorted(names)
    unknown = [n for n in wanted if n not in sources]
    if unknown:
        raise KeyError(f"not in {DEV_DIR}: {unknown}")
    errors = []
    for name in wanted:
        stream = native.classMethodObject("%Stream.GlobalCharacter", "%New")
        text = sources[name]
        for i in range(0, len(text), STREAM_CHUNK):
            _invoke(stream, "Write", text[i: i + STREAM_CHUNK])
        _invoke(stream, "Rewind")
        status = native.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "-d")
        if not _ok(native, status):
            errors.append(f"{name} (load): {_error_text(native, status)}")
    status = native.classMethodValue("%SYSTEM.OBJ", "CompileList", ",".join(f"{n}.cls" for n in wanted), "ck-d")
    if not _ok(native, status):
        errors.append(f"compile: {_error_text(native, status)}")
    if errors:
        raise RuntimeError("deploying development classes failed: " + "; ".join(errors))
    return wanted


def ensure_dev_classes(conn, names: Iterable[str], schema: str = "Graph_KG"):
    """Deploy whichever of `names` is not compiled in `conn`'s namespace; return the Native API handle used.

    Asked of the namespace every time (``$Data(^oddCOM(name))``), never remembered in the process: a scratch namespace
    is dropped and created again within one run."""
    native = _native(conn)
    missing = [n for n in names if not int(native.isDefined("^oddCOM", n) or 0)]
    if missing:
        deploy_dev_classes(native, missing, schema=schema)
    return native


def run_objectscript(conn, code: str) -> None:
    """`Xecute` `code` in `conn`'s namespace through `User.Exec.Run`, deployed first when it is not there."""
    ensure_dev_classes(conn, EXEC).classMethodVoid("User.Exec", "Run", code)


def remove_dev_classes(conn, names: Iterable[str]) -> None:
    """Delete development classes a test deployed (a namespace another test checks for them stays clean)."""
    native = _native(conn)
    for name in names:
        native.classMethodValue("%SYSTEM.OBJ", "Delete", name, "-d")
