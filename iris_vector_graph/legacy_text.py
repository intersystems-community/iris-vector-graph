"""How a legacy object's text is compared with what IVG shipped (4.6.3).

IVG 4.5.x and earlier created its SQLUser functions with ``CREATE OR REPLACE FUNCTION``, so a
namespace's own ``JSON_VALUE`` or ``RAND`` was overwritten, and an operator who installs theirs again after the upgrade
has a function under a name the legacy manifest lists. A name alone does not say whose an object is; its text does.
:mod:`iris_vector_graph.legacy_bodies` holds the fingerprint of every body, view definition and class source a release
shipped under a manifest name (``scripts/dev/gen_legacy_bodies.py`` derives it from the release tags), and these
functions put the text IRIS holds and the text a release shipped into the same form before either is hashed.

* A routine body: ``INFORMATION_SCHEMA.ROUTINES.ROUTINE_DEFINITION``, which IRIS 2026.3 fills for a ``CREATE
  FUNCTION`` (the code between the braces) and for a class's ``SqlProc`` method (its implementation), against the
  release's DDL between its outer braces or the method's lines.
* A view: ``INFORMATION_SCHEMA.VIEWS.VIEW_DEFINITION``, which IRIS stores as ``(<columns>) <query>`` with an
  ``/*#OPTIONS ...*/`` comment of the client's protocol, against the DDL's query after ``AS``.
* A class: its UDL text (``%Compiler.UDL.TextServices.GetTextAsString``) against the release's ``.cls`` file, without
  ``///`` description lines. IRIS writes the declared name in full (``Class User.PageRankEmbedded`` for a file that
  says ``Class PageRankEmbedded``).

Each is compared with runs of whitespace made one space: IRIS stores a one-line DDL body after ``\\r\\n\\t`` and a
class with ``\\r\\n`` line ends.
"""

from __future__ import annotations

import hashlib
import re

_OPTIONS = re.compile(r"/\*#OPTIONS.*?\*/", re.S)
_CLASS_LINE = re.compile(r"^Class\s+[\w.%]+", re.M)
_DESCRIPTION = re.compile(r"^[ \t]*///.*$", re.M)


def derived_class(entry) -> bool:
    """A manifest function whose class `CREATE FUNCTION` generated (`User.func<NAME>`), as against a shipped class's
    SqlProc: removal drops the first as a function and deletes the second's class."""
    return entry["class_name"] == "User.func" + str(entry["sql_name"]).split(".", 1)[1].replace("_", "")


def _source_checked_classes() -> frozenset:
    from iris_vector_graph.legacy_manifest import LEGACY_MANIFEST

    return frozenset(e["class_name"] for e in LEGACY_MANIFEST
                     if e["kind"] == "class" or (e["kind"] == "function" and not derived_class(e)))


#: The manifest classes compared by their whole source: the ones removal deletes as classes (a class entry, and a
#: shipped class whose SqlProcs the manifest lists, ``User.CypherFn``). A view's class goes with its view, a
#: function's generated class (``User.funcRAND``) with the function, and those are compared by the view's query and
#: the function's body.
SOURCE_CHECKED_CLASSES = _source_checked_classes()


def _squash(text: str) -> str:
    return " ".join(str(text or "").split())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def routine_fingerprint(body: str) -> str:
    """The fingerprint of a routine body: the code between a ``CREATE FUNCTION``'s outer braces, or a method's lines."""
    return _hash(_squash(body))


def view_query(definition: str) -> str:
    """A stored view definition's query: without the leading column list and the ``/*#OPTIONS*/`` comment."""
    text = _OPTIONS.sub("", str(definition or "")).strip()
    if text.startswith("("):
        end = text.find(")")
        if end >= 0:
            text = text[end + 1:]
    return text


def view_fingerprint(query: str) -> str:
    """The fingerprint of a view's query (``SELECT ...``): :func:`view_query` of a stored definition, or a DDL's text
    after ``AS``."""
    return _hash(_squash(query))


def class_fingerprint(class_name: str, source: str) -> str:
    """The fingerprint of a class's UDL text, its declaration naming `class_name` in full and its ``///``
    description lines left out: they are documentation, and IRIS does not keep every one (3.2.0's
    ``PageRankEmbedded`` came back without its method ``_computePagerankCore``'s, measured on 2026.3.0AI)."""
    text = _DESCRIPTION.sub("", str(source or ""))
    return _hash(_squash(_CLASS_LINE.sub("Class " + class_name, text, count=1)))
