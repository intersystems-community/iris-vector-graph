"""behave step definitions: When … query execution steps."""
try:
    from behave import when, step
except ImportError:
    def when(s):  # noqa: E306
        def _d(f): return f
        return _d
    def step(s):  # noqa: E306
        def _d(f): return f
        return _d

from tests.tck.steps.comparison import TCKValue
from tests.tck.steps.graph_setup import _inject_label


@when("executing query:")
def step_executing_query(context, query=None):
    if query is None:
        query = context.text
    query = query.strip()
    injected = _inject_match_scope(
        _inject_label(query, context.scenario_label, inject_anonymous=False),
        context.scenario_label,
    )
    params = getattr(context, "params", {}) or {}
    procedures = getattr(context, "_tck_procedures", None) or {}
    context.last_error = None
    try:
        context.last_result = context.engine.execute_cypher(
            injected, params, procedures=procedures
        )
    except Exception as exc:
        context.last_result = None
        context.last_error = exc
    context.params = {}


@when("executing control query:")
def step_control_query(context, query=None):
    if query is None:
        query = context.text
    query = query.strip()
    injected = _inject_match_scope(
        _inject_label(query, context.scenario_label, inject_anonymous=False),
        context.scenario_label,
    )
    params = getattr(context, "params", {}) or {}
    procedures = getattr(context, "_tck_procedures", None) or {}
    context.last_error = None
    try:
        context.last_result = context.engine.execute_cypher(
            injected, params, procedures=procedures
        )
    except Exception as exc:
        context.last_result = None
        context.last_error = exc
    context.params = {}


@step("parameters are:")
def step_parameters(context, table=None):
    if table is None:
        table = context.table
    params = {}
    # TCK table format: first column = param name, second column = value.
    # The table header row itself is row 0 (behave treats it as headings).
    # First, treat the heading row as a name=value pair.
    headings = table.headings
    if len(headings) == 2:
        params[headings[0]] = TCKValue.parse(headings[1]).python
    for row in table.rows:
        values = list(row)
        if len(values) >= 2:
            params[values[0]] = TCKValue.parse(values[1]).python
    context.params = params


def _inject_match_scope(query: str, label: str) -> str:
    """
    In MATCH clauses, add label filter so reads see only this scenario's nodes.
    Skips anonymous nodes () and already-labelled nodes.

    Also tracks variables already bound (from prior MATCH/CREATE/WITH) and
    does NOT re-inject labels into already-bound variables, which would cause
    VariableAlreadyBound errors in the translator.
    """
    import re

    lines = query.split('\n')
    result_lines = []
    in_match = False
    bound_vars = set()  # Track variables bound in MATCH/WITH/CREATE clauses

    for line in lines:
        stripped = line.strip().upper()
        first_word = stripped.split()[0] if stripped.split() else ''

        # Track when we exit MATCH to update bound variables
        if first_word in ('WHERE', 'WITH', 'RETURN', 'CREATE', 'MERGE',
                            'SET', 'DELETE', 'REMOVE', 'UNWIND', 'CALL',
                            'UNION', 'ORDER', 'SKIP', 'LIMIT'):
            in_match = False

            # Extract variables from WITH clause.
            # "WITH a, b AS c" binds: a (passthrough), c (alias). b is expr, not a var.
            # "WITH n.prop AS p" binds: p only.
            if first_word == 'WITH':
                # Collect AS aliases: "expr AS alias" → bind alias
                has_alias = set()
                for m in re.finditer(r'\bAS\s+([A-Za-z_][A-Za-z0-9_]*)', line, re.IGNORECASE):
                    bound_vars.add(m.group(1))
                    has_alias.add(m.group(1).upper())
                # Collect bare variable passthroughs: any lone identifier in the WITH list
                # that is not part of an "expr AS alias" or a property access.
                # Strategy: strip AS-aliased expressions, then find bare identifiers.
                without_aliases = re.sub(r'[^,]+\bAS\b[^,]+', '', line, flags=re.IGNORECASE)
                for m in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', without_aliases):
                    var = m.group(1)
                    if var.upper() not in ('WITH', 'AS', 'DISTINCT', 'ORDER', 'BY', 'WHERE'):
                        bound_vars.add(var)

        if first_word in ('MATCH', 'OPTIONAL'):
            in_match = True

        if in_match:
            line = _inject_match_label_in_line(line, label, skip_vars=bound_vars)
            # Extract newly-bound variables from this MATCH line
            for m in re.finditer(r'\(([A-Za-z_][A-Za-z0-9_]*)', line):
                bound_vars.add(m.group(1))

        result_lines.append(line)

    return '\n'.join(result_lines)


def _inject_match_label_in_line(line: str, label: str, skip_vars: set = None) -> str:
    """Add :<label> to named node patterns in MATCH lines. Anonymous nodes are not scoped.

    skip_vars: set of variable names that are already bound (e.g., from prior MATCH/WITH)
               and should NOT be re-labeled.
    """
    import re

    if skip_vars is None:
        skip_vars = set()

    def replacer(m):
        inner = m.group(1)
        # skip empty nodes ()
        if not inner.strip():
            return m.group(0)
        # skip already labelled
        if label in inner:
            return m.group(0)
        # Extract variable name from inner (before first : or {)
        # to check if it's already bound
        inner_stripped = inner.strip()
        var_name = inner_stripped.split(':')[0].split('{')[0].strip() if inner_stripped else ''
        # If var_name contains '.' or is a complex expression (function arg), skip.
        # Node patterns never have dots in the variable portion.
        if '.' in var_name or ' ' in var_name or var_name.startswith('%'):
            return m.group(0)
        if var_name and var_name in skip_vars:
            return m.group(0)  # Don't add label to already-bound variable
        # Insert label BEFORE any property map {…} so the result is valid Cypher.
        if '{' in inner:
            label_part, _, props_part = inner.partition('{')
            return f"({label_part.rstrip()}:{label} {{{props_part})"
        return f"({inner}:{label})"

    return re.sub(r'\(([^()]*)\)', replacer, line)
