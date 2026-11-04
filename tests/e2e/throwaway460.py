"""Throwaway ObjectScript classes for 4.6.0 E2E files that force a failure through a subclass (TESTING_POLICY,
"ObjectScript is measured, not assumed": reach code a test cannot call through a throwaway subclass).

`load(conn, name, text)` compiles one class into the connection's namespace the way `tcp-deploy` does (a server file
written through `%Stream.FileCharacter`, then `$System.OBJ.Load`), in a directory of the namespace's own;
`delete(conn, name)` removes it. Callers use a package of their own (`IVGR152`, ...) that ships nothing.
"""

from __future__ import annotations


def _native(conn):
    import iris

    return iris.createIRIS(conn)


def load(conn, name: str, text: str) -> None:
    nat = _native(conn)
    ns = str(nat.classMethodValue("%SYSTEM.SYS", "NameSpace"))
    path = f"/tmp/ivg_throwaway_{ns}/{name}.cls"
    nat.classMethodValue("%File", "CreateDirectoryChain", path.rsplit("/", 1)[0])
    stream = nat.classMethodObject("%Stream.FileCharacter", "%New")
    stream.invokeVoid("LinkToFile", path)
    for line in text.strip().split("\n"):
        stream.invokeVoid("WriteLine", line)
    stream.invokeVoid("%Save")
    status = nat.classMethodValue("%SYSTEM.OBJ", "Load", path, "ck-d")
    if not nat.classMethodValue("%SYSTEM.Status", "IsOK", status):
        raise AssertionError(f"{name}: {nat.classMethodValue('%SYSTEM.Status', 'GetErrorText', status)}")


def delete(conn, name: str) -> None:
    _native(conn).classMethodValue("%SYSTEM.OBJ", "Delete", name, "-d")
