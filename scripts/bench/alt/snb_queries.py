"""The LDBC SNB read queries every engine runs (Cypher subset that IVG, Neo4j and Memgraph all parse) and their parameters.

Interactive short and complex reads in the repo's Cypher dialect plus a few BI-style aggregates. Ids are strings and dates
ISO strings (see snb_model). `sample_params` draws the start people from the people with the most friends so that the
multi-hop queries do work, deterministically from a seed, so every engine gets the same parameters.
"""

from __future__ import annotations

import random
from collections import Counter

try:  # the worker runs as a script under a side's tree, whose own `scripts` package may not have this module
    from scripts.bench.alt import snb_model as model
except ImportError:
    import snb_model as model

QUERIES = {
    "IS1_profile": {
        "cypher": "MATCH (p:Person) WHERE p.sid = $personId RETURN p.firstName, p.lastName, p.birthday, p.locationIP, p.browserUsed, p.gender, p.creationDate",
        "params": ["personId"],
    },
    "IS2_recent_posts": {
        "cypher": "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId RETURN m.sid, m.creationDate ORDER BY m.creationDate DESC, m.sid LIMIT 10",
        "params": ["personId"],
    },
    "IS3_friends": {
        "cypher": "MATCH (p:Person)-[:KNOWS]-(f:Person) WHERE p.sid = $personId RETURN f.sid, f.firstName, f.lastName ORDER BY f.sid",
        "params": ["personId"],
    },
    "IS6_forum_of_post": {
        "cypher": "MATCH (m:Post)<-[:CONTAINER_OF]-(f:Forum)-[:HAS_MODERATOR]->(mod:Person) WHERE m.sid = $postId RETURN f.sid, f.title, mod.sid, mod.firstName, mod.lastName",
        "params": ["postId"],
    },
    "IC1_friends_named": {
        "cypher": "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId AND f.firstName = $firstName AND f.sid <> $personId RETURN DISTINCT f.sid, f.lastName ORDER BY f.sid LIMIT 20",
        "params": ["personId", "firstName"],
    },
    "IC2_friends_posts": {
        "cypher": "MATCH (p:Person)-[:KNOWS]-(f:Person)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId AND m.creationDate < $maxDate RETURN f.sid, f.firstName, f.lastName, m.sid, m.creationDate ORDER BY m.creationDate DESC, m.sid LIMIT 20",
        "params": ["personId", "maxDate"],
    },
    "IC3_foaf_count": {
        "cypher": "MATCH (p:Person)-[:KNOWS*2..2]-(f:Person) WHERE p.sid = $personId AND f.sid <> $personId RETURN count(DISTINCT f)",
        "params": ["personId"],
    },
    "IC5_forums_of_friends": {
        "cypher": "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_MEMBER]-(forum:Forum) WHERE p.sid = $personId AND f.sid <> $personId RETURN forum.title AS title, count(DISTINCT f) AS c ORDER BY c DESC, title LIMIT 20",
        "params": ["personId"],
        # IVG cannot prepare an aggregate with ORDER BY and LIMIT; it runs without the LIMIT and the first 20 rows are kept client-side
        "ivg_client_limit": 20,
    },
    "IC6_tag_cooccurrence": {
        "cypher": "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR]-(m:Post)-[:HAS_TAG]->(t:Tag), (m)-[:HAS_TAG]->(o:Tag) WHERE p.sid = $personId AND t.name = $tagName AND o.name <> $tagName AND f.sid <> $personId RETURN o.name AS name, count(DISTINCT m) AS c ORDER BY c DESC, name LIMIT 10",
        "params": ["personId", "tagName"],
        # IVG cannot prepare an aggregate with ORDER BY and LIMIT; it runs without the LIMIT and the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "IC7_likers": {
        "cypher": "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post)<-[:LIKES]-(l:Person) WHERE p.sid = $personId RETURN l.sid, l.firstName, m.sid, m.creationDate ORDER BY m.creationDate DESC, l.sid LIMIT 20",
        "params": ["personId"],
    },
    "IC8_replies": {
        "cypher": "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post)<-[:REPLY_OF]-(c:Comment)-[:HAS_CREATOR]->(a:Person) WHERE p.sid = $personId RETURN a.sid, a.firstName, c.sid, c.creationDate ORDER BY c.creationDate DESC, c.sid LIMIT 20",
        "params": ["personId"],
    },
    "IC9_recent_in_network": {
        "cypher": "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId AND m.creationDate < $maxDate AND f.sid <> $personId RETURN DISTINCT f.sid, m.sid, m.creationDate ORDER BY m.creationDate DESC, m.sid LIMIT 20",
        "params": ["personId", "maxDate"],
    },
    "IC13_shortest_path": {
        "cypher": "MATCH path = shortestPath((a:Person {sid: $personId})-[:KNOWS*..6]-(b:Person {sid: $person2Id})) RETURN length(path)",
        # Memgraph has no shortestPath(); its breadth-first expansion finds the same shortest path
        "memgraph": "MATCH (a:Person {sid: $personId}), (b:Person {sid: $person2Id}) MATCH path = (a)-[:KNOWS *BFS ..6]-(b) RETURN size(path)",
        # IVG executes shortestPath through its own path routine, which reads the endpoints' node keys
        "ivg": "MATCH path = shortestPath((a {node_id: $personKey})-[:KNOWS*..6]-(b {node_id: $person2Key})) RETURN length(path)",
        "params": ["personId", "person2Id", "personKey", "person2Key"],
    },
    "BI_posts_per_tag": {
        "cypher": "MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) RETURN t.name AS name, count(m) AS c ORDER BY c DESC, name LIMIT 10",
        "params": [],
        # IVG cannot prepare an aggregate with ORDER BY and LIMIT; it runs without the LIMIT and the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI_comments_per_person": {
        "cypher": "MATCH (c:Comment)-[:HAS_CREATOR]->(p:Person) RETURN p.sid AS person, count(c) AS n ORDER BY n DESC, person LIMIT 10",
        "params": [],
        # IVG cannot prepare an aggregate with ORDER BY and LIMIT; it runs without the LIMIT and the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI_count_persons": {
        "cypher": "MATCH (p:Person) RETURN count(p)",
        "params": [],
    },
}


