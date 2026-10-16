"""The LDBC SNB interactive-update workload in the Cypher subset IVG, Neo4j and Memgraph share.

Seven of the eight interactive updates (add person, like, forum, membership, post, comment, friendship; "like a comment" is the
same shape as "like a post"). Each is one write statement, run as its own transaction; the harness times each call. The ids of
the nodes created start at NEW_ID_BASE, above every id in the datagen output, so a run never collides with the loaded graph;
parameters come from a seed so every engine gets the same statements.
"""

from __future__ import annotations

import random

NEW_ID_BASE = 9_000_000_000

UPDATES = {
    "IU1_add_person": {
        "cypher": "CREATE (p:Person {<<KEY>>sid: $sid, firstName: $firstName, lastName: $lastName, gender: $gender, birthday: $birthday, creationDate: $creationDate, locationIP: $locationIP, browserUsed: $browserUsed})",
        "params": ["sid", "firstName", "lastName", "gender", "birthday", "creationDate", "locationIP", "browserUsed"],
        "keyed": "Person",
    },
    "IU2_add_like": {
        "cypher": "MATCH (p:Person), (m:Post) WHERE p.sid = $person AND m.sid = $post CREATE (p)-[:LIKES]->(m)",
        "params": ["person", "post"],
    },
    "IU3_add_like_comment": {
        "cypher": "MATCH (p:Person), (c:Comment) WHERE p.sid = $person AND c.sid = $comment CREATE (p)-[:LIKES]->(c)",
        "params": ["person", "comment"],
    },
    "IU4_add_forum": {
        "cypher": "MATCH (mod:Person) WHERE mod.sid = $moderator CREATE (f:Forum {<<KEY>>sid: $sid, title: $title, creationDate: $creationDate})-[:HAS_MODERATOR]->(mod)",
        "params": ["sid", "title", "creationDate", "moderator"],
        "keyed": "Forum",
    },
    "IU5_add_membership": {
        "cypher": "MATCH (f:Forum), (p:Person) WHERE f.sid = $forum AND p.sid = $person CREATE (f)-[:HAS_MEMBER]->(p)",
        "params": ["forum", "person"],
    },
    "IU6_add_post": {
        "cypher": "MATCH (p:Person), (f:Forum) WHERE p.sid = $person AND f.sid = $forum CREATE (m:Post {<<KEY>>sid: $sid, content: $content, creationDate: $creationDate, browserUsed: $browserUsed})-[:HAS_CREATOR]->(p), (f)-[:CONTAINER_OF]->(m)",
        "params": ["sid", "content", "creationDate", "browserUsed", "person", "forum"],
        "keyed": "Post",
    },
    "IU7_add_comment": {
        "cypher": "MATCH (p:Person), (r:Post) WHERE p.sid = $person AND r.sid = $post CREATE (c:Comment {<<KEY>>sid: $sid, content: $content, creationDate: $creationDate})-[:HAS_CREATOR]->(p), (c)-[:REPLY_OF]->(r)",
        "params": ["sid", "content", "creationDate", "person", "post"],
        "keyed": "Comment",
    },
    "IU8_add_friendship": {
        "cypher": "MATCH (a:Person), (b:Person) WHERE a.sid = $friendA AND b.sid = $friendB CREATE (a)-[:KNOWS]->(b)",
        "params": ["friendA", "friendB"],
    },
}

