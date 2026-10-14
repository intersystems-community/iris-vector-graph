"""Pure helpers of the SNB bulk loader (scripts/bench/alt/snb_bulk.py): row files, edge filtering, summaries, phase log."""

import json

import pytest

from scripts.bench.alt import snb_bulk as b


def _put(root, d, text):
    p = root / "graphs" / "csv" / "raw" / "composite-merged-fk" / d
    p.mkdir(parents=True, exist_ok=True)
    (p / "part-00000-x.csv").write_text(text)


@pytest.fixture
def raw(tmp_path):
    _put(tmp_path, "static/Place", "id|name|url|type|PartOfPlaceId\n1|India|u|Country|\n2|Pune|u|City|1\n")
    _put(tmp_path, "static/TagClass", "id|name|url|SubclassOfTagClassId\n5|Thing|u|\n")
    _put(tmp_path, "static/Tag", "id|name|url|TypeTagClassId\n9|Mozart|u|5\n")
    _put(tmp_path, "static/Organisation", "id|type|name|url|LocationPlaceId\n20|Company|Acme|u|1\n")
    _put(
        tmp_path,
        "dynamic/Person",
        "creationDate|deletionDate|explicitlyDeleted|id|firstName|lastName|gender|birthday|locationIP|browserUsed|LocationCityId|language|email\n"
        "2010-01-01|x|false|14|Ann|Lee|female|1984-03-11|1.1.1.1|Firefox|2|en|a@b\n"
        "2010-01-02|x|false|16|Bo||male|1985-01-01|1.1.1.2|Chrome|2|en|b@b\n",
    )
    # 14 knows 16 twice (a duplicate row) and itself once (a self loop); 14 knows 99, who is not a node.
    _put(
        tmp_path,
        "dynamic/Person_knows_Person",
        "creationDate|deletionDate|explicitlyDeleted|Person1Id|Person2Id\n"
        "2010-02-02|x|false|14|16\n2010-02-03|x|false|14|16\n2010-02-04|x|false|14|14\n2010-02-05|x|false|14|99\n",
    )
    return tmp_path


def test_tsv_line_joins_with_the_separator_and_carries_tabs():
    assert b.tsv_line("a", "b", "c") == f"a{b.SEP}b{b.SEP}c\n"
    assert b.tsv_line("k", "content", "one\ttwo\t") == f"k{b.SEP}content{b.SEP}one\ttwo\t\n"  # SF1 text holds tabs


@pytest.mark.parametrize("bad", ["a\x1fb", "a\nb", "a\rb"])
def test_tsv_line_refuses_a_field_it_cannot_carry(bad):
    with pytest.raises(ValueError):
        b.tsv_line("x", bad)


def test_pair_key_is_stable_and_distinguishes_direction():
    assert b.pair_key("Person:1", "Person:2") == b.pair_key("Person:1", "Person:2")
    assert b.pair_key("Person:1", "Person:2") != b.pair_key("Person:2", "Person:1")
    assert b.pair_key("Person:1", "Person:23") != b.pair_key("Person:12", "Person:3")
    assert b.pair_key("a:b", "c") != b.pair_key("a", "b:c")  # non-numeric ids fall back to exact tuples


def test_filter_edges_drops_self_loops_and_repeats_and_names_unknown_endpoints():
    edges = [("A:1", "R", "A:2"), ("A:1", "R", "A:2"), ("A:1", "R", "A:1"), ("A:1", "S", "A:2"), ("A:2", "R", "A:9")]
    known = {"A:1", "A:2"}
    st = b.EdgeFilter(known)
    out = list(st.filter(iter(edges)))
    assert out == [("A:1", "R", "A:2"), ("A:1", "S", "A:2"), ("A:2", "R", "A:9")]
    assert (st.self_loops, st.duplicates) == (1, 1)
    assert st.extra_nodes == ["A:9"]


def test_filter_edges_forgets_pairs_between_files_but_not_within_one():
    st = b.EdgeFilter({"A:1", "A:2"})
    first = list(st.filter(iter([("A:1", "R", "A:2")])))
    st.next_group()
    second = list(st.filter(iter([("A:1", "R", "A:2")])))
    assert first == second == [("A:1", "R", "A:2")]


