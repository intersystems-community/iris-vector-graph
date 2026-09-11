"""behave step definitions: Given … graph setup steps."""
from uuid import uuid4

try:
    from behave import given, step
except ImportError:
    def given(s):  # noqa: E306
        def _d(f): return f
        return _d
    def step(s):  # noqa: E306
        def _d(f): return f
        return _d


def _new_label() -> str:
    return f"TCK_{uuid4().hex[:8]}"


def _sync_kg(context) -> None:
    """Rebuild ^KG adjacency index after graph setup so variable-length paths work."""
    try:
        context.engine.sync()
    except Exception:
        pass  # sync failure is non-fatal; BFS will warn and fail gracefully


@given("an empty graph")
def step_empty_graph(context):
    context.scenario_label = _new_label()
    context.params = {}
    context.last_result = None
    context.last_error = None


@given("any graph")
def step_any_graph(context):
    context.scenario_label = _new_label()
    context.params = {}
    context.last_result = None
    context.last_error = None


@given("having executed:")
def step_having_executed(context, query=None):
    if query is None:
        query = context.text
    query = query.strip()
    try:
        context.engine.execute_cypher(
            _inject_label(query, context.scenario_label), {}
        )
    except Exception:
        pass  # setup queries may fail if already exists; ignore
    # Rebuild ^KG adjacency index so variable-length path queries work.
    _sync_kg(context)


@step("the {graph_name} graph")
def step_named_graph(context, graph_name):
    label = context.named_graphs.get(graph_name)
    if label is None:
        raise RuntimeError(
            f"Named graph '{graph_name}' not in session registry. "
            f"Available: {list(context.named_graphs.keys())}"
        )
    context.scenario_label = label
    context.params = {}
    context.last_result = None
    context.last_error = None
    # Ensure the named graph data is populated in the DB
    from tests.tck.environment import _ensure_named_graph
    _ensure_named_graph(context, graph_name, label)


@step("there exists a procedure {procedure_sig}")
def step_procedure_exists(context, procedure_sig):
    """Register a test procedure with its signature and test data.

    Format: test.name(arg1 :: TYPE1, arg2 :: TYPE2) :: (out1 :: TYPE1, out2 :: TYPE2):
    Followed by a table with test data rows.

    Example:
      And there exists a procedure test.labels() :: (label :: STRING?):
        | label |
        | 'A'   |
        | 'B'   |
        | 'C'   |
    """
    import re

    # Ensure procedure registry exists
    if not hasattr(context, '_tck_procedures'):
        context._tck_procedures = {}

    # Parse the procedure signature
    # Format: test.name(arg1 :: TYPE1, ...) :: (out1 :: TYPE1, ...)
    match = re.match(
        r'(\w+(?:\.\w+)*)\s*\((.*?)\)\s*::\s*\((.*?)\)',
        procedure_sig.strip(),
        re.VERBOSE
    )
    if not match:
        raise ValueError(f"Invalid procedure signature: {procedure_sig}")

    proc_name = match.group(1)
    args_str = match.group(2).strip()
    outputs_str = match.group(3).strip()

    # Parse arguments: "name :: TYPE?, name2 :: TYPE?"
    args = []
    if args_str:
        for arg_spec in args_str.split(','):
            arg_spec = arg_spec.strip()
            if '::' in arg_spec:
                arg_name, arg_type = arg_spec.split('::', 1)
                args.append({
                    'name': arg_name.strip(),
                    'type': arg_type.strip()
                })

    # Parse outputs: "name :: TYPE?, name2 :: TYPE?"
    outputs = []
    if outputs_str:
        for out_spec in outputs_str.split(','):
            out_spec = out_spec.strip()
            if '::' in out_spec:
                out_name, out_type = out_spec.split('::', 1)
                outputs.append({
                    'name': out_name.strip(),
                    'type': out_type.strip()
                })

    # Extract test data from table
    rows = []
    if context.table:
        # behave Row iterates as cell values, not (heading, cell) pairs — use as_dict()
        for row in context.table.rows:
            rows.append(row.as_dict())

    # Register procedure
    context._tck_procedures[proc_name] = {
        'args': args,
        'outputs': outputs,
        'rows': rows,
    }


def _extract_match_bound_vars(query: str) -> set:
    """Extract variable names bound in MATCH or WITH clauses (not in CREATE).

    Includes WITH aliases like 'n AS a' so that CREATE (a)-[:T]->(b) does not
    re-inject the isolation label onto already-bound variables.
    """
    import re
    bound = set()
    in_match = False
    for line in query.split('\n'):
        stripped = line.strip().upper()
        first_word = stripped.split()[0] if stripped.split() else ''
        if first_word in ('MATCH', 'OPTIONAL'):
            in_match = True
        elif first_word in ('CREATE', 'MERGE', 'RETURN', 'WHERE',
                            'ORDER', 'SKIP', 'LIMIT', 'UNWIND', 'SET',
                            'DELETE', 'REMOVE', 'CALL', 'UNION'):
            in_match = False
        elif first_word == 'WITH':
            in_match = False
            # Collect all aliases introduced by WITH: "expr AS alias"
            for m in re.finditer(r'\bAS\s+([A-Za-z_][A-Za-z0-9_]*)', line, re.IGNORECASE):
                bound.add(m.group(1))
        if in_match:
            # Extract variable names from node patterns: (varname ...) or (varname)
            for m in re.finditer(r'\(([A-Za-z_][A-Za-z0-9_]*)', line):
                bound.add(m.group(1))
    return bound


