"""The ObjectScript deploy a pip-installed IVG does on its own.

4.1.0 shipped no ``.cls`` in the wheel and loaded classes only from a directory
on the *server* (``/tmp/src``), so a consumer had to ``docker cp`` them in and
then paid a full reinstall (~8s) on every ``initialize_schema``. Compiling the
whole package also compiled embedded-Python and ``%AI`` classes, which a server
without them cannot compile. These tests pin the replacement: sources come from
the installed package, travel over the connection, and only classes the server
can compile are compiled, once per distinct source set.
"""

from pathlib import Path

import pytest

from iris_vector_graph._engine import class_deploy as cd

ROOT = Path(__file__).resolve().parents[2]


def _src(name, body="", extends="%RegisteredObject"):
    return f"Class {name} Extends {extends}\n{{\n{body}\n}}\n"


PY_METHOD = "ClassMethod P() [ Language = python ]\n{\n    return 1\n}"


class TestPackagedSources:
    def test_the_dev_tree_resolves_to_the_repo_sources(self):
        d = cd.packaged_class_dir()
        assert d is not None
        assert (d / "Graph" / "KG" / "PageRank.cls").is_file()

    def test_class_names_come_from_the_path(self):
        sources = cd.read_class_sources(cd.packaged_class_dir())
        assert "Graph.KG.PageRank" in sources
        assert "Graph.KG.PageRankEmbedded" in sources
        assert all(not n.endswith(".cls") for n in sources)

    def test_core_traversal_needs_no_embedded_python(self):
        # One dead [ Language = python ] helper made BFS uncompilable without Python.
        sources = cd.read_class_sources(cd.packaged_class_dir())
        assert not cd.uses_embedded_python(sources["Graph.KG.TraversalBFS"])


class TestSelection:
    def test_everything_compiles_where_everything_is_installed(self):
        sources = cd.read_class_sources(cd.packaged_class_dir())
        selected, skipped = cd.select_classes(
            sources, embedded_python=True, class_exists=lambda n: True
        )
        assert skipped == {}
        assert set(selected) == set(sources)

    def test_no_embedded_python_skips_python_classes_and_their_dependents(self):
        sources = cd.read_class_sources(cd.packaged_class_dir())
        selected, skipped = cd.select_classes(
            sources, embedded_python=False, class_exists=lambda n: True
        )
        for name in ("Graph.KG.PyOps", "IVG.CypherEngine", "Graph.KG.PageRankEmbedded"):
            assert "embedded Python" in skipped[name]
        # Service is a REST facade over PyOps.
        assert "Graph.KG.PyOps" in skipped["Graph.KG.Service"]
        for core in ("Graph.KG.TraversalBFS", "Graph.KG.Traversal", "Graph.KG.PageRank"):
            assert core in selected

    def test_missing_ai_classes_skip_the_mcp_cluster(self):
        sources = cd.read_class_sources(cd.packaged_class_dir())
        selected, skipped = cd.select_classes(
            sources,
            embedded_python=True,
            class_exists=lambda n: not n.startswith("%AI."),
        )
        for name in ("Graph.KG.MCPService", "Graph.KG.MCPTools", "Graph.KG.MCPToolSet"):
            assert name in skipped
        assert any("%AI." in skipped[n] for n in skipped)
        assert "Graph.KG.TemporalIndex" in selected

    def test_a_dependency_is_matched_on_the_whole_name(self):
        sources = {
            "A.Tools": _src("A.Tools", PY_METHOD),
            "A.ToolSet": _src("A.ToolSet"),
            "A.User": _src("A.User", "ClassMethod X()\n{\n Quit ##class(A.Tools).P()\n}"),
        }
        selected, skipped = cd.select_classes(
            sources, embedded_python=False, class_exists=lambda n: True
        )
        assert set(skipped) == {"A.Tools", "A.User"}
        assert selected == ["A.ToolSet"]

    def test_dependents_are_skipped_transitively(self):
        sources = {
            "A.Py": _src("A.Py", PY_METHOD),
            "A.B": _src("A.B", extends="A.Py"),
            "A.C": _src("A.C", extends="A.B"),
        }
        _, skipped = cd.select_classes(
            sources, embedded_python=False, class_exists=lambda n: True
        )
        assert set(skipped) == {"A.Py", "A.B", "A.C"}


