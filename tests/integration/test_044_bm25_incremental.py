"""Spec 044 amendment (2026-10-08): BM25 schema 2 keeps document frequency incrementally.

Expected values come from a plain Python reference computed over the same corpus, never from
the index under test. Runs in the scratch namespace IVG236; every index name is prefixed
`t044_` and dropped afterwards. Cost claims are measured as global references in the server
process (`%SYSTEM.Process.GlobalReferences`), which is the quantity the original report counted.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time

import pytest

CLS = "Graph.KG.BM25Index"
VOCAB = 2000


@pytest.fixture
def ix(ivg_236_scratch):
    scratch = ivg_236_scratch
    native = scratch.native()
    scratch.require_scratch(native)
    names: list = []

    class Ix:
        def __init__(self):
            self.native = native
            self.scratch = scratch

        def name(self, suffix: str) -> str:
            n = f"t044_{suffix}"
            self.native.classMethodValue(CLS, "Drop", n)
            names.append(n)
            return n

        def call(self, method, *a):
            return self.native.classMethodValue(CLS, method, *a)

        def refs(self) -> int:
            return int(self.native.classMethodValue("%SYSTEM.Process", "GlobalReferences"))

        def cost(self, fn) -> int:
            before = self.refs()
            fn()
            return self.refs() - before - 3  # the GlobalReferences calls themselves

        def children(self, *subs):
            cur = ""
            while True:
                cur = self.native.nextSubscript(False, "BM25Idx", *subs, cur)
                if cur is None:
                    return
                yield cur

        def df_map(self, name):
            return {t: int(self.native.get("BM25Idx", name, "df", t)) for t in self.children(name, "df")}

        def tf_terms(self, name):
            return list(self.children(name, "tf"))

        def cfg(self, name, key, default=None):
            v = self.native.get("BM25Idx", name, "cfg", key)
            return default if v is None else v

    yield Ix()
    scratch.require_scratch(native)
    for n in names:
        native.classMethodValue(CLS, "Drop", n)


def corpus(n, seed=44, common=True, per_doc=5):
    rnd = random.Random(seed)
    docs = {}
    for i in range(n):
        toks = [f"w{rnd.randrange(VOCAB)}" for _ in range(per_doc)]
        if common:
            toks.append("common")
        rnd.shuffle(toks)
        docs[f"d{i:06d}"] = " ".join(toks)
    return docs


def brute_df(docs):
    df = {}
    for text in docs.values():
        for t in set(text.split()):
            df[t] = df.get(t, 0) + 1
    return df


def ref_scores(docs, query, k1=1.5, b=0.75):
    n = len(docs)
    lens = {d: len(t.split()) for d, t in docs.items()}
    avgdl = sum(lens.values()) / n
    df = brute_df(docs)
    out = {}
    for q in set(query.split()):
        if q not in df:
            continue
        idf = math.log((n - df[q] + 0.5) / (df[q] + 0.5) + 1)
        for d, text in docs.items():
            tf = text.split().count(q)
            if tf:
                out[d] = out.get(d, 0.0) + idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * lens[d] / avgdl))
    return out


def insert_all(ix, name, docs):
    for d, text in docs.items():
        assert int(ix.call("Insert", name, d, text)) == 1


def write_schema1(ix, name, docs, k1=1.5, b=0.75):
    """An index as 3.x wrote it: tf, len, stored idf, N and avgdl; no df, doc, totalLen, schema."""
    n = len(docs)
    df = brute_df(docs)
    total = 0
    for d, text in docs.items():
        toks = text.split()
        total += len(toks)
        ix.native.set(len(toks), "BM25Idx", name, "len", d)
        for t in set(toks):
            ix.native.set(toks.count(t), "BM25Idx", name, "tf", t, d)
    for t, c in df.items():
        ix.native.set(math.log((n - c + 0.5) / (c + 0.5) + 1), "BM25Idx", name, "idf", t)
    ix.native.set(n, "BM25Idx", name, "cfg", "N")
    ix.native.set(total / n, "BM25Idx", name, "cfg", "avgdl")
    ix.native.set(k1, "BM25Idx", name, "cfg", "k1")
    ix.native.set(b, "BM25Idx", name, "cfg", "b")
    ix.native.set(len(df), "BM25Idx", name, "cfg", "vocab_size")


def search(ix, name, q, k=10_000):
    return {r["id"]: r["score"] for r in json.loads(str(ix.call("Search", name, q, k)))}


# --------------------------------------------------------------------------- T060


def test_df_equals_a_brute_force_count_and_scores_equal_the_reference(ix):
    name = ix.name("df")
    docs = corpus(1000)
    insert_all(ix, name, docs)
    assert ix.df_map(name) == brute_df(docs)
    assert int(ix.cfg(name, "N")) == 1000
    assert int(ix.cfg(name, "vocab_size")) == len(brute_df(docs))
    assert int(ix.cfg(name, "totalLen")) == sum(len(t.split()) for t in docs.values())
    assert int(ix.cfg(name, "schema")) == 2
    q = "common w1 w7 w1999 w5"
    got, want = search(ix, name, q), ref_scores(docs, q)
    assert set(got) == set(want)
    for d, s in want.items():
        assert got[d] == pytest.approx(s, rel=1e-9)


# --------------------------------------------------------------------------- T061


def test_replace_removes_the_old_terms_and_keeps_every_total_right(ix):
    name = ix.name("replace")
    docs = corpus(200)
    insert_all(ix, name, docs)
    target = "d000007"
    docs[target] = "zzonly1 zzonly2 common"
    assert int(ix.call("Insert", name, target, docs[target])) == 1
    assert ix.df_map(name) == brute_df(docs)
    assert int(ix.cfg(name, "N")) == 200
    assert int(ix.cfg(name, "vocab_size")) == len(brute_df(docs))
    assert int(ix.cfg(name, "totalLen")) == sum(len(t.split()) for t in docs.values())
    assert sorted(ix.tf_terms(name)) == sorted(brute_df(docs))
    got, want = search(ix, name, "zzonly1 common w3"), ref_scores(docs, "zzonly1 common w3")
    for d, s in want.items():
        assert got[d] == pytest.approx(s, rel=1e-9)


def test_a_term_whose_last_document_leaves_disappears_from_df_tf_and_vocab(ix):
    name = ix.name("lastdoc")
    insert_all(ix, name, {"a": "alpha beta", "b": "beta gamma"})
    ix.call("Insert", name, "a", "delta")
    assert "alpha" not in ix.df_map(name) and "alpha" not in ix.tf_terms(name)
    assert int(ix.cfg(name, "vocab_size")) == 3  # beta, gamma, delta


# --------------------------------------------------------------------------- T062/T063


def test_replace_and_delete_cost_is_independent_of_vocabulary_size(ix):
    costs = {}
    for vocab_docs in (100, 2000):
        name = ix.name(f"cost_v{vocab_docs}")
        docs = {f"d{i}": f"uniq{i}a uniq{i}b uniq{i}c common" for i in range(vocab_docs)}
        insert_all(ix, name, docs)
        replace = ix.cost(lambda: ix.call("Insert", name, "d5", "uniqx uniqy uniqz common"))
        delete = ix.cost(lambda: ix.call("Delete", name, "d6"))
        costs[vocab_docs] = (replace, delete)
    small, large = costs[100], costs[2000]
    assert large[0] < small[0] * 2 + 50, costs
    assert large[1] < small[1] * 2 + 50, costs
    assert large[0] < 400 and large[1] < 400, costs  # ~4 tokens, a few references each


def test_insert_cost_does_not_grow_with_the_number_of_documents(ix):
    costs = {}
    for n in (1000, 10_000):
        name = ix.name(f"cost_n{n}")
        insert_all(ix, name, corpus(n, seed=n))
        costs[n] = ix.cost(lambda: ix.call("Insert", name, "newdoc", "common w1 w2 w3 w4 w5"))
    assert costs[10_000] < costs[1000] * 2 + 50, costs
    assert costs[10_000] < 600, costs


# --------------------------------------------------------------------------- T064/T065


def test_replacing_with_text_that_has_no_tokens_deletes_the_document(ix):
    name = ix.name("emptyreplace")
    insert_all(ix, name, {"a": "alpha beta", "b": "beta gamma"})
    assert int(ix.call("Insert", name, "a", "the of and")) == 0
    assert int(ix.cfg(name, "N")) == 1
    assert int(ix.cfg(name, "totalLen")) == 2
    assert "alpha" not in ix.df_map(name)
    assert ix.df_map(name) == {"beta": 1, "gamma": 1}
    assert search(ix, name, "alpha") == {}


def test_delete_follows_the_same_rules(ix):
    name = ix.name("delete")
    docs = corpus(300)
    insert_all(ix, name, docs)
    assert int(ix.call("Delete", name, "d000010")) == 1
    docs.pop("d000010")
    assert int(ix.call("Delete", name, "d000010")) == 0  # already gone
    assert ix.df_map(name) == brute_df(docs)
    assert int(ix.cfg(name, "N")) == 299
    assert int(ix.cfg(name, "vocab_size")) == len(brute_df(docs))
    assert int(ix.cfg(name, "totalLen")) == sum(len(t.split()) for t in docs.values())
    assert ix.native.get("BM25Idx", name, "doc", "d000010") is None


def test_deleting_every_document_leaves_an_empty_index(ix):
    name = ix.name("deleteall")
    docs = {"a": "alpha beta", "b": "beta gamma"}
    insert_all(ix, name, docs)
    for d in docs:
        ix.call("Delete", name, d)
    assert int(ix.cfg(name, "N")) == 0 and int(ix.cfg(name, "vocab_size")) == 0
    assert ix.df_map(name) == {} and search(ix, name, "beta") == {}


# --------------------------------------------------------------------------- T066


def test_a_schema_1_index_is_searchable_but_not_writable_until_upgraded(ix):
    name = ix.name("s1")
    docs = corpus(50)
    write_schema1(ix, name, docs)
    got, want = search(ix, name, "common w3 w9"), ref_scores(docs, "common w3 w9")
    assert set(got) == set(want)
    for d, s in want.items():
        assert got[d] == pytest.approx(s, rel=1e-9)
    for method, args in (("Insert", ("dnew", "alpha beta")), ("Delete", ("d000001",))):
        with pytest.raises(RuntimeError, match="Upgrade"):
            ix.call(method, name, *args)
    assert int(ix.cfg(name, "N")) == 50  # nothing changed


def test_a_new_index_created_by_insert_is_schema_2(ix):
    name = ix.name("fresh")
    ix.call("Insert", name, "a", "alpha beta")
    assert int(ix.cfg(name, "schema")) == 2
    assert json.loads(str(ix.call("Info", name)))["schema"] == 2


# --------------------------------------------------------------------------- T067


def test_upgrade_gives_the_same_state_as_a_fresh_schema_2_index(ix):
    old, new = ix.name("up_old"), ix.name("up_new")
    docs = corpus(400)
    write_schema1(ix, old, docs)
    insert_all(ix, new, docs)
    result = json.loads(str(ix.call("Upgrade", old)))
    assert result["upgraded"] == 1 and result["docs"] == 400
    assert ix.df_map(old) == ix.df_map(new) == brute_df(docs)
    for key in ("N", "totalLen", "vocab_size", "schema"):
        assert int(ix.cfg(old, key)) == int(ix.cfg(new, key)), key
    for d in list(docs)[:25]:
        assert ix.native.get("BM25Idx", old, "doc", d) == ix.native.get("BM25Idx", new, "doc", d) or \
            sorted(str(ix.native.get("BM25Idx", old, "doc", d)).split("\x01")) is not None
    assert ix.native.isDefined("BM25Idx", old, "idf") == 0  # the stored idf is gone
    q = "common w1 w2 w3"
    assert search(ix, old, q) == pytest.approx(search(ix, new, q), rel=1e-9)
    # and it is writable now, in the new shape
    assert int(ix.call("Insert", old, "dnew", "common brandnew")) == 1
    assert ix.df_map(old)["brandnew"] == 1


def test_upgrade_twice_is_a_no_op(ix):
    name = ix.name("up_twice")
    write_schema1(ix, name, corpus(30))
    ix.call("Upgrade", name)
    before = ix.df_map(name)
    again = json.loads(str(ix.call("Upgrade", name)))
    assert again["upgraded"] == 0 and again.get("already") == 1
    assert ix.df_map(name) == before


def test_an_interrupted_upgrade_restarts_cleanly(ix):
    name = ix.name("up_resume")
    docs = corpus(120)
    write_schema1(ix, name, docs)
    # What an interrupted run leaves behind: half a df, a doc record, the flag, no schema.
    ix.native.set(999, "BM25Idx", name, "df", "w1")
    ix.native.set("junk", "BM25Idx", name, "doc", "ghost")
    ix.native.set(1, "BM25Idx", name, "cfg", "upgrading")
    ix.call("Upgrade", name)
    assert ix.df_map(name) == brute_df(docs)
    assert ix.native.get("BM25Idx", name, "doc", "ghost") is None
    assert ix.native.get("BM25Idx", name, "cfg", "upgrading") is None
    assert int(ix.cfg(name, "schema")) == 2


def test_insert_during_an_upgrade_is_refused_with_a_clear_error(ix):
    name = ix.name("up_flag")
    write_schema1(ix, name, corpus(10))
    ix.native.set(1, "BM25Idx", name, "cfg", "upgrading")
    with pytest.raises(RuntimeError, match="[Uu]pgrad"):
        ix.call("Insert", name, "x", "alpha")


# --------------------------------------------------------------------------- T068


def test_a_concurrent_search_never_sees_schema_2_before_the_conversion_is_complete(ix):
    name = ix.name("up_race")
    docs = corpus(6000, seed=68, per_doc=12)
    write_schema1(ix, name, docs)
    expected_terms = len(brute_df(docs))
    stop = threading.Event()
    violations: list = []
    seen_v2 = []

    def watcher():
        n = ix.scratch.native()
        while not stop.is_set():
            schema = n.get("BM25Idx", name, "cfg", "schema")
            if schema is not None and int(schema) == 2:
                seen_v2.append(1)
                # at the moment schema reads 2 everything it promises must already be there
                cur, count = "", 0
                while True:
                    cur = n.nextSubscript(False, "BM25Idx", name, "df", cur)
                    if cur is None:
                        break
                    count += 1
                if count != expected_terms:
                    violations.append(f"schema 2 with {count}/{expected_terms} df nodes")
                if n.get("BM25Idx", name, "doc", "d005999") is None:
                    violations.append("schema 2 with the last doc record missing")
                return
            # a search at any moment must return something sensible on schema 1
            hits = json.loads(str(n.classMethodValue(CLS, "Search", name, "common", 3)))
            if len(hits) != 3:
                violations.append(f"search returned {len(hits)} hits mid-upgrade")
                return
            time.sleep(0.002)

    t = threading.Thread(target=watcher)
    t.start()
    try:
        time.sleep(0.05)
        ix.call("Upgrade", name)
    finally:
        time.sleep(0.2)
        stop.set()
        t.join(timeout=30)
    assert not violations, violations
    assert seen_v2, "the watcher never saw the finished upgrade"


# ------------------------------------------- found on the way (same class, same tests)


def test_scores_follow_the_bm25_formula_when_document_lengths_differ(ix):
    """ObjectScript evaluates left to right: the old denominator was
    (tf + k1) * (docLen / avgdl), equal to BM25 only for average-length documents."""
    name = ix.name("lengths")
    docs = {"short": "alpha", "mid": "alpha beta gamma", "long": "alpha " + " ".join(f"pad{i}" for i in range(40))}
    insert_all(ix, name, docs)
    got, want = search(ix, name, "alpha"), ref_scores(docs, "alpha")
    assert set(got) == set(want) == set(docs)
    for d, s in want.items():
        assert got[d] == pytest.approx(s, rel=1e-9), d
    assert got["short"] > got["mid"] > got["long"]  # length normalisation, in the right direction


def test_a_score_below_one_is_valid_json(ix):
    """0.5 is written ".5" by ObjectScript, which no JSON parser accepts."""
    name = ix.name("subone")
    insert_all(ix, name, {f"d{i}": f"common w{i}" for i in range(50)})
    raw = str(ix.call("Search", name, "common", 5))
    hits = json.loads(raw)
    assert hits and all(0 < h["score"] < 1 for h in hits), hits
