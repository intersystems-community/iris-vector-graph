import re

def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL content into individual statements robustly."""
    statements = []
    current_stmt = []
    in_quote = False
    quote_char = None
    in_comment = False
    comment_type = None  # '--' or '/*'
    in_procedure = False
    brace_depth = 0
    
    i = 0
    while i < len(sql):
        ch = sql[i]
        next_ch = sql[i+1] if i+1 < len(sql) else ""
        
        # Handle quotes
        if not in_comment:
            if ch in ("'", '"') and (i == 0 or sql[i-1] != "\\"):
                if not in_quote:
                    in_quote = True
                    quote_char = ch
                elif quote_char == ch:
                    # Check for escaped quote ('')
                    if ch == "'" and next_ch == "'":
                        current_stmt.append("''")
                        i += 2
                        continue
                    in_quote = False
                    quote_char = None
        
        if not in_quote:
            # Handle comments
            if not in_comment:
                if ch == "-" and next_ch == "-":
                    in_comment = True
                    comment_type = "--"
                    i += 2
                    continue
                elif ch == "/" and next_ch == "*":
                    in_comment = True
                    comment_type = "/*"
                    i += 2
                    continue
            else:
                if comment_type == "--" and ch == "\n":
                    in_comment = False
                    comment_type = None
                elif comment_type == "/*" and ch == "*" and next_ch == "/":
                    in_comment = False
                    comment_type = None
                    i += 2
                    continue
                i += 1
                continue

            # Handle Procedure blocks
            upper_sql_slice = sql[i:i+40].upper()
            is_proc_start = (
                "CREATE PROCEDURE" in upper_sql_slice or 
                "CREATE OR REPLACE PROCEDURE" in upper_sql_slice or
                "CREATE FUNCTION" in upper_sql_slice or
                "CREATE OR REPLACE FUNCTION" in upper_sql_slice
            )
            if not in_procedure and is_proc_start:
                in_procedure = True
            
            if in_procedure:
                if ch == "{":
                    brace_depth += 1
                elif ch == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        current_stmt.append(ch)
                        statements.append("".join(current_stmt).strip())
                        current_stmt = []
                        in_procedure = False
                        i += 1
                        continue
                elif sql[i:i+4].upper() == "END;":
                    current_stmt.append("END;")
                    statements.append("".join(current_stmt).strip())
                    current_stmt = []
                    in_procedure = False
                    i += 4
                    continue
            
            # Split on semicolon
            if ch == ";" and not in_procedure:
                statements.append("".join(current_stmt).strip())
                current_stmt = []
                i += 1
                continue
                
        current_stmt.append(ch)
        i += 1
        
    if current_stmt:
        stmt = "".join(current_stmt).strip()
        if stmt:
            statements.append(stmt)
            
    return [s for s in statements if s]


# Bumped when the rerun text itself has gone stale on some connection; each value is
# one more cached statement in the namespace, and a new one is needed only after a
# `%BuildIndices` has run since that connection last used the current one.
_decode_epoch = 0


def is_list_error(exc: BaseException) -> bool:
    return "<list error>" in str(exc).lower()


def execute_decoded(cursor, sql: str, params=None):
    """`cursor.execute`, with a `<LIST ERROR>` turned back into the error IRIS meant.

    A connection that executed a statement before another connection ran
    `%BuildIndices` on its class gets `<LIST ERROR> Incorrect list format ... type
    detected : 0` from then on wherever that statement should have failed with its
    SQLCODE (-119, -121). It is keyed on the statement text and nothing on the
    connection clears it; a text the connection has never executed reports
    correctly. So the statement is re-run once under such a text (the same SQL with a
    comment), and a second time under a fresh one if that text has gone stale too.
    The statement that raised changed nothing, so re-running it is safe. If every
    attempt answers `<LIST ERROR>` the original exception is raised.
    """
    global _decode_epoch
    try:
        return cursor.execute(sql, params)
    except Exception as exc:
        if not is_list_error(exc):
            raise
        original = exc
    for _ in range(2):
        epoch = _decode_epoch
        try:
            # On its own line, so a trailing `--` comment cannot swallow it.
            return cursor.execute(f"{sql}\n/* ivg-decode {epoch} */", params)
        except Exception as exc:
            if not is_list_error(exc):
                raise
            _decode_epoch = max(_decode_epoch, epoch + 1)
    raise original