class TestFingerprint:
    def test_stable_and_order_independent(self):
        a = {"X": "1", "Y": "2"}
        b = {"Y": "2", "X": "1"}
        assert cd.fingerprint(a) == cd.fingerprint(b)

    def test_changes_with_content_or_selection(self):
        assert cd.fingerprint({"X": "1"}) != cd.fingerprint({"X": "2"})
        assert cd.fingerprint({"X": "1"}) != cd.fingerprint({"X": "1", "Y": "2"})


class FakeIRIS:
    """Records what the deploy asks the server to do."""

    def __init__(self, marker=None, compiled=True):
        self.globals = {}
        if marker is not None:
            self.globals[cd.MARKER] = marker
        self.compiled = compiled
        self.uploaded = {}
        self.loads = []
        self.compiles = []

    def get(self, glo, *subs):
        return self.globals.get((glo,) + subs)

    def set(self, value, glo, *subs):
        self.globals[(glo,) + subs] = value

    def isDefined(self, glo, *subs):
        """``$Data`` on the class dictionary: ``defined`` holds ``^oddDEF`` nodes, as tuples of subscripts."""
        assert glo == "^oddDEF"
        return 1 if subs in getattr(self, "defined", ()) else 0

    def classMethodObject(self, cls, method, *args):
        # No %Dictionary.PackageDefinition: R084, the deploy maps no package's SQL name.
        assert (cls, method) == ("%Stream.GlobalCharacter", "%New")
        return FakeStream()

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "LoadStream"):
            stream, flags = args
            assert stream.rewound and "c" not in flags.replace("-d", "")
            text = "".join(stream.chunks)
            self.uploaded[text.split()[1]] = text
            self.loads.append(flags)
            return 1
        if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
            self.compiles.append(args[0])
            return 1
        if (cls, method) == ("%SYSTEM.Status", "IsOK"):
            return 1 if args[0] == 1 else 0
        if (cls, method) == ("%Dictionary.CompiledClass", "%ExistsId"):
            return 1 if self.compiled else 0
        if (cls, method) == ("%SYSTEM.Status", "GetErrorText"):
            return ""
        raise AssertionError(f"unexpected call {cls}.{method}{args}")


class TestSchemaClasses:
    """R084: no package is remapped; the schema classes (`<pkg>.SqlProcs`, the ledger tables) are what the rewrite
    moved into the IVG Schema's package."""

    FWD = "Class IVGALT.SqlProcs Extends %RegisteredObject\n{\nParameter IVGSCHEMACLASS = 1;\n}\n"
    ONE = "Class Z.Code\n{\n}\n"

    @pytest.mark.parametrize("schema", ["Graph_KG", "IVGALT"])
    def test_no_package_is_mapped(self, schema):
        fake = FakeIRIS()  # asserts on any %Dictionary.PackageDefinition call
        result = cd.upload_and_compile(fake, "USER", {"Z.Code": self.ONE}, schema=schema)
        assert result.deployed and result.errors == []

    def test_the_forwarder_compiles_after_every_other_class(self):
        """An upgraded namespace's code classes project the same names (ERROR #5083, measured) until they recompile."""
        fake = FakeIRIS()
        cd.upload_and_compile(fake, "USER", {"IVGALT.SqlProcs": self.FWD, "Z.Code": self.ONE}, schema="IVGALT")
        assert fake.compiles == ["Z.Code.cls", "IVGALT.SqlProcs.cls"]

    def test_someone_elses_class_of_a_schema_class_name_stops_the_deploy(self):
        fake = FakeIRIS()
        fake.defined = {("IVGALT.SqlProcs",)}  # no IVGSCHEMACLASS parameter: an application's class
        result = cd.upload_and_compile(fake, "USER", {"IVGALT.SqlProcs": self.FWD}, schema="IVGALT")
        assert not result.deployed
        assert len(result.errors) == 1 and "IVGALT.SqlProcs" in result.errors[0]
        assert fake.uploaded == {} and fake.compiles == [] and cd.MARKER not in fake.globals

    def test_a_schema_class_ivg_deployed_before_is_loaded_again(self):
        fake = FakeIRIS()
        fake.defined = {("IVGALT.SqlProcs",), ("IVGALT.SqlProcs", "p", "IVGSCHEMACLASS")}
        result = cd.upload_and_compile(fake, "USER", {"IVGALT.SqlProcs": self.FWD}, schema="IVGALT")
        assert result.deployed and result.errors == [] and "IVGALT.SqlProcs" in fake.uploaded

    def test_graph_kg_is_ivgs_own_package(self):
        fake = FakeIRIS()
        fake.defined = {("Graph.KG.SqlProcs",)}
        src = self.FWD.replace("IVGALT", "Graph.KG")
        result = cd.upload_and_compile(fake, "USER", {"Graph.KG.SqlProcs": src})
        assert result.deployed and result.errors == []


