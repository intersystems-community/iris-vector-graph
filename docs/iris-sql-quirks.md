<!-- markdownlint-disable MD013 -->

# IRIS SQL behaviours IVG works around

For contributors changing the Cypher translator or IVG's SQL, and for anyone writing their own SQL against IVG's
tables. Each entry says what IRIS does, what IVG does about it, and where. Most were found during the openCypher TCK
work (spec 201) and were measured on IRIS 2025.1 and later.

Examples name tables in the default IVG Schema, `Graph_KG`. Use yours if the namespace records another.

## 1. `JSON_TABLE` on a nested array's element expands the element

A path that selects one element of an outer array, when that element is itself an array, yields one row per item of
the inner array, not one row holding it:

```sql
SELECT x FROM JSON_TABLE('[[1,2,3]]', '$[0]' COLUMNS(x VARCHAR(4096) PATH '$')) jt
```

<!-- doc-test: output -->

```text
1
2
3
```

The translator reads a subscript such as `list[0]` with IVG's `JSON_VALUE` instead (entry 4), so `x IN list[0]` tests
against the inner array and not its items one row at a time (commit `b44b686`).

## 2. `IN` compares a string with a number by converting it

```sql
SELECT CASE WHEN ('1' IN (1, 2)) THEN 1 ELSE 0 END
```

<!-- doc-test: output -->

```text
1
```

Cypher does not convert: `'1' IN [1, 2]` is false. The translator drops the list items whose type cannot equal the
left side before it writes the `IN`, and writes `(1=0)` when none is left (commit `96716a2`). The same conversion
happens with bound parameters: `1 IN (?, ?)` with `[2, '1']` is true.

## 3. `FETCH FIRST` with a multi-table join crashes some IRIS builds

On the IRIS AI builds (2026.2 and 2026.3, `%qaqpre`), a `SELECT` with a join over VARCHAR keys and
`FETCH FIRST n ROWS ONLY` ends the server process: the client sees the connection drop, with no SQL error. The same
statement with `TOP n` runs.

<!-- doc-test: skip reason="crashes the IRIS process that runs it on the 2026.2 and 2026.3 AI builds" -->

```sql
SELECT n.node_id FROM Graph_KG.nodes n JOIN Graph_KG.rdf_labels l ON l.s = n.node_id FETCH FIRST 10 ROWS ONLY
```

The translator writes `TOP n` for every `LIMIT` on a statement with a join, and `FETCH FIRST` only on a single table
(2.4.3). A limit therefore reaches SQL in three spellings (`FETCH FIRST`, `TOP` and a bare `LIMIT`), and
`_extract_sql_row_limit` in `iris_vector_graph/_engine/query.py` reads all three: a change to how limits are written
must keep it reading them.

## 4. IRIS 2026.1 has no `JSON_VALUE`; IVG calls its own

IRIS 2026.3 has a built-in `JSON_VALUE`; IRIS 2026.1 has none, and an unqualified `JSON_VALUE(...)` there fails with
SQLCODE -359 (function not found). IVG creates its own in the IVG Schema, `Graph_KG.JSON_VALUE(j, p)`, and the
translator always calls it qualified with the schema, for relationship properties and for list subscripts. It reads
one top-level member, a key of a JSON object (`$.name`) or an element of an array (`$[0]`), and answers an object or
an array as its JSON text:

```sql
SELECT Graph_KG.JSON_VALUE('[[1,2],[3,4]]', '$[0]')
```

<!-- doc-test: output -->

```text
[1,2]
```

That text can be read again with `JSON_TABLE(..., '$[*]' ...)`, which is how the translator reads one element of a
nested list. A deeper path such as `$.a.b` answers NULL.

## 5. A CTE's `ORDER BY` needs `TOP`

IRIS refuses an `ORDER BY` inside a common table expression unless the CTE also has `TOP`:

<!-- doc-test: skip reason="shows a statement IRIS refuses" -->

```sql
WITH ranked AS (SELECT node_id FROM Graph_KG.nodes ORDER BY node_id) SELECT * FROM ranked
```

The translator puts the order on the outer query, or gives the CTE `TOP 9223372036854775807` so that it may order.

## 6. A `?` inside `JSON_TABLE`'s source is not a parameter

`JSON_TABLE(Graph_KG.kg_PPR(?, ...), ...)` fails to prepare: IRIS does not treat a `?` inside the source argument as a
parameter marker. The translator writes those arguments as escaped literals instead (`_sql_arg` in
`iris_vector_graph/cypher/translator.py`), so for these procedure calls injection safety comes from the escaping, not
from binding. Any new procedure called from a `JSON_TABLE` needs the same.

## 7. Property values are text

`rdf_props.val` is `VARCHAR(64000)`: a property value is stored as its text (`prop_text` in
`iris_vector_graph/prop_values.py`: `true`/`false` for booleans, JSON for lists and maps), except a number, which every
writer stores as an IRIS number in the same column (the Python writers since 4.6.4, `prop_value`). The translator wraps a
property in `CAST(... AS DOUBLE)` where Cypher does arithmetic on it, and leaves string comparisons uncast. SQL of
your own over `rdf_props` should cast the same way rather than rely on how IRIS compares two strings: IRIS compares
two literals that look like numbers as numbers (`'10' < '9'` is false), so a test on literals says nothing about a
column's collation.

```sql
SELECT CASE WHEN '10' < '9' THEN 1 ELSE 0 END, CASE WHEN CAST('10' AS DOUBLE) < CAST('9' AS DOUBLE) THEN 1 ELSE 0 END
```

<!-- doc-test: output -->

```text
0 | 0
```

## 8. `CAST` from a string gives NaN and infinity

`CAST('NaN' AS DOUBLE)`, `CAST('Infinity' AS DOUBLE)` and `CAST('-Infinity' AS DOUBLE)` return those values, which
is how the translator answers Cypher's `0.0 / 0.0` (NaN) and a float division by zero (commit `76f96d8`).

## 9. Keywords as map keys (the parser, not IRIS)

Cypher allows keywords as map keys (`{end: 1}`, `{type: 'x'}`), but the lexer reads `end` as the `END` keyword. The
parser accepts any token as a key when the next token is `:` (commit `96716a2`). Recorded here because it was found
beside entry 2.

## 10. Driver crashes on some query shapes

`intersystems-iris` 5.3.3 ends the Python process (a native crash, no exception) on some of the multi-join `SELECT`s
the translator writes: one shape (a relationship variable with `LIMIT`) on small fixtures, one (joins, `EXISTS` and a
`LIKE` concatenation) only above about 100,000 rows. The translator is not at fault; the tests that cover these shapes
run them in a subprocess so a crash fails one test rather than the suite.
