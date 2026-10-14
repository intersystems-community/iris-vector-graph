"""LDBC SNB raw CSV (Spark datagen, composite-merged-fk) read into one neutral node/edge stream for every engine."""

import pytest

from scripts.bench.alt import snb_model as m


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    (p / "part-0.csv").write_text(text) if rel.count("/") == 1 and not rel.endswith(".csv") else p.write_text(text)


@pytest.fixture
def raw(tmp_path):
    def put(d, text):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / d / "part-00000-x.csv").write_text(text)
        (tmp_path / d / "_SUCCESS").write_text("")

    put("static/Place", "id|name|url|type|PartOfPlaceId\n1|India|u|Country|\n2|Pune|u|City|1\n")
    put("static/TagClass", "id|name|url|SubclassOfTagClassId\n5|Thing|u|\n6|Person|u|5\n")
    put("static/Tag", "id|name|url|TypeTagClassId\n9|Mozart|u|6\n")
    put("static/Organisation", "id|type|name|url|LocationPlaceId\n20|Company|Acme|u|1\n21|University|Uni|u|2\n")
    put("dynamic/Person", "creationDate|deletionDate|explicitlyDeleted|id|firstName|lastName|gender|birthday|locationIP|browserUsed|LocationCityId|language|email\n2010-01-01T00:00:00.000+00:00|x|false|14|Ann|Lee|female|1984-03-11|1.1.1.1|Firefox|2|en|a@b\n")
    put("dynamic/Person_knows_Person", "creationDate|deletionDate|explicitlyDeleted|Person1Id|Person2Id\n2010-02-02T00:00:00.000+00:00|x|false|14|16\n")
    put("dynamic/Post", "creationDate|deletionDate|explicitlyDeleted|id|imageFile|locationIP|browserUsed|language|content|length|CreatorPersonId|ContainerForumId|LocationCountryId\n2011-01-01T00:00:00.000+00:00|x|false|100||1.1.1.1|Firefox|en|hello world|11|14|30|1\n")
    put("dynamic/Comment", "creationDate|deletionDate|explicitlyDeleted|id|locationIP|browserUsed|content|length|CreatorPersonId|LocationCountryId|ParentPostId|ParentCommentId\n2011-01-02T00:00:00.000+00:00|x|false|200|1.1.1.1|Firefox|yes|3|14|1|100|\n2011-01-03T00:00:00.000+00:00|x|false|201|1.1.1.1|Firefox|no|2|14|1||200\n")
    put("dynamic/Forum", "creationDate|deletionDate|explicitlyDeleted|id|title|ModeratorPersonId\n2010-03-03T00:00:00.000+00:00|x|false|30|Wall|14\n")
    put("dynamic/Forum_hasMember_Person", "creationDate|deletionDate|explicitlyDeleted|ForumId|PersonId\n2010-03-04T00:00:00.000+00:00|x|false|30|14\n")
    put("dynamic/Person_likes_Post", "creationDate|deletionDate|explicitlyDeleted|PersonId|PostId\n2011-02-02T00:00:00.000+00:00|x|false|14|100\n")
    put("dynamic/Person_workAt_Company", "creationDate|deletionDate|PersonId|CompanyId|workFrom\n2010-01-01T00:00:00.000+00:00|x|14|20|2005\n")
    put("dynamic/Post_hasTag_Tag", "creationDate|deletionDate|PostId|TagId\n2011-01-01T00:00:00.000+00:00|x|100|9\n")
    return tmp_path


def test_nodes_carry_label_string_id_and_selected_properties(raw):
    nodes = {(n["label"], n["id"]): n["props"] for n in m.iter_nodes(raw)}
    assert nodes[("Person", "14")]["firstName"] == "Ann" and nodes[("Person", "14")]["sid"] == "14"
    assert nodes[("Place", "2")]["name"] == "Pune"
    assert nodes[("Post", "100")]["content"] == "hello world" and nodes[("Post", "100")]["creationDate"].startswith("2011")
    assert ("Organisation", "21") in nodes and nodes[("Organisation", "21")]["type"] == "University"
    assert set(l for l, _ in nodes) == {"Place", "TagClass", "Tag", "Organisation", "Person", "Post", "Comment", "Forum"}


def test_content_is_truncated_so_every_engine_stores_the_same_bytes(raw):
    (raw / "dynamic" / "Post" / "part-00000-x.csv").write_text(
        "creationDate|deletionDate|explicitlyDeleted|id|imageFile|locationIP|browserUsed|language|content|length|CreatorPersonId|ContainerForumId|LocationCountryId\n"
        f"2011-01-01T00:00:00.000+00:00|x|false|100||1.1.1.1|Firefox|en|{'z' * 500}|500|14|30|1\n")
    post = next(n for n in m.iter_nodes(raw) if n["label"] == "Post")
    assert len(post["props"]["content"]) == m.CONTENT_MAX


def test_edges_cover_foreign_keys_and_relationship_files(raw):
    edges = {(e["type"], e["src"], e["dst"]) for e in m.iter_edges(raw)}
    assert ("KNOWS", ("Person", "14"), ("Person", "16")) in edges
    assert ("HAS_CREATOR", ("Post", "100"), ("Person", "14")) in edges
    assert ("CONTAINER_OF", ("Forum", "30"), ("Post", "100")) in edges
    assert ("REPLY_OF", ("Comment", "200"), ("Post", "100")) in edges
    assert ("REPLY_OF", ("Comment", "201"), ("Comment", "200")) in edges
    assert ("HAS_MODERATOR", ("Forum", "30"), ("Person", "14")) in edges
    assert ("HAS_MEMBER", ("Forum", "30"), ("Person", "14")) in edges
    assert ("LIKES", ("Person", "14"), ("Post", "100")) in edges
    assert ("IS_LOCATED_IN", ("Person", "14"), ("Place", "2")) in edges
    assert ("IS_PART_OF", ("Place", "2"), ("Place", "1")) in edges
    assert ("IS_SUBCLASS_OF", ("TagClass", "6"), ("TagClass", "5")) in edges
    assert ("HAS_TYPE", ("Tag", "9"), ("TagClass", "6")) in edges
    assert ("HAS_TAG", ("Post", "100"), ("Tag", "9")) in edges
    assert ("WORK_AT", ("Person", "14"), ("Organisation", "20")) in edges


def test_node_key_is_label_colon_id():
    assert m.node_key("Person", "14") == "Person:14"
