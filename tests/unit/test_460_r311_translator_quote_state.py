"""4.6.0 R311 [SECURITY]: no Cypher text or parameter value changes the SQL statement the translator builds.

A backtick name, a string literal or a parameter value holding a quote reached the SQL unescaped at 47 places on the
release-4.6.0 head (this battery, built by the 4.6.0 security sweep): `RETURN keys($m)` pasted the parameter map's keys
into a SQL literal, a relationship property key went into `'$.key'` as written, a label of `(n:A|B)` went between quotes
as written, `RETURN x AS \\`a FROM t --\\`` gave `... AS a FROM t --`, and `(a {id: $p})-[:T]->()` under
IVG_EDGESCAN=1 pasted `$p`'s value.

Every case translates a query whose names, literals and values carry a marker, `ZQ'X`, `ZQ"X` or `ZQ X`, and reads the
SQL with a small lexer that knows SQL's three states (code, a '...' literal, a "..." identifier; a doubled quote stays
inside). A marker whose two ends sit in different states closed a quote the translator opened; a marker in code whose
middle character is not one an identifier holds is an unquoted name. Either is the statement changed by the text. A
query the translator refuses is not a breakout.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

import iris_vector_graph.cypher.translator as translator_module
from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.parser import CypherParseError, parse_query
from iris_vector_graph.cypher.translator import (
    _NAME_FIELDS,
    _jsontable_key,
    _safe_alias,
    _subquery_lateral_inline_param,
    translate_to_sql,
)

L = r"'ZQ\'X'"  # a Cypher string literal holding ZQ'X
Q = "`ZQ'X`"  # a backtick name holding ZQ'X
D = '`ZQ"X`'  # a backtick name holding ZQ"X
S = "`ZQ X`"  # a backtick name holding a space: no quote, but not an identifier either
P = "ZQ'X"  # a parameter value or key

CASES: list = []


def add(tag, q, params=None):
    CASES.append((tag, q, params))


# --- string literals and parameter values ---------------------------------------------------------------------------
for op in ["=", "<>", "<", ">=", "STARTS WITH", "ENDS WITH", "CONTAINS", "=~"]:
    add(f"lit WHERE {op}", f"MATCH (n) WHERE n.name {op} {L} RETURN n.name")
    add(f"param WHERE {op}", f"MATCH (n) WHERE n.name {op} $p RETURN n.name", {"p": P})
add("lit IN", f"MATCH (n) WHERE n.name IN [{L}, 'b'] RETURN n.name")
add("param IN list", "MATCH (n) WHERE n.name IN $p RETURN n.name", {"p": [P, "b"]})
add("lit inline map", f"MATCH (n {{name: {L}}}) RETURN n.name")
add("param inline map", "MATCH (n {name: $p}) RETURN n.name", {"p": P})
add("lit id", f"MATCH (n) WHERE n.id = {L} RETURN n")
add("param id", "MATCH (n) WHERE n.id = $p RETURN n", {"p": P})
add("lit id()", f"MATCH (n) WHERE id(n) = {L} RETURN n")
add("param id()", "MATCH (n) WHERE id(n) = $p RETURN n", {"p": P})
add("param id() IN", "MATCH (n) WHERE id(n) IN $p RETURN n", {"p": [P]})
add("lit RETURN", f"RETURN {L} AS x")
add("lit RETURN match", f"MATCH (n) RETURN {L} AS x, n.name")
add("lit list RETURN", f"RETURN [{L}, 'b'] AS x")
add("lit map RETURN", f"RETURN {{a: {L}}} AS x")
add("lit map key RETURN", f"RETURN {{{Q}: 1}} AS x")
add("param list RETURN", "RETURN $p AS x", {"p": [P]})
add("param map RETURN", "RETURN $p AS x", {"p": {P: P}})
add("param map key access", "RETURN $p.k AS x", {"p": {"k": P}})
add("lit toUpper", f"MATCH (n) RETURN toUpper({L}) AS x")
add("lit replace", f"MATCH (n) RETURN replace(n.name, {L}, {L}) AS x")
add("lit split", f"MATCH (n) RETURN split(n.name, {L}) AS x")
add("lit concat", f"MATCH (n) RETURN n.name + {L} AS x")
add("lit coalesce", f"MATCH (n) RETURN coalesce(n.name, {L}) AS x")
add("lit CASE", f"MATCH (n) RETURN CASE WHEN n.name = {L} THEN {L} ELSE 'z' END AS x")
add("lit CASE simple", f"MATCH (n) RETURN CASE n.name WHEN {L} THEN 1 ELSE 0 END AS x")
add("lit labels()", f"MATCH (n) WHERE {L} IN labels(n) RETURN n")
add("lit type()", f"MATCH (a)-[r]->(b) WHERE type(r) = {L} RETURN a")
add("lit date", f"RETURN date({L}) AS x")
add("lit datetime", f"RETURN datetime({L}) AS x")
add("lit duration", f"RETURN duration({L}) AS x")
add("lit WITH", f"MATCH (n) WITH n, {L} AS s WHERE n.name = s RETURN s")
add("lit UNWIND", f"UNWIND [{L}, 'b'] AS x RETURN x")
add("param UNWIND", "UNWIND $p AS x RETURN x", {"p": [P, "b"]})
add("param UNWIND maps", "UNWIND $p AS r MATCH (n {name: r.name}) RETURN n", {"p": [{"name": P}]})
add("param UNWIND map key", "UNWIND $p AS r RETURN r.k AS x", {"p": [{"k": P, P: 1}]})
add("lit ORDER BY", f"MATCH (n) RETURN n.name ORDER BY {L}")
add("lit exists sub", f"MATCH (n) WHERE EXISTS {{ MATCH (n)-->(m) WHERE m.name = {L} }} RETURN n")
add("lit pattern pred", f"MATCH (n) WHERE (n)-->({{name: {L}}}) RETURN n")
add("lit rel prop", f"MATCH (a)-[r {{w: {L}}}]->(b) RETURN a")
add("param rel prop", "MATCH (a)-[r {w: $p}]->(b) RETURN a", {"p": P})
add("lit rel prop where", f"MATCH (a)-[r]->(b) WHERE r.w = {L} RETURN a")
add("lit varlen node prop", f"MATCH (a {{name: {L}}})-[*1..3]->(b) RETURN b")
add("param varlen node prop", "MATCH (a {name: $p})-[*1..3]->(b) RETURN b", {"p": P})
add("lit varlen id", f"MATCH (a)-[*1..3]->(b) WHERE a.id = {L} RETURN b")
add("param varlen id", "MATCH (a)-[*1..3]->(b) WHERE a.id = $p RETURN b", {"p": P})
add("lit shortestPath", f"MATCH p=shortestPath((a {{id:{L}}})-[*]-(b {{id:'y'}})) RETURN p")
add("param shortestPath", "MATCH p=shortestPath((a {id:$p})-[*]-(b {id:$q})) RETURN p", {"p": P, "q": P})
add("lit OPTIONAL", f"MATCH (a) OPTIONAL MATCH (a)-->(b {{name:{L}}}) RETURN a, b")
add("lit any()", f"MATCH (n) WHERE any(x IN n.tags WHERE x = {L}) RETURN n")
add("lit list comp", f"MATCH (n) RETURN [x IN n.tags WHERE x <> {L}] AS t")
add("lit reduce", f"MATCH (n) RETURN reduce(s = {L}, x IN n.tags | s + x) AS t")
add("lit IS NULL coalesce", f"MATCH (n) WHERE coalesce(n.name, {L}) = {L} RETURN n")
add("lit count distinct", f"MATCH (n) RETURN count(DISTINCT {L}) AS c")
add("lit collect", f"MATCH (n) RETURN collect({L}) AS c")
add("lit UNION", f"RETURN {L} AS x UNION RETURN 'b' AS x")
add("lit CALL subq", f"MATCH (n) CALL {{ WITH n MATCH (n)-->(m) WHERE m.name = {L} RETURN m }} RETURN m")
add("lit point", f"RETURN point({{x:1, y:2, crs:{L}}}) AS p")
add("lit temporal pred", f"MATCH (a)-[r]->(b) WHERE r.ts > datetime({L}) RETURN a")
add("lit toInteger", f"RETURN toInteger({L}) AS x")
add("lit left", f"MATCH (n) RETURN left({L}, 2) AS x")
add("lit trim", f"MATCH (n) RETURN trim({L}) AS x")
add("lit toString", f"MATCH (n) RETURN toString({L}) AS x")
add("lit size", f"MATCH (n) RETURN size({L}) AS x")
add("lit reverse", f"MATCH (n) RETURN reverse({L}) AS x")
add("lit head list", f"MATCH (n) RETURN head([{L}]) AS x")
add("lit list IN prop", f"MATCH (n) WHERE {L} IN n.tags RETURN n")
add("param in prop list", "MATCH (n) WHERE $p IN n.tags RETURN n", {"p": P})
add("lit list index", f"RETURN [{L}][0] AS x")
add("lit list slice", f"RETURN [{L}, 'b'][0..1] AS x")
add("lit string fns", f"RETURN substring({L}, 0, 1) AS x, toLower({L}) AS y, ltrim({L}) AS z")
add("lit split2", f"RETURN split({L}, {L}) AS x")
add("lit replace2", f"RETURN replace({L}, {L}, {L}) AS x")
add("lit CASE rel", f"MATCH ()-[r]->() RETURN CASE type(r) WHEN {L} THEN 1 END AS x")
add("lit UNION ALL", f"MATCH (n) WHERE n.name = {L} RETURN n.name AS x UNION ALL MATCH (m) WHERE m.name = {L} RETURN m.name AS x")
add("lit FOREACH create", f"FOREACH (x IN [{L}] | CREATE (:P {{name: x}}))")
add("lit STARTS WITH id", f"MATCH (n) WHERE n.id STARTS WITH {L} RETURN n")
add("param STARTS WITH id", "MATCH (n) WHERE n.id STARTS WITH $p RETURN n", {"p": P})
add("lit STARTS WITH id()", f"MATCH (n) WHERE id(n) STARTS WITH {L} RETURN n")
add("lit CONTAINS rel", f"MATCH ()-[r]->() WHERE r.w CONTAINS {L} RETURN r")
add("param CONTAINS rel", "MATCH ()-[r]->() WHERE r.w CONTAINS $p RETURN r", {"p": P})
add("lit regex rel", f"MATCH ()-[r]->() WHERE r.w =~ {L} RETURN r")
add("lit IN rel", f"MATCH ()-[r]->() WHERE r.w IN [{L}] RETURN r")
add("param IN rel", "MATCH ()-[r]->() WHERE r.w IN $p RETURN r", {"p": [P]})
add("lit type IN", f"MATCH ()-[r]->() WHERE type(r) IN [{L}] RETURN r")
add("param type IN", "MATCH ()-[r]->() WHERE type(r) IN $p RETURN r", {"p": [P]})
add("param type eq", "MATCH ()-[r]->() WHERE type(r) = $p RETURN r", {"p": P})
add("param label in", "MATCH (n) WHERE $p IN labels(n) RETURN n", {"p": P})
add("lit id IN", f"MATCH (n) WHERE n.id IN [{L}] RETURN n")
add("lit varlen target id", f"MATCH (a {{id:'a'}})-[*1..2]->(b {{id:{L}}}) RETURN b")
add("param varlen target id", "MATCH (a {id:'a'})-[*1..2]->(b {id:$p}) RETURN b", {"p": P})
add("lit varlen both ids", f"MATCH p=(a {{id:{L}}})-[*1..2]->(b {{id:{L}}}) RETURN p")
add("param varlen both ids", "MATCH p=(a {id:$p})-[*1..2]->(b {id:$p}) RETURN p", {"p": P})
add("lit varlen opt", f"MATCH (a {{id:{L}}}) OPTIONAL MATCH (a)-[*1..2]->(b:L) RETURN b")
add("lit ORDER BY rel", f"MATCH ()-[r]->() RETURN r ORDER BY r.w = {L}")
add("lit EXISTS rel", f"MATCH (a) WHERE EXISTS {{ MATCH (a)-[r]->() WHERE r.w = {L} }} RETURN a")
add("lit WITH WHERE", f"MATCH (n) WITH n.name AS nm WHERE nm STARTS WITH {L} RETURN nm")
add("lit WITH WHERE ctn", f"MATCH (n) WITH n.name AS nm WHERE nm CONTAINS {L} RETURN nm")
add("lit WITH WHERE regex", f"MATCH (n) WITH n.name AS nm WHERE nm =~ {L} RETURN nm")
add("lit agg having", f"MATCH (n) WITH n.name AS nm, count(*) AS c WHERE nm = {L} RETURN nm")
add("lit SET label lit", f"MATCH (n {{id:'a'}}) SET n.name = {L}, n:Foo")
add("lit MERGE ON MATCH +=", f"MERGE (n {{id:'a'}}) ON MATCH SET n += {{name: {L}}}")
add("lit DELETE rel", f"MATCH (a {{id:{L}}})-[r]->() DELETE r")
add("lit WITH create id", f"WITH {L} AS i CREATE (n {{id: i}})")
add("lit WITH create id prop", f"WITH {L} AS i CREATE (n {{id: i, name: {L}}})")
add("param WITH create id", "WITH $p AS i CREATE (n {id: i, name: $p})", {"p": P})
add("lit MERGE id with", f"WITH {L} AS i MERGE (n {{id: i}})")
add("lit rel count by type", f"MATCH ()-[r]->() WHERE type(r) STARTS WITH {L} RETURN count(*)")
add("lit LIKE prefix id", f"MATCH (n) WHERE n.id STARTS WITH {L} AND n.name = 'q' RETURN n")
# --- writes ---------------------------------------------------------------------------------------------------------
add("lit CREATE prop", f"CREATE (n:Person {{name: {L}}}) RETURN n")
add("param CREATE prop", "CREATE (n:Person {name: $p}) RETURN n", {"p": P})
add("param CREATE $props", "CREATE (n:Person $p) RETURN n", {"p": {"name": P, P: P}})
add("lit CREATE id", f"CREATE (n:Person {{id: {L}}}) RETURN n")
add("param CREATE id", "CREATE (n:Person {id: $p}) RETURN n", {"p": P})
add("lit CREATE rel", f"CREATE (a {{id:{L}}})-[:R {{w:{L}}}]->(b {{id:'b'}})")
add("param CREATE rel", "CREATE (a {id:$p})-[:R {w:$p}]->(b {id:$p})", {"p": P})
add("lit MATCH CREATE rel", f"MATCH (a {{id:{L}}}), (b {{id:{L}}}) CREATE (a)-[:R {{w:{L}}}]->(b)")
add("param MATCH CREATE rel", "MATCH (a {id:$p}), (b {id:$p}) CREATE (a)-[:R {w:$p}]->(b)", {"p": P})
add("lit SET", f"MATCH (n {{id:'a'}}) SET n.name = {L}")
add("param SET", "MATCH (n {id:'a'}) SET n.name = $p", {"p": P})
add("param SET +=", "MATCH (n {id:'a'}) SET n += $p", {"p": {"name": P, P: P}})
add("param SET =", "MATCH (n {id:'a'}) SET n = $p", {"p": {"name": P, P: P}})
add("lit SET += map", f"MATCH (n {{id:'a'}}) SET n += {{name: {L}, {Q}: 1}}")
add("lit SET rel", f"MATCH (a)-[r]->(b) SET r.w = {L}")
add("param SET rel +=", "MATCH (a)-[r]->(b) SET r += $p", {"p": {"w": P, P: P}})
add("lit MERGE", f"MERGE (n:Person {{id: {L}}}) ON CREATE SET n.name = {L} ON MATCH SET n.x = {L} RETURN n")
add("param MERGE", "MERGE (n:Person {id: $p}) ON CREATE SET n.name = $p ON MATCH SET n.x = $p RETURN n", {"p": P})
add("param MERGE +=", "MERGE (n:Person {id: $p}) ON CREATE SET n += $m RETURN n", {"p": P, "m": {P: P}})
add("lit MERGE rel", f"MATCH (a {{id:'a'}}), (b {{id:'b'}}) MERGE (a)-[r:R {{w:{L}}}]->(b)")
add("param MERGE rel", "MATCH (a {id:$p}), (b {id:$p}) MERGE (a)-[r:R {w:$p}]->(b)", {"p": P})
add("param UNWIND CREATE", "UNWIND $rows AS r CREATE (n:Person {id: r.id, name: r.name})", {"rows": [{"id": P, "name": P}]})
add("param UNWIND MERGE", "UNWIND $rows AS r MERGE (n:Person {id: r.id}) SET n.name = r.name", {"rows": [{"id": P, "name": P}]})
add("param UNWIND MERGE +=", "UNWIND $rows AS r MERGE (n:Person {id: r.id}) SET n += r.props",
    {"rows": [{"id": P, "props": {P: P}}]})
add("param UNWIND CREATE rel", "UNWIND $rows AS r MATCH (a {id:r.s}), (b {id:r.t}) CREATE (a)-[:R {w:r.w}]->(b)",
    {"rows": [{"s": P, "t": P, "w": P}]})
add("param UNWIND MERGE rel", "UNWIND $rows AS r MATCH (a {id:r.s}), (b {id:r.t}) MERGE (a)-[:R]->(b)",
    {"rows": [{"s": P, "t": P}]})
add("lit DETACH DELETE", f"MATCH (n {{id:{L}}}) DETACH DELETE n")
add("param DETACH DELETE", "MATCH (n {id:$p}) DETACH DELETE n", {"p": P})
add("lit REMOVE", f"MATCH (n {{id:{L}}}) REMOVE n.name")
add("lit SET label", f"MATCH (n {{id:{L}}}) SET n:Foo")
add("lit CREATE list", f"CREATE (n {{id:'a', tags: [{L}]}})")
add("lit CREATE map value", f"CREATE (n {{id:'a', m: {L}}})")
add("lit CREATE rel list", f"CREATE (a {{id:'a'}})-[:R {{t: [{L}]}}]->(b {{id:'b'}})")
add("param CREATE rel list", "CREATE (a {id:'a'})-[:R {t: $p}]->(b {id:'b'})", {"p": [P]})
add("param CREATE list", "CREATE (n {id:'a', tags: $p})", {"p": [P]})
add("param SET list", "MATCH (n {id:'a'}) SET n.tags = $p", {"p": [P]})
add("param SET rel list", "MATCH ()-[r]->() SET r.tags = $p", {"p": [P]})
add("param SET rel", "MATCH ()-[r]->() SET r.w = $p", {"p": P})
add("param SET rel map", "MATCH ()-[r]->() SET r.w = $p", {"p": {P: P}})
add("lit SET rel list", f"MATCH ()-[r]->() SET r.w = [{L}]")
add("param SET = map", "MATCH (n {id:'a'}) SET n = $m", {"m": {P: P}})
add("param SET rel = map", "MATCH ()-[r]->() SET r = $m", {"m": {P: P}})
add("param CREATE rel $map", "MATCH (a {id:'a'}), (b {id:'b'}) CREATE (a)-[:R $m]->(b)", {"m": {P: P}})
add("param UNWIND SET +=", "UNWIND $rows AS row MATCH (n {id: row.id}) SET n += row", {"rows": [{"id": "a", P: P}]})
add("param UNWIND CREATE map", "UNWIND $rows AS row CREATE (n:X) SET n = row", {"rows": [{"id": "a", P: P}]})
# --- LIMIT / SKIP ---------------------------------------------------------------------------------------------------
add("param LIMIT str", "MATCH (n) RETURN n LIMIT $p", {"p": "1 UNION SELECT 1"})
add("param SKIP str", "MATCH (n) RETURN n SKIP $p LIMIT 2", {"p": "1 ZQ'X"})
add("param LIMIT marker", "MATCH (n) RETURN n LIMIT $p", {"p": P})
# --- labels, types, property keys, names, written in the query ------------------------------------------------------
for tag, ID in [("SQ", Q), ("DQ", D), ("SP", S)]:
    add(f"{tag} label MATCH", f"MATCH (n:{ID}) RETURN n")
    add(f"{tag} label WHERE", f"MATCH (n) WHERE n:{ID} RETURN n")
    add(f"{tag} label CREATE", f"CREATE (n:{ID} {{id:'a'}})")
    add(f"{tag} label MERGE", f"MERGE (n:{ID} {{id:'a'}})")
    add(f"{tag} label SET", f"MATCH (n {{id:'a'}}) SET n:{ID}")
    add(f"{tag} label REMOVE", f"MATCH (n {{id:'a'}}) REMOVE n:{ID}")
    add(f"{tag} label count", f"MATCH (n:{ID}) RETURN count(n)")
    add(f"{tag} label 2", f"MATCH (n:A:{ID}) RETURN n")
    add(f"{tag} labels OR", f"MATCH (n:A|{ID}) RETURN n")
    add(f"{tag} labels OR anchor-opt", f"OPTIONAL MATCH (n:A|{ID}) RETURN n")
    add(f"{tag} label varlen", f"MATCH (a)-[*1..2]->(b:{ID}) RETURN b")
    add(f"{tag} label OPTIONAL", f"MATCH (a) OPTIONAL MATCH (a)-->(b:{ID}) RETURN b")
    add(f"{tag} label exists", f"MATCH (a) WHERE EXISTS {{ MATCH (a)-->(:{ID}) }} RETURN a")
    add(f"{tag} label pattern pred", f"MATCH (a) WHERE (a)-->(:{ID}) RETURN a")
    add(f"{tag} label pred return", f"MATCH (n) RETURN n:{ID} AS b")
    add(f"{tag} type MATCH", f"MATCH (a)-[:{ID}]->(b) RETURN a")
    add(f"{tag} type alt", f"MATCH (a)-[:X|{ID}]->(b) RETURN a")
    add(f"{tag} type undirected", f"MATCH (a)-[:{ID}]-(b) RETURN a")
    add(f"{tag} type varlen", f"MATCH (a {{id:'a'}})-[:{ID}*1..3]->(b) RETURN b")
    add(f"{tag} type varlen nobind", f"MATCH (a)-[:{ID}*1..3]->(b) RETURN b")
    add(f"{tag} type varlen alt", f"MATCH (a {{id:'a'}})-[:X|{ID}*1..3]->(b) RETURN b")
    add(f"{tag} type varlen opt", f"MATCH (a {{id:'a'}}) OPTIONAL MATCH (a)-[:{ID}*1..2]->(b) RETURN b")
    add(f"{tag} type shortest", f"MATCH p=shortestPath((a {{id:'a'}})-[:{ID}*]-(b {{id:'b'}})) RETURN p")
    add(f"{tag} type allShortest", f"MATCH p=allShortestPaths((a {{id:'a'}})-[:{ID}*]-(b {{id:'b'}})) RETURN p")
    add(f"{tag} type anchored", f"MATCH (a {{id:'a'}})-[:{ID}]->(b) RETURN b LIMIT 1")
    add(f"{tag} type CREATE", f"MATCH (a {{id:'a'}}), (b {{id:'b'}}) CREATE (a)-[:{ID}]->(b)")
    add(f"{tag} type CREATE lit", f"CREATE (a {{id:'a'}})-[:{ID}]->(b {{id:'b'}})")
    add(f"{tag} type MERGE", f"MATCH (a {{id:'a'}}), (b {{id:'b'}}) MERGE (a)-[:{ID}]->(b)")
    add(f"{tag} type MERGE ON CREATE",
        f"MATCH (a {{id:'a'}}), (b {{id:'b'}}) MERGE (a)-[r:{ID}]->(b) ON CREATE SET r.w = 1")
    add(f"{tag} type count", f"MATCH ()-[r:{ID}]->() RETURN count(r)")
    add(f"{tag} type exists", f"MATCH (a) WHERE EXISTS {{ MATCH (a)-[:{ID}]->() }} RETURN a")
    add(f"{tag} type pattern pred", f"MATCH (a) WHERE (a)-[:{ID}]->() RETURN a")
    add(f"{tag} type size pattern", f"MATCH (a) RETURN size((a)-[:{ID}]->()) AS d")
    add(f"{tag} type COUNT sub", f"MATCH (a) RETURN COUNT {{ (a)-[:{ID}]->() }} AS d")
    add(f"{tag} type OPTIONAL", f"MATCH (a) OPTIONAL MATCH (a)-[:{ID}]->(b) RETURN b")
    add(f"{tag} type 2hop", f"MATCH (a {{id:'a'}})-[:{ID}]->(b)-[:{ID}]->(c) RETURN c")
    add(f"{tag} type path len", f"MATCH p=(a {{id:'a'}})-[:{ID}*1..3]->(b) RETURN length(p)")
    add(f"{tag} type undirected rel var", f"MATCH (a)-[r:{ID}]-(b) RETURN r")
    add(f"{tag} type DELETE", f"MATCH (a)-[r:{ID}]->(b) DELETE r")
    add(f"{tag} type CALL subq", f"MATCH (a) CALL {{ WITH a MATCH (a)-[:{ID}]->(b) RETURN b }} RETURN b")
    add(f"{tag} type pattern compr", f"MATCH (a) RETURN [(a)-[:{ID}]->(b) | b.name] AS x")
    add(f"{tag} label pattern compr", f"MATCH (a) RETURN [(a)-->(b:{ID}) | b.name] AS x")
    add(f"{tag} key WHERE", f"MATCH (n) WHERE n.{ID} = 1 RETURN n")
    add(f"{tag} key RETURN", f"MATCH (n) RETURN n.{ID}")
    add(f"{tag} key IS NULL", f"MATCH (n) WHERE n.{ID} IS NULL RETURN n")
    add(f"{tag} key IS NOT NULL", f"MATCH (n) WHERE n.{ID} IS NOT NULL RETURN n")
    add(f"{tag} key ORDER BY", f"MATCH (n) RETURN n ORDER BY n.{ID}")
    add(f"{tag} key inline map", f"MATCH (n {{{ID}: 1}}) RETURN n")
    add(f"{tag} key inline map str", f"MATCH (n {{{ID}: 'v'}}) RETURN n")
    add(f"{tag} key rel WHERE", f"MATCH (a)-[r]->(b) WHERE r.{ID} = 1 RETURN a")
    add(f"{tag} key rel RETURN", f"MATCH (a)-[r]->(b) RETURN r.{ID}")
    add(f"{tag} key rel inline", f"MATCH (a)-[r {{{ID}: 1}}]->(b) RETURN a")
    add(f"{tag} key rel WITH", f"MATCH ()-[r]->() WITH r RETURN r.{ID} AS x")
    add(f"{tag} key rel WITH ORDER", f"MATCH ()-[r]->() WITH r RETURN r ORDER BY r.{ID}")
    add(f"{tag} key rel alias ORDER", f"MATCH ()-[r]->() RETURN r AS q ORDER BY q.{ID}")
    add(f"{tag} key rel ORDER", f"MATCH ()-[r]->() RETURN r ORDER BY r.{ID}")
    add(f"{tag} key rel map proj", f"MATCH ()-[r]->() RETURN r {{ .{ID} }} AS m")
    add(f"{tag} key rel IS NOT NULL", f"MATCH ()-[r]->() WHERE r.{ID} IS NOT NULL RETURN r")
    add(f"{tag} key rel exists()", f"MATCH ()-[r]->() WHERE exists(r.{ID}) RETURN r")
    add(f"{tag} key rel agg", f"MATCH ()-[r]->() RETURN sum(r.{ID}) AS s")
    add(f"{tag} key rel undirected", f"MATCH (a)-[r]-(b) WHERE r.{ID} = 1 RETURN a")
    add(f"{tag} key rel OPTIONAL", f"MATCH (a) OPTIONAL MATCH (a)-[r]->(b) RETURN r.{ID}")
    add(f"{tag} key rel SET from", f"MATCH (a)-[r]->(b) SET r.w = r.{ID} + 1")
    add(f"{tag} key rel SET += lit", f"MATCH (a)-[r]->(b) SET r += {{{ID}: 1}}")
    add(f"{tag} key varlen rel map", f"MATCH (a {{id:'a'}})-[*1..2 {{{ID}: 'v'}}]->(b) RETURN b")
    add(f"{tag} key varlen rel map num", f"MATCH (a {{id:'a'}})-[*1..2 {{{ID}: 1}}]->(b) RETURN b")
    add(f"{tag} key varlen rel map bool", f"MATCH (a {{id:'a'}})-[*1..2 {{{ID}: true}}]->(b) RETURN b")
    add(f"{tag} key map WITH", f"WITH {{a: 1}} AS m RETURN m.{ID} AS x")
    add(f"{tag} key UNWIND map", f"UNWIND [{{a:1}}] AS m RETURN m.{ID} AS x")
    add(f"{tag} key param map", f"RETURN $m.{ID} AS x", {"m": {"a": 1}})
    add(f"{tag} key collect", f"MATCH (n) WITH collect(n) AS ns RETURN ns[0].{ID} AS x")
    add(f"{tag} key nodes(p)", f"MATCH p=(a)-[*1..2]->(b) RETURN [x IN nodes(p) | x.{ID}] AS x")
    add(f"{tag} key rels(p)", f"MATCH p=(a)-[*1..2]->(b) RETURN [x IN relationships(p) | x.{ID}] AS x")
    add(f"{tag} key temporal", f"MATCH (n) WITH n.d AS d RETURN d.{ID} AS x")
    add(f"{tag} key CREATE", f"CREATE (n {{id:'a', {ID}: 1}})")
    add(f"{tag} key CREATE rel", f"MATCH (a {{id:'a'}}), (b {{id:'b'}}) CREATE (a)-[:R {{{ID}: 1}}]->(b)")
    add(f"{tag} key CREATE rel lit", f"CREATE (a {{id:'a'}})-[:R {{{ID}: 1}}]->(b {{id:'b'}})")
    add(f"{tag} key MERGE rel", f"MATCH (a {{id:'a'}}), (b {{id:'b'}}) MERGE (a)-[:R {{{ID}: 1}}]->(b)")
    add(f"{tag} key SET", f"MATCH (n {{id:'a'}}) SET n.{ID} = 1")
    add(f"{tag} key SET from", f"MATCH (n {{id:'a'}}) SET n.w = n.{ID} + 1")
    add(f"{tag} key SET += lit", f"MATCH (n {{id:'a'}}) SET n += {{{ID}: 1}}")
    add(f"{tag} key SET rel", f"MATCH (a)-[r]->(b) SET r.{ID} = 1")
    add(f"{tag} key REMOVE", f"MATCH (n {{id:'a'}}) REMOVE n.{ID}")
    add(f"{tag} key REMOVE rel", f"MATCH (a)-[r]->(b) REMOVE r.{ID}")
    add(f"{tag} key MERGE", f"MERGE (n {{{ID}: 'a'}})")
    add(f"{tag} key MERGE onset", f"MERGE (n {{id: 'a'}}) ON CREATE SET n.{ID} = 1")
    add(f"{tag} key WITH", f"MATCH (n) WITH n.{ID} AS v RETURN v")
    add(f"{tag} key agg", f"MATCH (n) RETURN max(n.{ID}) AS m, count(n.{ID}) AS c")
    add(f"{tag} key DISTINCT", f"MATCH (n) RETURN DISTINCT n.{ID}")
    add(f"{tag} key exists()", f"MATCH (n) WHERE exists(n.{ID}) RETURN n")
    add(f"{tag} key varlen", f"MATCH (a {{id:'a'}})-[*1..2]->(b) RETURN b.{ID}")
    add(f"{tag} key varlen where", f"MATCH (a {{id:'a'}})-[*1..2]->(b) WHERE b.{ID} = 1 RETURN b")
    add(f"{tag} key properties()", f"MATCH (n) RETURN properties(n).{ID} AS x")
    add(f"{tag} key map proj", f"MATCH (n) RETURN n {{ .{ID} }} AS x")
    add(f"{tag} key map proj alias", f"MATCH (n) RETURN n {{ {ID}: n.name }} AS x")
    add(f"{tag} key map proj literal", f"MATCH (n) RETURN n {{.name, {ID}: 1}} AS m")
    add(f"{tag} key map proj all", f"MATCH (n) RETURN n {{.*, {ID}: 1}} AS m")
    add(f"{tag} key map literal", f"RETURN {{{ID}: 1}} AS m")
    add(f"{tag} key map literal match", f"MATCH (n) RETURN {{{ID}: n.name}} AS m")
    add(f"{tag} key list comp", f"MATCH (n) RETURN [x IN n.tags | x.{ID}] AS x")
    # names: variables and aliases
    add(f"{tag} alias", f"MATCH (n) RETURN n.name AS {ID}")
    add(f"{tag} alias literal", f"RETURN 1 AS {ID}")
    add(f"{tag} alias var", f"MATCH (n) RETURN n AS {ID}")
    add(f"{tag} alias rel", f"MATCH ()-[r]->() RETURN r AS {ID}")
    add(f"{tag} alias path", f"MATCH p=(a)-[*1..2]->(b) RETURN p AS {ID}")
    add(f"{tag} alias WITH", f"MATCH (n) WITH n.name AS {ID} RETURN {ID}")
    add(f"{tag} alias WITH node", f"MATCH (n) WITH n AS {ID} RETURN {ID}.name AS x")
    add(f"{tag} alias UNWIND", f"UNWIND [1,2] AS {ID} RETURN {ID}")
    add(f"{tag} alias UNWIND param", f"UNWIND $p AS {ID} RETURN {ID} AS x", {"p": [1, 2]})
    add(f"{tag} alias ORDER", f"MATCH (n) RETURN n.name AS {ID} ORDER BY {ID}")
    add(f"{tag} alias agg", f"MATCH (n) RETURN n.name AS {ID}, count(*) AS c")
    add(f"{tag} alias count", f"MATCH (n) RETURN count(*) AS {ID}")
    add(f"{tag} alias collect", f"MATCH (n) RETURN collect(n.name) AS {ID}")
    add(f"{tag} alias DISTINCT", f"MATCH (n) RETURN DISTINCT n.name AS {ID}")
    add(f"{tag} alias UNION", f"RETURN 1 AS {ID} UNION RETURN 2 AS {ID}")
    add(f"{tag} var", f"MATCH ({ID}) RETURN {ID}.name")
    add(f"{tag} var bare", f"MATCH ({ID}) RETURN {ID}")
    add(f"{tag} var rel", f"MATCH (a)-[{ID}]->(b) RETURN {ID}")
    add(f"{tag} var rel prop", f"MATCH (a)-[{ID}]->(b) RETURN {ID}.w AS w")
    add(f"{tag} var path", f"MATCH {ID}=(a)-[*1..2]->(b) RETURN {ID}")
    add(f"{tag} var varlen rel", f"MATCH (a)-[{ID}*1..2]->(b) RETURN {ID}")
    add(f"{tag} var OPTIONAL", f"MATCH (a) OPTIONAL MATCH (a)-->({ID}) RETURN {ID}")
    add(f"{tag} var WHERE", f"MATCH ({ID}) WHERE {ID}.name = 'x' RETURN {ID}.name AS x")
    add(f"{tag} var labels()", f"MATCH ({ID}) RETURN labels({ID}) AS x")
    add(f"{tag} var count", f"MATCH ({ID}) RETURN count({ID}) AS x")
    add(f"{tag} var list comp", f"RETURN [{ID} IN [1,2] | {ID} + 1] AS x")
    add(f"{tag} var list pred", f"MATCH (n) WHERE any({ID} IN n.tags WHERE {ID} = 'a') RETURN n")
    add(f"{tag} var reduce acc", f"RETURN reduce({ID} = 0, x IN [1,2] | {ID} + x) AS x")
    add(f"{tag} var reduce item", f"RETURN reduce(s = 0, {ID} IN [1,2] | s + {ID}) AS x")
    add(f"{tag} var FOREACH", f"MATCH (n {{id:'a'}}) FOREACH ({ID} IN [1] | SET n.w = {ID})")
    add(f"{tag} var pattern compr path", f"MATCH (a) RETURN [{ID}=(a)-->(b) | length({ID})] AS x")
    add(f"{tag} var MERGE path", f"MERGE {ID}=(a {{id:'a'}})-[:R]->(b {{id:'b'}}) RETURN {ID}")
    add(f"{tag} var CALL import", f"MATCH ({ID}) CALL {{ WITH {ID} RETURN {ID}.name AS x }} RETURN x")
    add(f"{tag} var CREATE", f"CREATE ({ID} {{id:'a'}}) RETURN {ID}")
    add(f"{tag} var DELETE", f"MATCH ({ID} {{id:'a'}}) DETACH DELETE {ID}")
    add(f"{tag} var SET", f"MATCH ({ID} {{id:'a'}}) SET {ID}.w = 1")
    add(f"{tag} var map proj", f"MATCH ({ID}) RETURN {ID} {{.name}} AS x")
    add(f"{tag} var YIELD alias", f"CALL db.labels() YIELD label AS {ID} RETURN {ID}")
    add(f"{tag} param name", f"MATCH (n) WHERE n.name = ${ID} RETURN n", {ID.strip("`"): "v"})
    add(f"{tag} graph USE", f"USE {ID} MATCH (n) RETURN n")
    add(f"{tag} proc", f"CALL {ID}() YIELD x RETURN x")
# --- parameters whose keys or values reach the SQL ------------------------------------------------------------------
add("param keys()", "RETURN keys($m) AS k", {"m": {P: 1}})
add("param keys() match", "MATCH (n) RETURN keys($m) AS k", {"m": {P: 1}})
add("param size(keys())", "RETURN size(keys($m)) AS k", {"m": {P: 1}})
add("param UNWIND keys", "UNWIND $rows AS row RETURN keys(row) AS k", {"rows": [{P: P}]})
add("param UNWIND row prop", "UNWIND $rows AS row RETURN row.`ZQ'X` AS k", {"rows": [{P: P}]})
add("param rel prop varlen", "MATCH (a {id:'a'})-[*1..2 {w: $p}]->(b) RETURN b", {"p": P})
add("param rel prop varlen list", "MATCH (a {id:'a'})-[*1..2 {w: $p}]->(b) RETURN b", {"p": [P, 1]})
add("param list concat", "RETURN $a + $b AS x", {"a": [P], "b": [P]})
add("param list contains", "MATCH (n) WHERE n.name IN $a RETURN n", {"a": [P, 1, None]})
add("param in list UNWIND", "UNWIND $a AS x WITH x WHERE x IN $a RETURN x", {"a": [P, [P]]})
add("param nested list UNWIND", "UNWIND $a AS x RETURN x", {"a": [[P], [P]]})
add("param map UNWIND", "UNWIND $a AS x RETURN x.k", {"a": [{"k": [P]}]})
add("param in collect", "MATCH (n) WITH collect(n.name) AS c RETURN $p IN c AS b", {"p": P})
add("param point", "RETURN point($m) AS p", {"m": {"x": 1, "y": 2, "crs": P}})
add("param date", "RETURN date($p) AS d", {"p": P})
add("param datetime", "RETURN datetime($p) AS d", {"p": P})
add("param duration", "RETURN duration($p) AS d", {"p": P})
add("param date map", "RETURN date($m) AS d", {"m": {"year": 2020, "timezone": P}})
add("param datetime map tz", "RETURN datetime({year: 2020, timezone: $p}) AS d", {"p": P})
add("param subscript key", "MATCH (n) RETURN n[$k] AS x", {"k": P})
add("param subscript key where", "MATCH (n) WHERE n[$k] = 1 RETURN n", {"k": P})
add("param map subscript", "RETURN $m[$k] AS x", {"m": {P: 1}, "k": P})
add("lit subscript key", "MATCH (n) RETURN n['ZQ\\'X'] AS x")
add("lit subscript key where", "MATCH (n) WHERE n['ZQ\\'X'] = 1 RETURN n")
add("param graph", "USE GRAPH $g MATCH (n) RETURN n", {"g": P})
add("lit USE GRAPH", f"USE GRAPH {L} MATCH (n) RETURN n")
add("param anchored id", "MATCH (a {id:$p})-[:T]->(b) RETURN b", {"p": P})
add("lit anchored id", f"MATCH (a {{id:{L}}})-[:T]->(b) RETURN b")
add("param anchored id rel var", "MATCH (a {id:$p})-[r:T]->(b) RETURN b, r", {"p": P})
add("param CALL lateral IN", "MATCH (n) CALL { WITH n MATCH (n)-->(m) WHERE m.name IN $p RETURN m.name AS x, m.id AS y }"
    " RETURN n, x", {"p": [P]})
add("param CALL lateral eq list", "MATCH (n) CALL { WITH n MATCH (n)-->(m) WHERE m.name = $p RETURN m.name AS x,"
    " count(*) AS c } RETURN x, c", {"p": [P]})
add("param CALL lateral rel", "MATCH (n) CALL { WITH n MATCH (n)-[r]->(m) WHERE r.w IN $p RETURN m AS x, r AS y }"
    " RETURN x", {"p": [P]})
# --- temporal literals ----------------------------------------------------------------------------------------------
add("lit datetime map tz", f"RETURN datetime({{year: 2020, timezone: {L}}}) AS d")
add("lit time map tz", f"RETURN time({{hour: 1, timezone: {L}}}) AS d")
add("lit datetime str zone", r"RETURN datetime('2020-01-01T00:00[ZQ\'X]') AS d")
add("lit time str zone", r"RETURN time('10:00[ZQ\'X]') AS d")
add("lit datetime str zone offset", r"RETURN datetime('2020-01-01T00:00+01:00[ZQ\'X]') AS d")
# --- procedures -----------------------------------------------------------------------------------------------------
add("proc vector lit", f"CALL ivg.vector.search({L}, 'emb', [0.1,0.2], 5) YIELD node, score RETURN node")
add("proc vector param", "CALL ivg.vector.search($p, $p, $v, 5) YIELD node, score RETURN node",
    {"p": P, "v": [0.1, 0.2]})
add("proc vector param str vec", "CALL ivg.vector.search('L', 'emb', $v, 5) YIELD node, score RETURN node", {"v": P})
add("proc vector lit prop", f"CALL ivg.vector.search('L', {L}, [0.1], 5) YIELD node, score RETURN node")
add("proc vector lit opts",
    f"CALL ivg.vector.search('L', 'emb', [0.1], 5, {{embedding_config: {L}}}) YIELD node, score RETURN node")
add("proc vector lit text",
    f"CALL ivg.vector.search('L', 'emb', {L}, 5, {{embedding_config: 'c'}}) YIELD node, score RETURN node")
add("proc fulltext lit", f"CALL db.index.fulltext.queryNodes({L}, {L}) YIELD node RETURN node")
add("proc fulltext param", "CALL db.index.fulltext.queryNodes($p, $p) YIELD node RETURN node", {"p": P})
add("proc ppr lit", f"CALL ivg.ppr([{L}], 0.85, 10) YIELD node, score RETURN node")
add("proc ppr param", "CALL ivg.ppr($p, 0.85, 10) YIELD node, score RETURN node", {"p": [P]})
add("proc bm25 lit", f"CALL ivg.bm25.search({L}, {L}, 5) YIELD node, score RETURN node")
add("proc bm25 param", "CALL ivg.bm25.search($p, $p, 5) YIELD node, score RETURN node", {"p": P})
add("proc neighbors", f"CALL ivg.neighbors([{L}], {L}, 'out') YIELD node RETURN node")
add("proc neighbors param", "CALL ivg.neighbors($p, $q, 'out') YIELD node RETURN node", {"p": [P], "q": P})
add("proc shortest weighted", f"CALL ivg.shortestPath.weighted({L}, {L}, 'w', 5) YIELD path RETURN path")
add("proc shortest weighted param", "CALL ivg.shortestPath.weighted($p, $p, $p, 5) YIELD path RETURN path", {"p": P})
add("proc ivf lit", f"CALL ivg.ivf.search({L}, [0.1], 5) YIELD node, score RETURN node")
add("proc ivf param", "CALL ivg.ivf.search($p, [0.1], 5) YIELD node, score RETURN node", {"p": P})
for proc in ["degreeCentrality", "betweenness", "kcore", "pagerank", "closeness", "eigenvector"]:
    add(f"proc {proc}", f"CALL ivg.{proc}({L}) YIELD node RETURN node")
add("proc db.labels where", f"CALL db.labels() YIELD label WHERE label = {L} RETURN label")


# --- the lexer ------------------------------------------------------------------------------------------------------
MARKER = re.compile(r"ZQ(['\" ])X")


def lexical_states(sql: str) -> list:
    """One state per character: c (code), s (inside '...'), d (inside "..."). A doubled quote stays inside."""
    out, mode, i, n = [], "c", 0, len(sql)
    while i < n:
        ch = sql[i]
        if mode == "c":
            out.append("c")
            mode = {"'": "s", '"': "d"}.get(ch, "c")
            i += 1
            continue
        quote = "'" if mode == "s" else '"'
        if ch == quote and i + 1 < n and sql[i + 1] == quote:
            out += [mode, mode]
            i += 2
            continue
        if ch == quote:
            mode = "c"
        out.append(mode)
        i += 1
    return out


def breakouts(sql: str) -> list:
    """Each marker whose ends sit in different states, or that sits in code with a character no identifier holds."""
    states = lexical_states(sql)
    found = []
    for m in MARKER.finditer(sql):
        z, x = m.start(), m.end() - 1
        if states[z] != states[x] or states[z] == "c":
            found.append(f"[{states[z]}->{states[x]}] " + sql[max(0, z - 100) : x + 40].replace("\n", " "))
    return found


def translated_sql(query: str, params) -> str | None:
    """The statement(s) as one text; None when the translator refuses the query (no SQL, nothing to break)."""
    try:
        result = translate_to_sql(parse_query(query), dict(params or {}))
    except Exception:  # a refusal of any kind builds no statement; the battery measures statements only
        return None
    return result.sql if isinstance(result.sql, str) else "\n;\n".join(result.sql)


def battery() -> dict:
    out = {}
    for tag, query, params in CASES:
        sql = translated_sql(query, params)
        if sql is not None:
            found = breakouts(sql)
            if found:
                out[tag] = (query, params, found[:2])
    return out


# --- the tests ------------------------------------------------------------------------------------------------------
def test_the_lexer_sees_a_breakout_and_an_escaped_marker():
    """The battery is not blind: an undoubled quote, an unquoted name and a quoted one read as they should."""
    assert breakouts("SELECT 'a' FROM t WHERE x = 'ZQ'X'")
    assert breakouts("SELECT 1 AS ZQ'X FROM t")
    assert breakouts("SELECT 1 AS ZQ X FROM t")
    assert breakouts("SELECT JSON_VALUE(q, '$.ZQ\"X') AS ZQ\"X")
    assert not breakouts("SELECT 'ZQ''X', \"ZQ'X\", \"ZQ X\", '$.ZQ\"X', \"ZQ\"\"X\" FROM t")


def test_the_battery_has_its_cases():
    assert len(CASES) > 450
    assert len({tag for tag, _, _ in CASES}) == len(CASES), "tags are unique"


@pytest.mark.parametrize("edgescan", ["0", "1"])
def test_no_text_or_value_changes_the_statement(monkeypatch, edgescan):
    """0 breakouts (47 on the release-4.6.0 head). IVG_EDGESCAN=1 takes the anchored edge-scan route (H10)."""
    monkeypatch.setenv("IVG_EDGESCAN", edgescan)
    found = battery()
    assert not found, f"{len(found)} breakouts:\n" + "\n".join(
        f"{tag}: {q} params={p}\n    " + "\n    ".join(hits) for tag, (q, p, hits) in found.items()
    )


# --- names: one helper, and the names the query writes ------------------------------------------------------------
@pytest.mark.parametrize("name", ["n", "_x", "größe", "名前", "n_1"])
def test_a_plain_name_goes_out_as_it_is(name):
    assert _safe_alias(name) == name


@pytest.mark.parametrize("name", ["count", "value", "type"])
def test_a_reserved_word_is_quoted(name):
    assert _safe_alias(name) == f'"{name}"'


@pytest.mark.parametrize("name", ["a b", "ZQ'X", 'ZQ"X', "a FROM t --", "a.b", "1a", "a-b", "a;b", ""])
def test_any_other_name_is_a_delimited_identifier_or_nothing(name):
    """`_safe_alias` never answers a name that is not an identifier as it is: it delimits it (quotes doubled)."""
    out = _safe_alias(name)
    if not name:
        assert out == ""
        return
    assert out == '"' + name.replace('"', '""') + '"'
    states = lexical_states(f"{out} x")
    assert set(states[1 : len(out) - 1]) == {"d"} and states[len(out) :] == ["c", "c"], "one delimited identifier"


NAMED = [
    "MATCH (n) RETURN n.name AS {x}",
    "RETURN 1 AS {x}",
    "MATCH (n) WITH n AS {x} RETURN {x}",
    "UNWIND [1] AS {x} RETURN {x}",
    "MATCH ({x}) RETURN {x}",
    "MATCH ()-[{x}]->() RETURN {x}",
    "MATCH {x}=()-->() RETURN {x}",
    "RETURN [{x} IN [1] | {x}] AS y",
    "RETURN reduce({x} = 0, y IN [1] | {x} + y) AS y",
    "MATCH (n) WHERE all({x} IN [1] WHERE {x} > 0) RETURN n",
    "MATCH (n {{id:'a'}}) FOREACH ({x} IN [1] | SET n.w = {x})",
    "CALL db.labels() YIELD label AS {x} RETURN {x}",
    "MATCH (n) WHERE n.name = ${x} RETURN n",
]


@pytest.mark.parametrize("template", NAMED)
@pytest.mark.parametrize("name", ["a FROM t --", "ZQ'X", 'a"b', "a.b", "a b"])
def test_a_name_that_is_not_an_identifier_is_refused(template, name):
    """A variable, alias or parameter name the query writes in backticks is a SQL identifier in the statement; one that
    is not an identifier is refused before any SQL is built, so no site can paste it (H9)."""
    query = template.format(x=f"`{name}`")
    try:
        tree = parse_query(query)
    except CypherParseError:
        return  # refused before the translator (a backtick parameter name)
    with pytest.raises(ValueError, match=r"UnsupportedName"):
        translate_to_sql(tree, {name: "v"})


@pytest.mark.parametrize("template", [t for t in NAMED if "$" not in t])  # the parser takes no backtick parameter
def test_a_backtick_name_that_is_an_identifier_is_not_refused(template):
    """The check refuses no identifier. (Two of these shapes, a YIELD alias and FOREACH ... SET, raise
    UndefinedVariable on 4.5.1 too: not the check.)"""
    try:
        translate_to_sql(parse_query(template.format(x="`x_1`")), {})
    except SyntaxError as e:
        assert "UnsupportedName" not in str(e), e
        assert "UndefinedVariable" in str(e) or "Undefined variable" in str(e), e


def test_every_name_field_of_the_tree_is_checked():
    """Structural: each dataclass field of the Cypher tree that holds a variable, alias or name is one the check reads.

    A new tree field that names a variable must be added to the check (translator `_NAME_FIELDS`), or this fails."""
    name_like = re.compile(r"(^|_)(variable|variables|alias|accumulator|yield_items)$")
    fields = {
        f.name
        for cls in vars(ast).values()
        if isinstance(cls, type) and dataclasses.is_dataclass(cls)
        for f in dataclasses.fields(cls)
        if name_like.search(f.name)
    }
    assert fields <= _NAME_FIELDS, sorted(fields - _NAME_FIELDS)


# --- the property-key auto alias (`RETURN n.key`) -------------------------------------------------------------------
@pytest.mark.parametrize("key", ["ZQ'X", 'ZQ"X', "ZQ X"])
@pytest.mark.parametrize("var", ["n", "r"])
def test_an_unaliased_property_column_is_named_by_its_text(key, var):
    """`RETURN n.\\`k\\`` names its SQL column after the key with what an identifier cannot hold replaced, and maps it
    back to the Cypher text, so the column is still called by what the query wrote."""
    pattern = "(n)" if var == "n" else "()-[r]->()"
    result = translate_to_sql(parse_query(f"MATCH {pattern} RETURN {var}.`{key}`"), {})
    assert not breakouts(result.sql), result.sql
    assert f"{var}.`{key}`" in (result.column_name_map or {}).values(), result.column_name_map


def test_every_json_path_key_goes_through_the_helper():
    """Structural: in the translator's source, each `$.<key>` path an f-string builds takes its key from
    `_jsonpath_key` (IVG's JSON_VALUE-style functions) or `_jsontable_key` (IRIS's JSON_TABLE PATH). Nine places
    pasted the key as written (H2-H5)."""
    source = Path(translator_module.__file__).read_text()
    raw = re.findall(r"\$\.(?:props\.)?\{(?!_jsonpath_key\(|_jsontable_key\()[^}]*\}", source)
    assert raw == [], raw


@pytest.mark.parametrize("key", ['a"b', "a\\b"])
def test_a_jsontable_key_it_cannot_spell_is_refused(key):
    with pytest.raises(ValueError, match="UnsupportedName"):
        _jsontable_key(key)


def test_a_jsontable_key_is_an_identifier_or_one_quoted_member():
    assert _jsontable_key("k'e y.z") == "\"k''e y.z\""
    assert _jsontable_key("plain_1") == "plain_1"


@pytest.mark.parametrize("value", [["ZQ'X"], {"k": "ZQ'X"}, ("ZQ'X",)])
def test_a_lateral_subquery_value_that_is_not_a_scalar_is_one_literal(value):
    """`str()` of a list is Python's repr, its quote undoubled; a correlated CALL's inlined value is JSON in a literal."""
    out = _subquery_lateral_inline_param(value)
    assert not breakouts(f"SELECT {out}") and out.startswith("'") and out.endswith("'"), out


@pytest.mark.parametrize("query", ["MATCH (n) WHERE n.name = $1 RETURN n", "UNWIND $2 AS x RETURN x",
                                   "MATCH (a {id: $1})-[:T]->(b) RETURN b"])
def test_a_numbered_parameter_is_not_refused(query):
    """`$1` is a parameter name Cypher writes without backticks (TCK MatchWhere2 [2], WithWhere2 [2]): its value is
    bound or escaped, and its name is not an identifier the SQL holds."""
    sql = translated_sql(query, {"1": "ZQ'X", "2": ["ZQ'X"]})
    assert sql is not None and not breakouts(sql), sql