# One delete per insert, run on what the inserts created, so the whole run leaves the graph as it found it.
DELETES = {
    "DEL1_delete_person": {"cypher": "MATCH (p:Person) WHERE p.sid = $sid DETACH DELETE p", "params": ["sid"]},
    "DEL2_delete_like": {"cypher": "MATCH (p:Person)-[r:LIKES]->(m:Post) WHERE p.sid = $person AND m.sid = $post DELETE r", "params": ["person", "post"]},
    "DEL3_delete_like_comment": {"cypher": "MATCH (p:Person)-[r:LIKES]->(c:Comment) WHERE p.sid = $person AND c.sid = $comment DELETE r", "params": ["person", "comment"]},
    "DEL4_delete_forum": {"cypher": "MATCH (f:Forum) WHERE f.sid = $sid DETACH DELETE f", "params": ["sid"]},
    "DEL5_delete_membership": {"cypher": "MATCH (f:Forum)-[r:HAS_MEMBER]->(p:Person) WHERE f.sid = $forum AND p.sid = $person DELETE r", "params": ["forum", "person"]},
    "DEL6_delete_post": {"cypher": "MATCH (m:Post) WHERE m.sid = $sid DETACH DELETE m", "params": ["sid"]},
    "DEL7_delete_comment": {"cypher": "MATCH (c:Comment) WHERE c.sid = $sid DETACH DELETE c", "params": ["sid"]},
    "DEL8_delete_friendship": {"cypher": "MATCH (a:Person)-[r:KNOWS]->(b:Person) WHERE a.sid = $friendA AND b.sid = $friendB DELETE r", "params": ["friendA", "friendB"]},
}
_DELETE_OF = {
    "DEL1_delete_person": ("IU1_add_person", ["sid"]),
    "DEL2_delete_like": ("IU2_add_like", ["person", "post"]),
    "DEL3_delete_like_comment": ("IU3_add_like_comment", ["person", "comment"]),
    "DEL4_delete_forum": ("IU4_add_forum", ["sid"]),
    "DEL5_delete_membership": ("IU5_add_membership", ["forum", "person"]),
    "DEL6_delete_post": ("IU6_add_post", ["sid"]),
    "DEL7_delete_comment": ("IU7_add_comment", ["sid"]),
    "DEL8_delete_friendship": ("IU8_add_friendship", ["friendA", "friendB"]),
}

_NEW_NODES = {"IU1_add_person": "Person", "IU4_add_forum": "Forum", "IU6_add_post": "Post", "IU7_add_comment": "Comment"}
_EDGES = {
    "IU1_add_person": [],
    "IU2_add_like": ["LIKES"],
    "IU3_add_like_comment": ["LIKES"],
    "IU4_add_forum": ["HAS_MODERATOR"],
    "IU5_add_membership": ["HAS_MEMBER"],
    "IU6_add_post": ["HAS_CREATOR", "CONTAINER_OF"],
    "IU7_add_comment": ["HAS_CREATOR", "REPLY_OF"],
    "IU8_add_friendship": ["KNOWS"],
}


def statement(name, engine):
    """The statement text for an engine. IVG keys a node by its `id` property; the others have no use for it."""
    return {**UPDATES, **DELETES}[name]["cypher"].replace("<<KEY>>", "id: $key, " if engine == "ivg" else "")


def with_key(params, label):
    """The same parameters plus the IVG node key (`Label:sid`, as the loaders build it)."""
    return {**params, "key": f"{label}:{params['sid']}"}


