"""CY_SORT_KEY: no regex on the per-row path, and an impossible date orders as a string.

`ORDER BY` calls CY_SORT_KEY once per row per sort column. Its datetime branch ran two
`$Match` regexes per call; over SNB SF0.1's 425k creationDate values the function took
2.1 s, with character tests 0.13 s (IC9: 3.4 s of a 4.5 s query). And `$ZDateH` raised
<ILLEGAL VALUE> on a string shaped like a date that is not one (`2021-02-30T10:00`),
which failed the whole statement; execute_cypher reports that as zero rows.
"""

import re

from iris_vector_graph.schema import GraphSchema


def _sort_key_ddl():
    ddl = [s for s in GraphSchema.get_procedures_sql_list() if "FUNCTION SQLUser.CY_SORT_KEY(" in s]
    assert len(ddl) == 1
    return ddl[0]


def test_no_regex_on_the_per_row_path():
    assert "$Match(" not in _sort_key_ddl()


def test_an_impossible_date_falls_back_to_string_order():
    ddl = _sort_key_ddl()
    call = re.search(r"\$ZDateH\(([^()]|\([^()]*\))*\)", ddl).group(0)
    assert call.endswith(', , "")'), call  # erropt: "" instead of <ILLEGAL VALUE>
    assert 'If h = "" { Quit "l" _ v }' in ddl


def test_the_body_survives_the_dbapi_ddl_tokenizer():
    # an odd apostrophe count or a bare ? reads as an open literal / a parameter: ")QC" at Prepare
    body = _sort_key_ddl().split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert body.count("'") % 2 == 0
    assert "?" not in body