def _inject_label(query: str, label: str, inject_anonymous: bool = True) -> str:
    """
    Inject isolation label into node patterns in CREATE/MERGE clauses.

    inject_anonymous: if True (default, for setup queries), also label anonymous
    nodes () so teardown can clean them.  If False (for main test queries),
    anonymous endpoint nodes are NOT labeled — this prevents them from polluting
    Stage1 CTE scans that query by the isolation label.  Orphaned anonymous
    nodes are cleaned up by _teardown_orphaned_nodes.

    Skips MATCH-bound variables and WITH aliases used as CREATE endpoints —
    they don't create new nodes and adding labels would trigger VariableAlreadyBound.

    Within a multi-line CREATE/MERGE block, tracks injected variables to avoid
    re-injecting labels on variable references in subsequent lines (e.g., relationships).
    """
    match_bound = _extract_match_bound_vars(query)
    lines = query.split('\n')
    result_lines = []
    in_create = False
    # Accumulate injected vars across ALL create blocks so that a variable defined
    # in one CREATE block is not re-labeled in a later CREATE block of the same query.
    all_create_injected_vars: set = set()

    for line in lines:
        stripped = line.strip().upper()
        first_word = stripped.split()[0] if stripped.split() else ''
        if first_word in ('CREATE', 'MERGE'):
            in_create = True
        elif first_word in ('MATCH', 'OPTIONAL', 'WITH', 'RETURN', 'WHERE',
                            'ORDER', 'SKIP', 'LIMIT', 'UNWIND', 'SET',
                            'DELETE', 'REMOVE', 'CALL', 'UNION'):
            in_create = False

        if in_create:
            line, newly_injected = _inject_all_nodes_tracking(
                line, label, skip_vars=match_bound.union(all_create_injected_vars),
                inject_anonymous=inject_anonymous)
            all_create_injected_vars.update(newly_injected)
        result_lines.append(line)
    return '\n'.join(result_lines)


def _inject_all_nodes_tracking(line: str, label: str, skip_vars: set = None,
                               inject_anonymous: bool = True) -> tuple[str, set]:
    """Add :<label> to every node pattern in a line, returning modified line and injected vars.

    Returns: (modified_line, newly_injected_variables)

    skip_vars: variable names to skip (e.g. MATCH-bound vars used as CREATE endpoints,
               or variables already injected in prior lines of a CREATE/MERGE block).
    inject_anonymous: if False, skip anonymous () nodes (no variable name).

    On the same line, track which variables have already been injected with the label
    to avoid double-binding (e.g., CREATE (root:A), ..., (root)-[:R]->(...) should not
    inject the label into the second (root) reference since it refers to the already-bound root).
    """
    if skip_vars is None:
        skip_vars = set()
    injected_vars = set()  # Track vars we've injected on this line
    result = []
    i = 0
    while i < len(line):
        if line[i] == '(':
            # Find matching )
            depth = 0
            end = i
            for k in range(i, len(line)):
                if line[k] == '(':
                    depth += 1
                elif line[k] == ')':
                    depth -= 1
                    if depth == 0:
                        end = k
                        break

            inner = line[i+1:end]
            # Extract variable name from inner (before first : or {)
            inner_stripped = inner.strip()
            var_name = inner_stripped.split(':')[0].split('{')[0].strip() if inner_stripped else ''
            # Inject label unless: already labeled, a skip_var, already injected on this line,
            # or truly anonymous (empty inner) when inject_anonymous is disabled.
            # Nodes with labels but no var name — e.g. (:A) — are not anonymous and always get
            # the isolation label (unless already on this line or in skip_vars).
            is_truly_anonymous = not inner.strip()
            if label not in inner and var_name not in skip_vars and var_name not in injected_vars:
                if not is_truly_anonymous or inject_anonymous:
                    inner = _add_label_to_node(inner, label)
                    if var_name:  # Track that we injected this variable
                        injected_vars.add(var_name)
            result.append(f"({inner})")
            i = end + 1
        else:
            result.append(line[i])
            i += 1
    return ''.join(result), injected_vars


def _inject_all_nodes(line: str, label: str, skip_vars: set = None,
                      inject_anonymous: bool = True) -> str:
    """Add :<label> to every node pattern in a line. (Legacy wrapper; use _inject_all_nodes_tracking)"""
    result, _ = _inject_all_nodes_tracking(line, label, skip_vars, inject_anonymous)
    return result


def _add_label_to_node(inner: str, label: str) -> str:
    """Add :<label> to a node pattern inner string, before any {} props."""
    if '{' in inner:
        label_part, _, props_part = inner.partition('{')
        return f"{label_part.rstrip()}:{label} {{{props_part}"
    else:
        return f"{inner}:{label}"
