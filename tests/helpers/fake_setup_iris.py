"""A fake Native API handle in %SYS for scripts/dev/tcp_setup.py's tests: only the names `iris.IRIS` has
(classMethodValue, classMethodObject, lock, unlock), answering IVGDev.Setup's methods from a model of IRIS's namespaces,
databases, resources and tasks. Each call is checked against the class source (the method exists, the arguments fit)."""

from __future__ import annotations

import re
from pathlib import Path

CLASS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "objectscript" / "IVGDev"
CLASS = CLASS_DIR / "Setup.cls"
RUN_CLASS = CLASS_DIR / "Run.cls"


def class_methods(path: Path = CLASS) -> dict:
    """{method name: number of formal arguments} of a helper class's class methods, read from the source
    (IVGDev.Setup by default)."""
    found = {}
    for name, args in re.findall(r"^ClassMethod (\w+)\(([^)]*)\)", path.read_text(), re.M):
        found[name] = len([a for a in args.split(",") if a.strip()])
    return found


class FakeStream:
    def __init__(self):
        self.text = ""

    def invoke(self, method, *args):
        if method == "Write":
            self.text += args[0]
        elif method != "Rewind":
            raise AssertionError(f"stream method {method}")
        return None


class FakeIRIS:
    """What tcp_setup asks of IRIS, answered from a model of it. Records every IVGDev.Setup call in order."""

    def __init__(self, loaded_hash=None):
        self.namespaces: dict = {}  # name -> (globals, routines)
        self.databases: dict = {}  # name -> directory
        self.resources: set = set()
        self.tasks: dict = {}  # id -> namespace
        self.calls: list = []
        self.loaded_hash = loaded_hash
        self.loads: list = []
        self.locks: list = []
        self.methods = class_methods()
        self.fail: dict = {}  # method -> answer

    # -- Native API names ---------------------------------------------------------------------------------------
    def lock(self, mode, timeout, name, *subs):
        self.locks.append(("lock", name))

    def unlock(self, mode, name, *subs):
        self.locks.append(("unlock", name))

    def classMethodObject(self, cls, method, *args):
        assert (cls, method) == ("%Stream.GlobalCharacter", "%New")
        return FakeStream()

    def classMethodValue(self, cls, method, *args):
        if cls == "%SYSTEM.OBJ" and method == "LoadStream":
            stream, flags = args
            self.loads.append((re.search(r"^Class ([\w.]+)", stream.text, re.M).group(1), flags))
            m = re.search(r'Parameter SOURCEHASH = "(\w*)";', stream.text)
            if m:
                self.loaded_hash = m.group(1)
            return 1
        if cls == "%SYSTEM.Status":
            return "error text"
        assert cls == "IVGDev.Setup", (cls, method)
        if self.loaded_hash is None:
            raise RuntimeError("<CLASS DOES NOT EXIST> IVGDev.Setup")
        assert method in self.methods, f"IVGDev.Setup has no class method {method}"
        assert len(args) <= self.methods[method], (method, args)
        self.calls.append((method, *args))
        if method in self.fail:
            return self.fail[method]
        return getattr(self, "_" + method)(*args)

    # -- IVGDev.Setup, modelled ---------------------------------------------------------------------------------
    def _SourceHash(self):
        return self.loaded_hash

    def _NamespaceExists(self, ns):
        return int(ns in self.namespaces)

    def _NamespaceDatabases(self, ns):
        return ",".join(self.namespaces[ns]) if ns in self.namespaces else ""

    def _CreateResource(self, name, description=""):
        self.resources.add(name)
        return ""

    def _CreateDatabase(self, name, directory, resource=""):
        self.databases.setdefault(name, directory)
        return ""

    def _CreateNamespace(self, ns, globals_db, routines_db):
        self.namespaces[ns] = (globals_db, routines_db)
        return ""

    def _TaskIds(self, ns):
        return ",".join(i for i, n in self.tasks.items() if n == ns)

    def _DeleteTask(self, task_id):
        del self.tasks[task_id]
        return ""

    def _DeleteNamespace(self, ns):
        self.namespaces.pop(ns, None)
        return ""

    def _DeleteDatabase(self, name, delete_resource=0):
        self.databases.pop(name, None)
        if delete_resource:
            self.resources.discard(f"%DB_{name}")
        return ""

    def _UnExpireUserPasswords(self, pattern="*"):
        return ""
