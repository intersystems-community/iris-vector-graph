"""Make and drop IRIS namespaces and databases over TCP, with no `iris session`.

    python scripts/dev/tcp_setup.py make NS          (database /usr/irissys/mgr/<ns>/, namespace NS on it)
    python scripts/dev/tcp_setup.py unexpire         (Security.Users.UnExpireUserPasswords("*"))

Cortex XDR kills any process that pipes ObjectScript into `iris session` (through `docker exec` or ssh), so IVG's
test tooling makes its scratch namespaces through the Native API instead, as _SYSTEM in %SYS on 127.0.0.1:IVG_PORT
(default 31972). Credentials: IRIS_USERNAME / IRIS_PASSWORD, default _SYSTEM / SYS.

Config.Databases.Create and Config.Namespaces.Create take their properties as a local array by reference, which the
Native API cannot pass, so a helper class with plain-argument methods, `IVGDev.Setup`
(scripts/dev/objectscript/IVGDev/Setup.cls), does each step. It is sent over TCP as a stream and loaded with
%SYSTEM.OBJ.LoadStream, as tests/helpers/dev_classes.py loads iris_src/dev; it is loaded again only when its source
changes (the source's hash is stamped into its SOURCEHASH parameter). It never ships: the wheel packages only
iris_vector_graph and iris_src/src.

This module has no rule about names beyond refusing USER and %SYS on the command line: scripts/dev/
make_scratch_namespace.py, which the gate and the scratch work use, checks its prefixes before calling it.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

HELPER = "IVGDev.Setup"
HELPER_SOURCE = Path(__file__).resolve().parent / "objectscript" / "IVGDev" / "Setup.cls"
HOST = "127.0.0.1"
#: How long to wait for another process loading the helper.
LOCK_TIMEOUT = 120
_HASH_LINE = 'Parameter SOURCEHASH = "";'


def mgr_directory(db: str) -> str:
    """Where a database `db` this module makes lives in the container."""
    return f"/usr/irissys/mgr/{db.lower()}/"


def source_hash() -> str:
    return hashlib.sha256(HELPER_SOURCE.read_bytes()).hexdigest()[:24]


def helper_source() -> str:
    """The class source, its SOURCEHASH parameter set to `source_hash()`."""
    text = HELPER_SOURCE.read_text()
    if _HASH_LINE not in text:
        raise RuntimeError(f"{HELPER_SOURCE}: no `{_HASH_LINE}` line to stamp")
    return text.replace(_HASH_LINE, f'Parameter SOURCEHASH = "{source_hash()}";', 1)


def _loaded_hash(native) -> Optional[str]:
    try:
        return str(native.classMethodValue(HELPER, "SourceHash"))
    except RuntimeError as exc:  # the class is not there yet: nothing else is expected here
        if "CLASS DOES NOT EXIST" not in str(exc):
            raise
        return None


def ensure_helper(native) -> bool:
    """Load and compile IVGDev.Setup into `native`'s namespace (%SYS) unless the loaded one is this source; True when
    it loaded. Under a lock, so two gate workers starting at once do not compile it over each other."""
    if _loaded_hash(native) == source_hash():
        return False
    native.lock("", LOCK_TIMEOUT, "^IVGDev.Setup")
    try:
        if _loaded_hash(native) == source_hash():
            return False
        stream = native.classMethodObject("%Stream.GlobalCharacter", "%New")
        stream.invoke("Write", helper_source())
        stream.invoke("Rewind")
        status = native.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "ck-d")
        if not (status == 1 or str(status) == "1"):
            text = native.classMethodValue("%SYSTEM.Status", "GetErrorText", status)
            raise RuntimeError(f"loading {HELPER} into %SYS failed: {text}")
    finally:
        native.unlock("", "^IVGDev.Setup")
    return True


@contextlib.contextmanager
def sys_native(port: Optional[int] = None, host: str = HOST) -> Iterator:
    """A Native API handle in %SYS with the helper loaded; the connection is closed after."""
    import iris

    conn = iris.createConnection(
        hostname=host,
        port=int(port or os.environ.get("IVG_PORT", "31972")),
        namespace="%SYS",
        username=os.environ.get("IRIS_USERNAME", "_SYSTEM"),
        password=os.environ.get("IRIS_PASSWORD", "SYS"),
    )
    try:
        native = iris.createIRIS(conn)
        ensure_helper(native)
        yield native
    finally:
        conn.close()


def _call(native, method: str, *args, what: str = "") -> str:
    """IVGDev.Setup.<method>(args); raises naming `what` when it answers FAIL."""
    out = native.classMethodValue(HELPER, method, *args)
    out = "" if out is None else str(out)
    if out.startswith("FAIL"):
        label = what or (args[0] if args else HELPER)
        raise RuntimeError(f"{label}: {method}: {out}")
    return out


def namespace_exists(native, ns: str) -> bool:
    return bool(int(_call(native, "NamespaceExists", ns) or 0))


def namespace_databases(native, ns: str) -> tuple:
    """(globals, routines): the databases `ns` maps; () when it does not exist."""
    out = _call(native, "NamespaceDatabases", ns)
    return tuple(out.split(",")) if out else ()


def make_namespace(native, ns: str, globals_db: Optional[str] = None, routines_db: Optional[str] = None,
                   resources: Optional[Dict[str, str]] = None) -> bool:
    """Make `ns` with its globals in `globals_db` and its routines in `routines_db` (both `ns` by default), each
    database in /usr/irissys/mgr/<db>/; `resources`: {database: resource} for a database on a resource of its own
    (otherwise IRIS gives it %DB_%DEFAULT). False, and nothing changed, when `ns` already existed."""
    if namespace_exists(native, ns):
        return False
    globals_db, routines_db = globals_db or ns, routines_db or ns
    resources = resources or {}
    for db in dict.fromkeys((globals_db, routines_db)):
        resource = resources.get(db, "")
        if resource:
            _call(native, "CreateResource", resource, f"scratch database {db}", what=ns)
        _call(native, "CreateDatabase", db, mgr_directory(db), resource, what=ns)
    _call(native, "CreateNamespace", ns, globals_db, routines_db, what=ns)
    return True


def drop_namespace(native, ns: str, own: Iterable[str]) -> bool:
    """Delete `ns`: its Task Manager tasks, its configuration, then each database it maps that `own` names (with
    resource %DB_<db> for a database not named `ns`). A database `own` does not name is left. False when `ns` did not
    exist."""
    if not namespace_exists(native, ns):
        return False
    dbs = namespace_databases(native, ns)
    # a Task Manager task (IVG's EmbedQueue worker) outlives its namespace and keeps running against it
    for task_id in filter(None, _call(native, "TaskIds", ns).split(",")):
        _call(native, "DeleteTask", task_id, what=ns)
    _call(native, "DeleteNamespace", ns)
    own = set(own)
    for db in dict.fromkeys(dbs):
        if db in own:
            _call(native, "DeleteDatabase", db, int(db != ns), what=ns)
    return True


def unexpire_passwords(native, pattern: str = "*") -> None:
    _call(native, "UnExpireUserPasswords", pattern)


USAGE = "usage: tcp_setup.py make NS   (upper-case alphanumeric, not USER or %SYS)\n       tcp_setup.py unexpire"


def main(argv: list) -> int:
    if argv[:1] == ["unexpire"] and len(argv) == 1:
        with sys_native() as native:
            unexpire_passwords(native)
        print("passwords unexpired")
        return 0
    if len(argv) != 2 or argv[0] != "make" or argv[1] in ("USER", "%SYS") or not re.fullmatch(r"[A-Z][A-Z0-9]*", argv[1]):
        print(f"{USAGE}\nrefused: {argv}")
        return 2
    ns = argv[1]
    with sys_native() as native:
        print(f"{ns} {'created' if make_namespace(native, ns) else 'exists'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
