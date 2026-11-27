"""4.7.2: a namespace that has IVG's tables and none of its ObjectScript classes says so, once, at WARNING.

Without the classes every BFS, PageRank and bulk load runs on a Python or SQL fallback, and nothing said so: the one
line the engine logged was INFO, and the same line for a namespace that is merely not set up yet. The rules:

- tables there, classes missing: one WARNING per engine instance, naming the namespace and the missing classes;
- classes there: no warning;
- no tables (a fresh namespace before setup): no warning;
- whatever the probe meets, no exception: warn and report, never fail.

``check_objectscript_classes`` names the missing classes (``IRISCapabilities.missing_classes``).
"""

from __future__ import annotations

import logging

from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

SENTINEL = "Graph.KG.PageRank"
ENGINE_LOGGER = "iris_vector_graph.engine"


class _Server:
    """A cursor over a stand-in server: `classes` are the compiled ones, `tables` those in the IVG Schema."""

    def __init__(self, classes=(), tables=(), tables_raise=None, tables_schema="Graph_KG"):
        self.classes = set(classes)
        self.tables = list(tables)
        self.tables_schema = tables_schema
        self.tables_raise = tables_raise
        self.sql = []
        self._rows = []

    def cursor(self):
        return self

    def commit(self):
        pass

    def close(self):
        pass

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "FROM INFORMATION_SCHEMA.TABLES" in sql:
            if self.tables_raise:
                raise self.tables_raise
            asked = params[0] if params else None
            self._rows = [(t,) for t in self.tables] if asked == self.tables_schema else []
        elif "COUNT(*) FROM %Dictionary.ClassDefinition" in sql:
            name = sql.split("Name='", 1)[1].split("'", 1)[0]
            self._rows = [(1 if name in self.classes else 0,)]
        elif "SELECT Name FROM %Dictionary.ClassDefinition" in sql:
            self._rows = [(c,) for c in sorted(self.classes)]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _engine(server):
    return IRISGraphEngine(server, embedding_dimension=4)


def _warnings(caplog):
    return [r for r in caplog.records if r.name == ENGINE_LOGGER and r.levelno == logging.WARNING]


# ---------------------------------------------------------------------------
# check_objectscript_classes names the missing classes
# ---------------------------------------------------------------------------


def test_check_names_the_missing_classes_with_the_sentinel_first():
    caps = GraphSchema.check_objectscript_classes(_Server(classes=["Graph.KG.Meta"]))
    assert caps.objectscript_deployed is False
    assert caps.missing_classes[0] == SENTINEL
    assert "Graph.KG.Meta" not in caps.missing_classes  # present, so not named
    assert len(caps.missing_classes) > 20  # the packaged set, not the two probes
    assert len(set(caps.missing_classes)) == len(caps.missing_classes)


def test_check_names_nothing_when_the_classes_are_there():
    caps = GraphSchema.check_objectscript_classes(_Server(classes=[SENTINEL]))
    assert caps.objectscript_deployed is True
    assert caps.missing_classes == []


def test_a_dictionary_that_cannot_be_listed_still_names_the_sentinel():
    class _Refusing(_Server):
        def execute(self, sql, params=None):
            if "SELECT Name FROM" in sql:
                raise RuntimeError("[SQLCODE: <-99>:<Privilege violation>]")
            super().execute(sql, params)

    caps = GraphSchema.check_objectscript_classes(_Refusing())
    assert caps.objectscript_deployed is False
    assert caps.missing_classes[0] == SENTINEL


def test_default_capabilities_name_nothing_missing():
    assert IRISCapabilities().missing_classes == []


# ---------------------------------------------------------------------------
# the engine's one line
# ---------------------------------------------------------------------------


def test_tables_without_classes_warns_once_naming_the_classes(caplog):
    eng = _engine(_Server(tables=["nodes", "rdf_edges", "rdf_labels", "rdf_props"]))
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        caps = eng.capabilities
        _ = eng.capabilities  # cached
        eng.capabilities = None  # detected again on next use
        _ = eng.capabilities
    assert caps.objectscript_deployed is False
    lines = _warnings(caplog)
    assert len(lines) == 1, [r.getMessage() for r in lines]
    text = lines[0].getMessage()
    assert SENTINEL in text
    assert "initialize_schema()" in text
    assert "fallback" in text


def test_the_warning_counts_what_is_missing_without_listing_all_of_it(caplog):
    eng = _engine(_Server(tables=["nodes", "rdf_edges"]))
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        caps = eng.capabilities
    text = _warnings(caplog)[0].getMessage()
    assert str(len(caps.missing_classes)) in text
    assert len(text) < 600


def test_each_engine_instance_warns_once(caplog):
    server = _Server(tables=["nodes", "rdf_edges"])
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        _ = _engine(server).capabilities
        _ = _engine(server).capabilities
    assert len(_warnings(caplog)) == 2


def test_classes_present_means_no_warning(caplog):
    eng = _engine(_Server(classes=[SENTINEL], tables=["nodes", "rdf_edges"]))
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        assert eng.capabilities.objectscript_deployed is True
    assert _warnings(caplog) == []


def test_no_tables_means_no_warning(caplog):
    """A fresh namespace before setup has neither tables nor classes: nothing is wrong yet."""
    eng = _engine(_Server())
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        assert eng.capabilities.objectscript_deployed is False
    assert _warnings(caplog) == []


def test_tables_of_another_schema_do_not_count(caplog):
    eng = IRISGraphEngine(_Server(tables=["nodes", "rdf_edges"], tables_schema="Graph_KG"), embedding_dimension=4,
                          schema_prefix="RAG")
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        eng.capabilities
    assert _warnings(caplog) == []


def test_a_probe_that_fails_does_not_raise_and_does_not_warn(caplog):
    eng = _engine(_Server(tables_raise=RuntimeError("[SQLCODE: <-99>:<Privilege violation>]")))
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        caps = eng.capabilities
    assert caps.objectscript_deployed is False
    assert _warnings(caplog) == []


def test_only_one_of_the_two_core_tables_is_not_an_ivg_install(caplog):
    eng = _engine(_Server(tables=["nodes"]))
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        eng.capabilities
    assert _warnings(caplog) == []