def ivg_cypher(spec):
    """The text IVG runs: its override if any, and the query without its LIMIT when the limit moves client-side."""
    text = spec.get("ivg", spec["cypher"])
    if "ivg_client_limit" in spec:
        text = text.rsplit("LIMIT", 1)[0].rstrip()
    return text


def sample_params(root, n=20, seed=42):
    """n parameter sets: people drawn from the top-degree half, plus a tag, a date, a first name and a post."""
    deg = Counter()
    for e in model.iter_edges_of(root, "KNOWS"):
        deg[e["src"][1]] += 1
        deg[e["dst"][1]] += 1
    rng = random.Random(seed)
    people = [p for p, _ in sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))]
    pool = people[: max(n * 4, len(people) // 2)]
    chosen = rng.sample(pool, min(n, len(pool)))
    first = {r["id"]: r.get("firstName", "") for r in model._rows(root, "dynamic/Person")}
    tags = [r["name"] for r in model._rows(root, "static/Tag")]
    posts = [r for r in model._rows(root, "dynamic/Post")]
    dates = sorted(r["creationDate"] for r in posts)
    out = []
    for i, pid in enumerate(chosen):
        out.append({
            "personId": pid,
            "person2Id": chosen[(i + 1) % len(chosen)],
            "firstName": first.get(chosen[(i + 3) % len(chosen)], "") or "Ann",
            "tagName": rng.choice(tags),
            "maxDate": dates[len(dates) * 3 // 4] if dates else "2012-01-01T00:00:00.000+00:00",
            "postId": rng.choice(posts)["id"] if posts else "0",
            "personKey": model.node_key("Person", pid),
            "person2Key": model.node_key("Person", chosen[(i + 1) % len(chosen)]),
        })
    return out
