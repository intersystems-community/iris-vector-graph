"""Spec 262 T017: every class IVG ships is in one of IVG's own packages (FR-002, FR-002a; research R10).

A class in `User` lands beside the application's classes, and its `[ SqlProc ]` methods project into `SQLUser`; a class
with no package is filed by IRIS under `User` (4.5.1's `PageRankEmbedded` compiled as `User.PageRankEmbedded`).
"""

from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

IVG_PACKAGES = ("Graph.KG.", "IVG.")
#: Shipped outside Graph.KG/IVG, each for a stated reason.
#: - `iris.vector.graph.GraphOperators`: projects into schema `iris_vector_graph`, not SQLUser; its kg_TXT/kg_KNN_VEC/
#:   kg_RRF_FUSE share names with the DDL procedures in the IVG Schema, so it cannot move there unrenamed (open item).
#: - `User.Exec`: leaves the wheel for iris_src/dev in T055 (US5), with the tests that call it.
KNOWN = {"iris.vector.graph.GraphOperators", "User.Exec"}


def _classes():
    return read_class_sources(packaged_class_dir())


def test_every_class_is_in_an_ivg_package():
    outside = sorted(n for n in _classes() if not n.startswith(IVG_PACKAGES) and n not in KNOWN)
    assert outside == []


def test_no_class_without_a_package():
    assert sorted(n for n in _classes() if "." not in n) == []


def test_the_moved_function_classes_keep_their_sql_names():
    """R10: the classes move, the SQL names they project stay: `JSON_KEYS` by its SqlName, `CypherFn_IVG<m>` from
    the class's short name, which stays `CypherFn`. The deploy maps their package to the IVG Schema."""
    sources = _classes()
    assert "SqlName = JSON_KEYS" in sources["Graph.KG.JsonKeys"]
    assert "ClassMethod IVGJSONSET(" in sources["Graph.KG.CypherFn"]


def test_str_split_has_one_definition():
    """4.5.x shipped `User.funcSTRSPLIT` (SqlName STR_SPLIT) and also created `SQLUser.STR_SPLIT` by DDL, which compiles
    to the same class, so whichever ran last won. In the IVG Schema a SqlProc named STR_SPLIT would collide with the DDL
    function; only the DDL one exists."""
    sources = _classes()
    assert not [n for n, t in sources.items() if "SqlName = STR_SPLIT" in t]