def update_params(nodes, n, seed, id_offset=0, taken=None):
    """{update name: [n parameter dicts]}. `nodes` maps Person/Post/Forum to the ids that exist in the loaded graph."""
    rnd = random.Random(seed)
    persons, posts, forums = nodes["Person"], nodes["Post"], nodes["Forum"]
    comments = nodes.get("Comment") or []
    # Pairs the graph already has: IVG keeps one edge per (source, type, target), so a repeated pair adds nothing there and adds a
    # parallel edge on Neo4j and Memgraph. Updates draw only pairs that are new, so every engine adds the same edges.
    taken = {k: set(v) for k, v in (taken or {}).items()}
    counter = [NEW_ID_BASE + id_offset]

    def new_id():
        counter[0] += 1
        return str(counter[0])

    out = {}
    for name, spec in UPDATES.items():
        sets = []
        for i in range(n):
            if name == "IU1_add_person":
                ps = {"sid": new_id(), "firstName": f"New{i % 7}", "lastName": f"Person{i}", "gender": "female" if i % 2 else "male",
                      "birthday": "1990-05-17", "creationDate": "2012-09-01T00:00:00.000+00:00", "locationIP": "10.0.0.1", "browserUsed": "Firefox"}
            elif name == "IU2_add_like":
                while True:
                    pr = (rnd.choice(persons), rnd.choice(posts))
                    if pr not in taken.setdefault("LIKES", set()):
                        taken["LIKES"].add(pr)
                        break
                ps = {"person": pr[0], "post": pr[1]}
            elif name == "IU3_add_like_comment":
                while True:
                    pr = (rnd.choice(persons), rnd.choice(comments))
                    if pr not in taken.setdefault("LIKES", set()):
                        taken["LIKES"].add(pr)
                        break
                ps = {"person": pr[0], "comment": pr[1]}
            elif name == "IU4_add_forum":
                ps = {"sid": new_id(), "title": f"Forum of New{i}", "creationDate": "2012-09-01T00:00:00.000+00:00", "moderator": rnd.choice(persons)}
            elif name == "IU5_add_membership":
                while True:
                    pr = (rnd.choice(forums), rnd.choice(persons))
                    if pr not in taken.setdefault("HAS_MEMBER", set()):
                        taken["HAS_MEMBER"].add(pr)
                        break
                ps = {"forum": pr[0], "person": pr[1]}
            elif name == "IU6_add_post":
                ps = {"sid": new_id(), "content": f"post {i} " + "x" * 40, "creationDate": "2012-09-02T00:00:00.000+00:00",
                      "browserUsed": "Chrome", "person": rnd.choice(persons), "forum": rnd.choice(forums)}
            elif name == "IU7_add_comment":
                ps = {"sid": new_id(), "content": f"comment {i} " + "y" * 20, "creationDate": "2012-09-03T00:00:00.000+00:00",
                      "person": rnd.choice(persons), "post": rnd.choice(posts)}
            else:
                while True:
                    a, b = rnd.sample(persons, 2)
                    if frozenset((a, b)) not in taken.setdefault("KNOWS", set()):
                        taken["KNOWS"].add(frozenset((a, b)))
                        break
                ps = {"friendA": a, "friendB": b}
            sets.append(ps)
        out[name] = sets
    return out


def delete_params(inserts):
    """{delete name: parameters} that remove exactly what the insert parameters created."""
    return {name: [{k: ps[k] for k in keys} for ps in inserts[src]] for name, (src, keys) in _DELETE_OF.items()}


def expected_delta(params):
    """What the whole run should add: node counts per label and edge counts per type."""
    nodes, edges = {}, {}
    for name, sets in params.items():
        if name in _NEW_NODES:
            nodes[_NEW_NODES[name]] = nodes.get(_NEW_NODES[name], 0) + len(sets)
        for etype in _EDGES[name]:
            edges[etype] = edges.get(etype, 0) + len(sets)
    return {"nodes": nodes, "edges": edges}


def existing_ids(root):
    """Person, Post and Forum ids in the loaded dataset (the pool the updates pick from)."""
    from scripts.bench.alt import snb_model as model

    pool = {"Person": [], "Post": [], "Forum": [], "Comment": []}
    for n in model.iter_nodes(root):
        if n["label"] in pool:
            pool[n["label"]].append(n["id"])
    return pool


def existing_pairs(root):
    """The (source, target) pairs of the edge types the updates add, as the loaded graph has them."""
    from scripts.bench.alt import snb_model as model

    taken = {"LIKES": set(), "HAS_MEMBER": set(), "KNOWS": set()}
    for etype in ("LIKES", "HAS_MEMBER"):
        for e in model.iter_edges_of(root, etype):
            taken[etype].add((e["src"][1], e["dst"][1]))
    for e in model.iter_edges_of(root, "KNOWS"):
        taken["KNOWS"].add(frozenset((e["src"][1], e["dst"][1])))
    return taken
