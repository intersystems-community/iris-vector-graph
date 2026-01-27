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
            upper_sql_slice = sql[i:i+20].upper()
            if not in_procedure and (upper_sql_slice.startswith("CREATE PROCEDURE") or upper_sql_slice.startswith("CREATE FUNCTION")):
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