class FakeStream:
    def __init__(self):
        self.chunks = []
        self.rewound = False

    def invoke(self, method, *args):
        if method == "Write":
            assert len(args[0]) <= cd.STREAM_CHUNK
            self.chunks.append(args[0])
        elif method == "Rewind":
            self.rewound = True
        else:
            raise AssertionError(method)


class TestUpload:
    SOURCES = {"A.One": "Class A.One\n{\n}\n", "A.Two": "Class A.Two\n{\n}\n"}

    def test_loads_each_class_uncompiled_then_compiles_the_list(self):
        # %Compiler.UDL.TextServices rejected `[ SqlSchemaName = ... ]` headers and
        # some QUITs that %SYSTEM.OBJ.Load accepts; LoadStream is Load's parser.
        fake = FakeIRIS()
        result = cd.upload_and_compile(fake, "USER", self.SOURCES)
        assert result.deployed and not result.errors
        assert fake.uploaded["A.One"] == self.SOURCES["A.One"]
        assert fake.loads == ["-d", "-d"]
        assert fake.compiles == ["A.One.cls,A.Two.cls"]
        assert fake.globals[cd.MARKER] == cd.fingerprint(self.SOURCES)

    def test_functional_index_types_compile_first(self):
        fake = FakeIRIS()
        sources = {
            "A.Edge": "Class A.Edge Extends %Persistent\n{\n}\n",
            "A.Idx": "Class A.Idx Extends %Library.FunctionalIndex\n{\n}\n",
        }
        cd.upload_and_compile(fake, "USER", sources)
        assert fake.compiles == ["A.Idx.cls", "A.Edge.cls"]

    def test_a_matching_marker_skips_the_reinstall(self):
        fake = FakeIRIS(marker=cd.fingerprint(self.SOURCES))
        result = cd.upload_and_compile(fake, "USER", self.SOURCES)
        assert result.unchanged
        assert fake.uploaded == {} and fake.compiles == []

    def test_force_reinstalls_despite_the_marker(self):
        fake = FakeIRIS(marker=cd.fingerprint(self.SOURCES))
        result = cd.upload_and_compile(fake, "USER", self.SOURCES, force=True)
        assert result.deployed and fake.compiles

    def test_a_marker_with_no_compiled_class_is_not_trusted(self):
        # The marker survives a namespace whose classes were deleted.
        fake = FakeIRIS(marker=cd.fingerprint(self.SOURCES), compiled=False)
        result = cd.upload_and_compile(fake, "USER", self.SOURCES)
        assert result.deployed and fake.compiles

    def test_a_failed_compile_writes_no_marker(self):
        fake = FakeIRIS()
        fake.classMethodValue = _failing_compile(fake.classMethodValue)
        result = cd.upload_and_compile(fake, "USER", self.SOURCES)
        assert result.errors
        assert cd.MARKER not in fake.globals

    def test_a_failed_batch_is_compiled_once_more(self):
        # A method generator can read another class in the same batch
        # (%AI.ToolSet's %Discover reads MCPTools), which only the second pass sees
        # compiled. A fresh namespace has nothing compiled, so the first pass fails.
        fake = FakeIRIS()
        inner = fake.classMethodValue
        outcomes = iter([0, 1])

        def call(cls, method, *args):
            if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
                fake.compiles.append(args[0])
                return next(outcomes)
            return inner(cls, method, *args)

        fake.classMethodValue = call
        result = cd.upload_and_compile(fake, "USER", self.SOURCES)
        assert result.errors == []
        assert fake.compiles == ["A.One.cls,A.Two.cls"] * 2
        assert fake.globals[cd.MARKER] == cd.fingerprint(self.SOURCES)

    def test_a_batch_that_fails_both_passes_is_compiled_class_by_class(self):
        # Build 154: the batch compile fails on Graph.KG.MCPToolSet ("MCPTools has not had
        # inheritance resolved") and every class compiles alone (reported by Mindwalk).
        fake = FakeIRIS()
        inner = fake.classMethodValue

        def call(cls, method, *args):
            if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
                fake.compiles.append(args[0])
                return 0 if "," in args[0] else 1
            return inner(cls, method, *args)

        fake.classMethodValue = call
        result = cd.upload_and_compile(fake, "USER", self.SOURCES)
        assert result.errors == []
        assert fake.compiles == ["A.One.cls,A.Two.cls"] * 2 + ["A.One.cls", "A.Two.cls"]
        assert fake.globals[cd.MARKER] == cd.fingerprint(self.SOURCES)

    def test_a_class_that_fails_alone_is_reported_and_writes_no_marker(self):
        fake = FakeIRIS()
        inner = fake.classMethodValue

        def call(cls, method, *args):
            if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
                fake.compiles.append(args[0])
                return 1 if args[0] == "A.One.cls" else 0
            return inner(cls, method, *args)

        fake.classMethodValue = call
        result = cd.upload_and_compile(fake, "USER", self.SOURCES)
        assert len(result.errors) == 1
        assert cd.MARKER not in fake.globals

    def test_a_large_class_is_written_in_chunks(self):
        fake = FakeIRIS()
        big = "Class A.Big\n{\n" + "// x\n" * (cd.STREAM_CHUNK // 2) + "}\n"
        cd.upload_and_compile(fake, "USER", {"A.Big": big})
        assert fake.uploaded["A.Big"] == big

    def test_a_failed_load_is_reported_and_writes_no_marker(self):
        fake = FakeIRIS()
        inner = fake.classMethodValue

        def call(cls, method, *args):
            if (cls, method) == ("%SYSTEM.OBJ", "LoadStream"):
                return 0
            if (cls, method) == ("%SYSTEM.Status", "GetErrorText"):
                return "ERROR #8500: bad"
            return inner(cls, method, *args)

        fake.classMethodValue = call
        result = cd.upload_and_compile(fake, "USER", self.SOURCES)
        assert any(e.startswith("A.One: ERROR #8500") for e in result.errors)
        assert cd.MARKER not in fake.globals


def _failing_compile(inner):
    def call(cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
            return 0
        if (cls, method) == ("%SYSTEM.Status", "GetErrorText"):
            return "ERROR #5030: boom"
        return inner(cls, method, *args)

    return call


class TestProbes:
    def test_embedded_python_override(self, monkeypatch):
        monkeypatch.setenv("IVG_EMBEDDED_PYTHON", "0")
        assert cd.embedded_python_available(object()) is False

    def test_embedded_python_probe_failure_means_unavailable(self, monkeypatch):
        monkeypatch.delenv("IVG_EMBEDDED_PYTHON", raising=False)

        class Broken:
            def classMethodObject(self, *a):
                raise RuntimeError("<OBJECT DISPATCH> no python")

        assert cd.embedded_python_available(Broken()) is False


def test_the_wheel_config_ships_the_classes():
    tomllib = pytest.importorskip("tomllib")

    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    wheel = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel.get("force-include", {}).get("iris_src/src") == "iris_vector_graph/iris_src/src"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
