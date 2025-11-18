"""
Cypher Parser (MVP - Pattern-Based)

TEMPORARY IMPLEMENTATION: Pattern-based parser for common Cypher queries.
This is a pragmatic MVP solution to unblock development.

Supported Patterns:
- MATCH (n:Label) RETURN n.property
- MATCH (n:Label {prop: value}) RETURN n
- MATCH (a)-[r:TYPE]->(b) RETURN a, b
- WHERE clause with simple conditions
- Parameters ($param)

Future: Replace with libcypher-parser or full grammar (lark/pyparsing)
"""

import re
from typing import Dict, Any, Optional, List, Tuple
from .ast import (
    CypherQuery, MatchClause, NodePattern, RelationshipPattern,
    GraphPattern, WhereClause, ReturnClause, ReturnItem,
    PropertyReference, Variable, Literal, BooleanExpression,
    BooleanOperator, Direction, VariableLength, OrderByClause, OrderByItem
)


class CypherParseError(Exception):
    """Cypher parsing error with line/column information"""
    def __init__(
        self,
        message: str,
        line: int = 1,
        column: int = 1,
        suggestion: Optional[str] = None
    ):
        self.message = message
        self.line = line
        self.column = column
        self.suggestion = suggestion
        super().__init__(message)


class SimpleCypherParser:
    """
    MVP Parser for common Cypher patterns.

    This is a TEMPORARY implementation using regex pattern matching.
    Supports common use cases to unblock development.

    Limitations:
    - No complex nested expressions
    - Limited WHERE clause support
    - No subqueries or WITH clauses
    - No UNION or complex OPTIONAL MATCH

    For production, replace with libcypher-parser or full grammar parser.
    """

    # Regex patterns for common Cypher elements
    NODE_PATTERN = r'\((\w+)(?::(\w+))?\s*(?:\{([^}]+)\})?\)'
    REL_PATTERN = r'-\[(?:(\w+):)?(\w+)(?:\*(\d+)\.\.(\d+))?\]->'
    PROPERTY_REF = r'(\w+)\.(\w+)'
    PARAM_REF = r'\$(\w+)'

    def parse(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> CypherQuery:
        """
        Parse Cypher query string to AST.

        Args:
            query: Cypher query string
            parameters: Optional named parameters ($param -> value)

        Returns:
            CypherQuery AST

        Raises:
            CypherParseError: If query syntax is invalid
        """
        self.query = query.strip()
        self.parameters = parameters or {}
        self.pos = 0

        # Check for common syntax errors
        self._check_syntax_errors()

        # Parse clauses
        match_clauses = self._parse_match_clauses()
        where_clause = self._parse_where_clause()
        return_clause = self._parse_return_clause()
        order_by_clause = self._parse_order_by_clause()
        limit, skip = self._parse_limit_skip()

        # Build CypherQuery
        try:
            return CypherQuery(
                match_clauses=match_clauses,
                where_clause=where_clause,
                return_clause=return_clause,
                order_by_clause=order_by_clause,
                limit=limit,
                skip=skip
            )
        except ValueError as e:
            raise CypherParseError(str(e))

    def _check_syntax_errors(self):
        """Check for common syntax errors and provide suggestions"""
        # Check for common typos
        typos = {
            'RETRUN': 'RETURN',
            'METCH': 'MATCH',
            'WERE': 'WHERE',
            'ODER BY': 'ORDER BY'
        }

        for typo, correct in typos.items():
            if typo in self.query.upper():
                # Find line and column
                lines = self.query.split('\n')
                for line_num, line in enumerate(lines, 1):
                    if typo in line.upper():
                        col = line.upper().index(typo) + 1
                        raise CypherParseError(
                            f"Unexpected token '{typo}' at line {line_num}, column {col}",
                            line=line_num,
                            column=col,
                            suggestion=f"Did you mean '{correct}'?"
                        )

    def _parse_match_clauses(self) -> List[MatchClause]:
        """Parse MATCH clauses"""
        clauses = []

        # Find all MATCH clauses
        match_pattern = re.compile(r'MATCH\s+(.+?)(?=\s+(?:WHERE|RETURN|ORDER|LIMIT|$))', re.IGNORECASE)
        matches = match_pattern.findall(self.query)

        if not matches:
            # Try to find if MATCH is misspelled
            if 'RETURN' in self.query.upper() and 'MATCH' not in self.query.upper():
                raise CypherParseError(
                    "Query must have at least one MATCH clause",
                    suggestion="Add MATCH clause before RETURN"
                )

        for match_str in matches:
            pattern = self._parse_graph_pattern(match_str.strip())
            clauses.append(MatchClause(pattern=pattern))

        return clauses

    def _parse_graph_pattern(self, pattern_str: str) -> GraphPattern:
        """Parse graph pattern (nodes and relationships)"""
        nodes = []
        relationships = []

        # Simple pattern: (n:Label)-[:TYPE]->(m:Label)
        # Split by relationship arrows
        parts = re.split(r'(-\[.*?\]->)', pattern_str)

        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue

            if part.startswith('('):
                # Node pattern
                node_match = re.match(self.NODE_PATTERN, part)
                if node_match:
                    var, label, props_str = node_match.groups()
                    labels = [label] if label else []
                    properties = self._parse_properties(props_str) if props_str else {}
                    nodes.append(NodePattern(variable=var, labels=labels, properties=properties))
            elif part.startswith('-['):
                # Relationship pattern
                rel_match = re.match(self.REL_PATTERN, part)
                if rel_match:
                    var, rel_type, min_hops, max_hops = rel_match.groups()
                    types = [rel_type] if rel_type else []

                    variable_length = None
                    if min_hops and max_hops:
                        variable_length = VariableLength(int(min_hops), int(max_hops))

                    relationships.append(RelationshipPattern(
                        variable=var,
                        types=types,
                        direction=Direction.OUTGOING,
                        variable_length=variable_length
                    ))

        return GraphPattern(nodes=nodes, relationships=relationships)

    def _parse_properties(self, props_str: str) -> Dict[str, Any]:
        """Parse property map {key: value, key2: value2}"""
        properties = {}
        # Simple key:value parsing (MVP - doesn't handle nested objects)
        pairs = props_str.split(',')
        for pair in pairs:
            if ':' in pair:
                key, value = pair.split(':', 1)
                key = key.strip().strip('"').strip("'")
                value = value.strip().strip('"').strip("'")
                # Try to parse as number
                try:
                    if '.' in value:
                        value = float(value)
                    else:
                        value = int(value)
                except ValueError:
                    pass  # Keep as string
                properties[key] = value
        return properties

    def _parse_where_clause(self) -> Optional[WhereClause]:
        """Parse WHERE clause (simple conditions only)"""
        where_match = re.search(r'WHERE\s+(.+?)(?=\s+(?:RETURN|ORDER|LIMIT|$))', self.query, re.IGNORECASE)
        if not where_match:
            return None

        condition_str = where_match.group(1).strip()

        # Simple condition: p.name = 'TP53' or p.score > 0.8
        # For MVP, create simple equality/comparison expressions
        if '=' in condition_str and '!=' not in condition_str and '<>' not in condition_str:
            left, right = condition_str.split('=', 1)
            left = left.strip()
            right = right.strip().strip('"').strip("'")

            # Parse left side (property reference)
            prop_match = re.match(self.PROPERTY_REF, left)
            if prop_match:
                var, prop = prop_match.groups()
                left_expr = PropertyReference(variable=var, property_name=prop)
            else:
                left_expr = Variable(name=left)

            # Parse right side (literal or parameter)
            if right.startswith('$'):
                param_name = right[1:]
                right_expr = Literal(value=self.parameters.get(param_name))
            else:
                right_expr = Literal(value=right)

            expr = BooleanExpression(
                operator=BooleanOperator.EQUALS,
                operands=[left_expr, right_expr]
            )
            return WhereClause(expression=expr)

        return None

    def _parse_return_clause(self) -> Optional[ReturnClause]:
        """Parse RETURN clause"""
        return_match = re.search(r'RETURN\s+(DISTINCT\s+)?(.+?)(?:\s+ORDER|\s+LIMIT|$)', self.query, re.IGNORECASE | re.DOTALL)
        if not return_match:
            raise CypherParseError(
                "Query must have exactly one RETURN clause",
                suggestion="Add RETURN clause to specify query results"
            )

        distinct_keyword, items_str = return_match.groups()
        distinct = distinct_keyword is not None

        items = []
        for item_str in items_str.split(','):
            item_str = item_str.strip()

            # Check for alias (AS)
            alias = None
            if ' AS ' in item_str.upper():
                item_str, alias = re.split(r'\s+AS\s+', item_str, 1, re.IGNORECASE)
                alias = alias.strip()

            # Parse item (property reference or variable)
            prop_match = re.match(self.PROPERTY_REF, item_str)
            if prop_match:
                var, prop = prop_match.groups()
                expr = PropertyReference(variable=var, property_name=prop)
            else:
                expr = Variable(name=item_str)

            items.append(ReturnItem(expression=expr, alias=alias))

        return ReturnClause(items=items, distinct=distinct)

    def _parse_order_by_clause(self) -> Optional[OrderByClause]:
        """Parse ORDER BY clause"""
        order_match = re.search(r'ORDER\s+BY\s+(.+?)(?=\s+(?:LIMIT|$))', self.query, re.IGNORECASE)
        if not order_match:
            return None

        items_str = order_match.group(1).strip()
        items = []

        for item_str in items_str.split(','):
            item_str = item_str.strip()

            # Check for ASC/DESC
            ascending = True
            if item_str.upper().endswith(' DESC'):
                ascending = False
                item_str = item_str[:-5].strip()
            elif item_str.upper().endswith(' ASC'):
                item_str = item_str[:-4].strip()

            # Parse expression
            prop_match = re.match(self.PROPERTY_REF, item_str)
            if prop_match:
                var, prop = prop_match.groups()
                expr = PropertyReference(variable=var, property_name=prop)
            else:
                expr = Variable(name=item_str)

            items.append(OrderByItem(expression=expr, ascending=ascending))

        return OrderByClause(items=items) if items else None

    def _parse_limit_skip(self) -> Tuple[Optional[int], Optional[int]]:
        """Parse LIMIT and SKIP clauses"""
        limit = None
        skip = None

        limit_match = re.search(r'LIMIT\s+(\d+)', self.query, re.IGNORECASE)
        if limit_match:
            limit = int(limit_match.group(1))

        skip_match = re.search(r'SKIP\s+(\d+)', self.query, re.IGNORECASE)
        if skip_match:
            skip = int(skip_match.group(1))

        return limit, skip


# ==============================================================================
# Public API
# ==============================================================================

def parse_query(
    query: str,
    parameters: Optional[Dict[str, Any]] = None
) -> CypherQuery:
    """
    Parse openCypher query string to AST.

    Args:
        query: Cypher query string
        parameters: Optional named parameters

    Returns:
        CypherQuery AST

    Raises:
        CypherParseError: If query syntax is invalid

    Example:
        >>> ast = parse_query("MATCH (p:Protein {id: 'PROTEIN:TP53'}) RETURN p.name")
        >>> print(ast.return_clause.items[0].expression.property_name)
        'name'
    """
    parser = SimpleCypherParser()
    return parser.parse(query, parameters)
