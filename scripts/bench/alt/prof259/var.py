"""Walk variants: a generated Walk1 (Gen.Source of a shape's hash), transformed, loaded as ZIVGP.V<name> and timed."""
import json
import re
import statistics
import time

from scripts.bench.alt.prof259.lib import *

HASH = {"BI04": "665075845", "BI18": "921108366", "BI14": "2357888831"}
NAME = {"BI04": "BI04_top_forums_by_country", "BI14": "BI14_international_dialog", "BI18": "BI18_friend_recommendation"}


def source(n, h):
    return str(n.classMethodValue("Graph.KG.Cypher.Gen", "Source", h))


def deploy(n, conn, cls, src, extra="", base="Graph.KG.Cypher.Exec"):
    """Create/replace class ZIVGP.<cls> extending Exec with Walk1 = src (and the methods in `extra`)."""
    text = [f"Class ZIVGP.{cls} Extends {base}", "{", "", 'ClassMethod Walk1(ByRef pQ, pKey, pGName As %String, ByRef pB)', "{"]
    text += src.rstrip("\n").split("\n") + ["}", ""] + extra.split("\n") + ["", "}"]
    dest = f"/tmp/zivgp_{cls}.cls"
    stream = n.classMethodObject("%Stream.FileCharacter", "%New")
    stream.invokeVoid("LinkToFile", dest)
    for line in text:
        stream.invokeVoid("WriteLine", line)
    stream.invokeVoid("%Save")
    res = n.classMethodValue("%SYSTEM.OBJ", "Load", dest, "ck-d")
    if not n.classMethodValue("%SYSTEM.Status", "IsOK", res):
        raise RuntimeError(n.classMethodValue("%SYSTEM.Status", "GetOneErrorText", res))


def rows_of(head):
    h = json.loads(head)
    return h.get("rows") if "rows" in h else head


def run_once(n, plan, params):
    n.classMethodVoid("ZIVGP.Prof", "Reset")
    t = time.perf_counter()
    head = str(n.classMethodValue("ZIVGP.Prof", "Time", json.dumps(plan), json.dumps(params)))
    wall = (time.perf_counter() - t) * 1000
    ph = json.loads(str(n.classMethodValue("ZIVGP.Prof", "Phases")))
    return wall, ph, head


def set_walk(n, cls):
    n.classMethodValue("ZIVGP.T", "X", f'Set ^||ZWalk = "{cls}"' if cls else 'Kill ^||ZWalk')


def paired_full(n, plan, params, classes, rounds=7):
    """Alternate the classes `rounds` times; per class a list of (wall ms, phases dict); and each class's rows."""
    res = {c: [] for c in classes}
    heads = {}
    for _ in range(rounds):
        for c in classes:
            set_walk(n, c)
            wall, ph, head = run_once(n, plan, params)
            res[c].append((wall, ph))
            heads[c] = rows_of(head)
    set_walk(n, None)
    return res, heads


def paired(n, plan, params, classes, rounds=7, key="walk"):
    """Alternate the classes (None = the generated one) `rounds` times; per class the list of `key` times (ms)."""
    res = {c: [] for c in classes}
    heads = {}
    for _ in range(rounds):
        for c in classes:
            set_walk(n, c)
            wall, ph, head = run_once(n, plan, params)
            res[c].append(ph.get(key, wall) if key != "wall" else wall)
            heads[c] = rows_of(head)
    set_walk(n, None)
    return res, heads
