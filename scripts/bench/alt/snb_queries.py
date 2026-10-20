"""The LDBC SNB read queries every engine runs (Cypher subset that IVG, Neo4j and Memgraph all parse) and their parameters.

Interactive short and complex reads in the repo's Cypher dialect plus a few BI-style aggregates. Ids are strings and dates
ISO strings (see snb_model). `sample_params` draws the start people from the people with the most friends so that the
multi-hop queries do work, deterministically from a seed, so every engine gets the same parameters.
"""

from __future__ import annotations

import os
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
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 20 rows are kept client-side
        "ivg_client_limit": 20,
    },
    "IC6_tag_cooccurrence": {
        "cypher": "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR]-(m:Post)-[:HAS_TAG]->(t:Tag), (m)-[:HAS_TAG]->(o:Tag) WHERE p.sid = $personId AND t.name = $tagName AND o.name <> $tagName AND f.sid <> $personId RETURN o.name AS name, count(DISTINCT m) AS c ORDER BY c DESC, name LIMIT 10",
        "params": ["personId", "tagName"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
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
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI_comments_per_person": {
        "cypher": "MATCH (c:Comment)-[:HAS_CREATOR]->(p:Person) RETURN p.sid AS person, count(c) AS n ORDER BY n DESC, person LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI01_posting_summary": {
        "cypher": "MATCH (m:Post) WHERE m.creationDate < $maxDate RETURN substring(m.creationDate, 0, 4) AS year, count(m) AS n ORDER BY year",
        "params": ["maxDate"],
    },
    "BI02_tag_evolution": {
        "cypher": "MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) WHERE m.creationDate > $minDate AND m.creationDate < $midDate RETURN t.name AS tag, count(m) AS n ORDER BY n DESC, tag LIMIT 10",
        "params": ["minDate", "midDate"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI03_popular_topics_in_country": {
        "cypher": "MATCH (c:Place)<-[:IS_LOCATED_IN]-(m:Post)-[:HAS_TAG]->(t:Tag) WHERE c.name = $country RETURN t.name AS tag, count(DISTINCT m) AS n ORDER BY n DESC, tag LIMIT 10",
        "params": ["country"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI04_top_forums_by_country": {
        "cypher": "MATCH (c:Place)<-[:IS_PART_OF]-(city:Place)<-[:IS_LOCATED_IN]-(p:Person)<-[:HAS_MEMBER]-(f:Forum) WHERE c.name = $country RETURN f.title AS title, count(DISTINCT p) AS n ORDER BY n DESC, title LIMIT 10",
        "params": ["country"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI05_active_posters_on_tag": {
        "cypher": "MATCH (t:Tag)<-[:HAS_TAG]-(m:Post)-[:HAS_CREATOR]->(p:Person) WHERE t.name = $tagName RETURN p.sid AS person, count(m) AS n ORDER BY n DESC, person LIMIT 10",
        "params": ["tagName"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI06_authoritative_users": {
        "cypher": "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post)<-[:LIKES]-(l:Person) RETURN p.sid AS person, count(l) AS n ORDER BY n DESC, person LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI07_related_topics": {
        "cypher": "MATCH (t:Tag)<-[:HAS_TAG]-(m:Post)<-[:REPLY_OF]-(c:Comment)-[:HAS_TAG]->(r:Tag) WHERE t.name = $tagName AND r.name <> $tagName RETURN r.name AS tag, count(DISTINCT c) AS n ORDER BY n DESC, tag LIMIT 10",
        "params": ["tagName"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI08_central_person_for_tag": {
        "cypher": "MATCH (t:Tag)<-[:HAS_INTEREST]-(p:Person) WHERE t.name = $tagName RETURN p.sid AS person, count(t) AS n ORDER BY n DESC, person LIMIT 10",
        "params": ["tagName"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI09_top_thread_initiators": {
        "cypher": "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post)<-[:REPLY_OF]-(c:Comment) RETURN p.sid AS person, count(DISTINCT m) AS threads, count(c) AS replies ORDER BY replies DESC, person LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI11_friend_triangles": {
        "cypher": "MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)-[:KNOWS]-(a) WHERE a.sid < b.sid AND b.sid < c.sid RETURN count(*)",
        "params": [],
    },
    "BI12_person_posting_distribution": {
        "cypher": "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n RETURN n AS postCount, count(p) AS people ORDER BY people DESC, postCount LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI14_international_dialog": {
        "cypher": "MATCH (c1:Place)<-[:IS_PART_OF]-(x:Place)<-[:IS_LOCATED_IN]-(a:Person)-[:KNOWS]-(b:Person)-[:IS_LOCATED_IN]->(y:Place)-[:IS_PART_OF]->(c2:Place) WHERE c1.name = $country AND c2.name <> $country RETURN c2.name AS other, count(*) AS n ORDER BY n DESC, other LIMIT 10",
        "params": ["country"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI18_friend_recommendation": {
        "cypher": "MATCH (p:Person)-[:KNOWS]-(m:Person)-[:KNOWS]-(f:Person) WHERE p.sid = $personId AND f.sid <> $personId RETURN f.sid AS person, count(DISTINCT m) AS mutual ORDER BY mutual DESC, person LIMIT 20",
        "params": ["personId"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 20 rows are kept client-side
        "ivg_client_limit": 20,
    },
    "BI10_experts_in_circle": {
        "cypher": "MATCH (p:Person)-[:KNOWS*2..2]-(e:Person)<-[:HAS_CREATOR]-(m:Post)-[:HAS_TAG]->(t:Tag) WHERE p.sid = $personId AND e.sid <> $personId RETURN t.name AS tag, count(DISTINCT m) AS n ORDER BY n DESC, tag LIMIT 10",
        "params": ["personId"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI13_low_activity_people": {
        "cypher": "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n WHERE n < 3 RETURN count(p)",
        "params": [],
    },
    "BI16_tag_window_posters": {
        "cypher": "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post)-[:HAS_TAG]->(t:Tag) WHERE t.name = $tagName AND m.creationDate > $minDate AND m.creationDate < $midDate RETURN p.sid AS person, count(m) AS n ORDER BY n DESC, person LIMIT 10",
        "params": ["tagName", "minDate", "midDate"],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI17_propagation_reach": {
        "cypher": "MATCH (t:Tag)<-[:HAS_TAG]-(m:Post)<-[:REPLY_OF]-(c:Comment)-[:HAS_CREATOR]->(p:Person) WHERE t.name = $tagName RETURN count(DISTINCT p)",
        "params": ["tagName"],
    },
    "BIW_weighted_shortest_path": {
        # BI15 (trusted connection paths), BI19 (interaction path between cities) and BI20 (recruitment) are weighted shortest paths
        # over interaction weights (snb_weights); the core operation, the cost between two people, is what is timed. The weights are
        # written to every engine before the run (`snb.py weights`).
        "cypher": "MATCH (s:Person {sid: $personId}), (t:Person {sid: $person2Id}) CALL gds.shortestPath.dijkstra.stream('snbw', {sourceNode: s, targetNode: t, relationshipWeightProperty: 'weight'}) YIELD totalCost RETURN totalCost",
        "memgraph": "MATCH (a:Person {sid: $personId})-[r:KNOWS *WSHORTEST (r, n | r.weight) total_weight]-(b:Person {sid: $person2Id}) RETURN total_weight",
        "ivg": "CALL ivg.shortestPath.weighted($personKey, $person2Key, 'weight', 9999, 10, 'both', ['KNOWS']) YIELD totalCost RETURN totalCost",
        "ivg_inline": True,
        "ivg_pick": 1,
        "params": ["personId", "person2Id", "personKey", "person2Key"],
    },
    "BI_count_persons": {
        "cypher": "MATCH (p:Person) RETURN count(p)",
        "params": [],
    },
    "BI_forum_members": {
        "cypher": "MATCH (f:Forum)-[:HAS_MEMBER]->(p:Person) RETURN f.title AS title, count(p) AS n ORDER BY n DESC, title LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI_posts_per_country": {
        "cypher": "MATCH (m:Post)-[:IS_LOCATED_IN]->(c:Place) RETURN c.name AS country, count(m) AS n ORDER BY n DESC, country LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI_posts_per_tagclass": {
        "cypher": "MATCH (m:Post)-[:HAS_TAG]->(t:Tag)-[:HAS_TYPE]->(tc:TagClass) RETURN tc.name AS cls, count(m) AS n ORDER BY n DESC, cls LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI_top_friend_counts": {
        "cypher": "MATCH (p:Person)-[:KNOWS]->(f:Person) RETURN p.sid AS person, count(f) AS n ORDER BY n DESC, person LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI_replies_per_post": {
        "cypher": "MATCH (c:Comment)-[:REPLY_OF]->(m:Post) RETURN m.sid AS post, count(c) AS n ORDER BY n DESC, post LIMIT 10",
        "params": [],
        # trees before dd35f0b0 cannot prepare an aggregate with ORDER BY and LIMIT: there (IVG_BENCH_CLIENT_LIMIT=1) the first 10 rows are kept client-side
        "ivg_client_limit": 10,
    },
    "BI_likes_total": {
        "cypher": "MATCH (p:Person)-[:LIKES]->(m:Post) RETURN count(m)",
        "params": [],
    },
}


def ivg_cypher(spec):
    """The text IVG runs: its override if any. With IVG_BENCH_CLIENT_LIMIT=1 (a tree before dd35f0b0, whose translator
    could not prepare an aggregate with ORDER BY and LIMIT) the query loses its LIMIT, which then moves client-side."""
    text = spec.get("ivg", spec["cypher"])
    if "ivg_client_limit" in spec and os.environ.get("IVG_BENCH_CLIENT_LIMIT") == "1":
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
    countries = sorted(r["name"] for r in model._rows(root, "static/Place") if r.get("type") == "Country") or ["India"]
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
            "minDate": dates[len(dates) // 4] if dates else "2011-01-01T00:00:00.000+00:00",
            "midDate": dates[len(dates) // 2] if dates else "2011-07-01T00:00:00.000+00:00",
            "country": rng.choice(countries),
            "personKey": model.node_key("Person", pid),
            "person2Key": model.node_key("Person", chosen[(i + 1) % len(chosen)]),
        })
    return out