def test_write_tables_writes_the_four_files_and_counts(raw, tmp_path):
    out = tmp_path / "out"
    counts = b.write_tables(raw, out)
    nodes = (out / "nodes.tsv").read_text().splitlines()
    assert nodes[:2] == ["Place:1", "Place:2"] and "Person:14" in nodes and "Person:99" in nodes  # 99: an endpoint with no row
    labels = [ln.split(b.SEP) for ln in (out / "labels.tsv").read_text().splitlines()]
    assert ["Person:14", "Person"] in labels and not any(ln[0] == "Person:99" for ln in labels)
    props = {tuple(ln.split(b.SEP)[:2]): ln.split(b.SEP)[2] for ln in (out / "props.tsv").read_text().splitlines()}
    assert props[("Person:14", "firstName")] == "Ann" and props[("Person:14", "sid")] == "14"
    assert ("Person:16", "lastName") not in props  # an empty value is not a property
    # The engine's bulk_create_nodes adds `id` = the node key to every node's properties (nodes_edges.py, SQL path).
    assert props[("Person:14", "id")] == "Person:14" and props[("Place:1", "id")] == "Place:1"
    edges = [tuple(ln.split(b.SEP)) for ln in (out / "edges.tsv").read_text().splitlines()]
    assert ("Person:14", "KNOWS", "Person:16") in edges and ("Person:14", "KNOWS", "Person:14") not in edges
    assert edges.count(("Person:14", "KNOWS", "Person:16")) == 1
    assert counts["edges"] == len(edges) and counts["nodes"] == len(nodes) and counts["labels"] == len(labels)
    assert counts["props"] == len(props) and counts["self_loops"] == 1 and counts["duplicates"] == 1 and counts["extra_nodes"] == 1
    assert json.loads((out / "counts.json").read_text()) == counts


def test_write_tables_refuses_a_repeated_node(raw, tmp_path):
    _put(raw, "static/Tag", "id|name|url|TypeTagClassId\n9|Mozart|u|5\n9|Again|u|5\n")
    with pytest.raises(ValueError, match="Tag:9"):
        b.write_tables(raw, tmp_path / "out")


def test_files_ready_needs_every_file_and_the_counts(raw, tmp_path):
    out = tmp_path / "out"
    assert not b.files_ready(out)
    b.write_tables(raw, out)
    assert b.files_ready(out)
    (out / "props.tsv").unlink()
    assert not b.files_ready(out)


def test_diff_summaries_names_each_difference():
    a = {"counts": {"nodes": 3, "edges": 4}, "kg": {"out_sources": 2, "out_edges": 4}, "queries": {"q1": ["s1", "s2"]}}
    assert b.diff_summaries(a, json.loads(json.dumps(a))) == []
    c = {"counts": {"nodes": 3, "edges": 5}, "kg": {"out_sources": 2, "out_edges": 4}, "queries": {"q1": ["s1", "sX"]}}
    diffs = b.diff_summaries(a, c)
    assert any("counts.edges" in d for d in diffs) and any("q1" in d for d in diffs) and len(diffs) == 2


def test_phase_log_records_each_phase_in_order():
    ticks = iter([10.0, 12.5, 12.5, 20.0])
    log = b.PhaseLog(clock=lambda: next(ticks))
    with log.phase("nodes"):
        pass
    with log.phase("edges"):
        pass
    assert log.as_dict() == {"nodes_s": 2.5, "edges_s": 7.5}


def test_objectscript_loader_names_what_it_loads():
    cls = b.LOADER_CLASS
    for needle in ("LoadNodes", "LoadLabels", "LoadProps", "LoadEdges", "LoadAdjacency", "BuildIdx", "%NOINDEX", "%NOCHECK"):
        assert needle in cls


def test_kg_summary_script_is_read_only_and_parses_back():
    assert "Set ^" not in b.KG_SUMMARY_SCRIPT and "Kill" not in b.KG_SUMMARY_SCRIPT
    text = "SNB1B>\nKGS out_sources=5\nKGS kg_gen=5\nKGS nkg_meta_edgeCount=9\nnoise\nKGS kg_version=12\n"
    assert b.parse_kg_summary(text) == {"out_sources": 5, "kg_gen": 5, "nkg_meta_edgeCount": 9, "kg_version": 12}
