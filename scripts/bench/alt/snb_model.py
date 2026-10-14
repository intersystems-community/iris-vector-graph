"""LDBC SNB (Spark datagen, `csv/raw/composite-merged-fk`) as one neutral stream of nodes and edges for every engine.

Stdlib only, so IVG's loader (under either side's tree), the Neo4j/Memgraph loaders and the tests all read the data the same
way. Ids are strings and node keys are ``<Label>:<id>``; dates stay ISO strings (they sort lexically). Property sets are
trimmed to what the queries use, and ``content`` is cut to CONTENT_MAX characters so every engine stores the same bytes.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

CONTENT_MAX = 100
csv.field_size_limit(sys.maxsize)

# label -> (directory, id column, property columns)
NODE_FILES = (
    ("Place", "static/Place", "id", ("name", "type")),
    ("TagClass", "static/TagClass", "id", ("name",)),
    ("Tag", "static/Tag", "id", ("name",)),
    ("Organisation", "static/Organisation", "id", ("name", "type")),
    ("Person", "dynamic/Person", "id", ("firstName", "lastName", "gender", "birthday", "creationDate", "locationIP", "browserUsed")),
    ("Forum", "dynamic/Forum", "id", ("title", "creationDate")),
    ("Post", "dynamic/Post", "id", ("creationDate", "content", "length", "language", "browserUsed", "locationIP")),
    ("Comment", "dynamic/Comment", "id", ("creationDate", "content", "length", "browserUsed", "locationIP")),
)

# (type, directory, source label, source column, target label, target column); an empty target column value is skipped
EDGE_FILES = (
    ("IS_PART_OF", "static/Place", "Place", "id", "Place", "PartOfPlaceId"),
    ("IS_SUBCLASS_OF", "static/TagClass", "TagClass", "id", "TagClass", "SubclassOfTagClassId"),
    ("HAS_TYPE", "static/Tag", "Tag", "id", "TagClass", "TypeTagClassId"),
    ("IS_LOCATED_IN", "static/Organisation", "Organisation", "id", "Place", "LocationPlaceId"),
    ("IS_LOCATED_IN", "dynamic/Person", "Person", "id", "Place", "LocationCityId"),
    ("HAS_MODERATOR", "dynamic/Forum", "Forum", "id", "Person", "ModeratorPersonId"),
    ("HAS_CREATOR", "dynamic/Post", "Post", "id", "Person", "CreatorPersonId"),
    ("CONTAINER_OF", "dynamic/Post", "Forum", "ContainerForumId", "Post", "id"),
    ("IS_LOCATED_IN", "dynamic/Post", "Post", "id", "Place", "LocationCountryId"),
    ("HAS_CREATOR", "dynamic/Comment", "Comment", "id", "Person", "CreatorPersonId"),
    ("IS_LOCATED_IN", "dynamic/Comment", "Comment", "id", "Place", "LocationCountryId"),
    ("REPLY_OF", "dynamic/Comment", "Comment", "id", "Post", "ParentPostId"),
    ("REPLY_OF", "dynamic/Comment", "Comment", "id", "Comment", "ParentCommentId"),
    ("KNOWS", "dynamic/Person_knows_Person", "Person", "Person1Id", "Person", "Person2Id"),
    ("HAS_MEMBER", "dynamic/Forum_hasMember_Person", "Forum", "ForumId", "Person", "PersonId"),
    ("HAS_TAG", "dynamic/Forum_hasTag_Tag", "Forum", "ForumId", "Tag", "TagId"),
    ("HAS_TAG", "dynamic/Post_hasTag_Tag", "Post", "PostId", "Tag", "TagId"),
    ("HAS_TAG", "dynamic/Comment_hasTag_Tag", "Comment", "CommentId", "Tag", "TagId"),
    ("HAS_INTEREST", "dynamic/Person_hasInterest_Tag", "Person", "PersonId", "Tag", "TagId"),
    ("LIKES", "dynamic/Person_likes_Post", "Person", "PersonId", "Post", "PostId"),
    ("LIKES", "dynamic/Person_likes_Comment", "Person", "PersonId", "Comment", "CommentId"),
    ("WORK_AT", "dynamic/Person_workAt_Company", "Person", "PersonId", "Organisation", "CompanyId"),
    ("STUDY_AT", "dynamic/Person_studyAt_University", "Person", "PersonId", "Organisation", "UniversityId"),
)


def node_key(label, ident):
    return f"{label}:{ident}"


def _rows(root, directory):
    d = Path(root) / "graphs" / "csv" / "raw" / "composite-merged-fk" / directory
    if not d.is_dir():
        d = Path(root) / directory  # a root that already points at composite-merged-fk
    for part in sorted(d.glob("*.csv")):
        with open(part, newline="", encoding="utf-8", errors="replace") as f:
            yield from csv.DictReader(f, delimiter="|", quoting=csv.QUOTE_NONE)


def iter_nodes(root):
    for label, directory, id_col, cols in NODE_FILES:
        for row in _rows(root, directory):
            props = {"sid": row[id_col]}  # not "id": IVG reads .id as the node key
            for c in cols:
                v = row.get(c)
                if v:
                    props[c] = v[:CONTENT_MAX] if c == "content" else v
            yield {"label": label, "id": row[id_col], "props": props}


def iter_edges(root):
    for etype, directory, slabel, scol, tlabel, tcol in EDGE_FILES:
        for row in _rows(root, directory):
            s, t = row.get(scol), row.get(tcol)
            if s and t:
                yield {"type": etype, "src": (slabel, s), "dst": (tlabel, t)}


def iter_edges_of(root, etype):
    """Only the edges of one type (every file that produces it)."""
    for t, directory, slabel, scol, tlabel, tcol in EDGE_FILES:
        if t != etype:
            continue
        for row in _rows(root, directory):
            s, d = row.get(scol), row.get(tcol)
            if s and d:
                yield {"type": t, "src": (slabel, s), "dst": (tlabel, d)}


def edge_counts(root):
    """{(type, directory): rows} without materialising the stream twice; for the report and the loaders' checks."""
    out = {}
    for e in iter_edges(root):
        out[e["type"]] = out.get(e["type"], 0) + 1
    return out
