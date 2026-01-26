"""
Recursive-Descent Cypher Parser

Translates Cypher query strings into an Abstract Syntax Tree (AST).
Replaces the temporary regex-based implementation.
"""

from typing import List, Optional, Any, Dict, Union
from .lexer import Lexer, Token, TokenType
from . import ast
import logging

logger = logging.getLogger(__name__)

class CypherParseError(Exception):
    """Raised when Cypher parsing fails"""
    def __init__(self, message: str, line: int = 0, column: int = 0, suggestion: Optional[str] = None):
        self.message = message
        self.line = line
        self.column = column
        self.suggestion = suggestion
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"Cypher error at line {self.line}, col {self.column}: {self.message}"
        if self.suggestion:
            msg += f"\nSuggestion: {self.suggestion}"
        return msg

class Parser:
    """Base Recursive-Descent Parser for Cypher"""
    
    def __init__(self, lexer: Lexer):
        self.lexer = lexer

    def peek(self) -> Token:
        return self.lexer.peek()

    def eat(self) -> Token:
        return self.lexer.eat()

    def expect(self, kind: TokenType) -> Token:
        tok = self.peek()
        if tok.kind != kind:
            raise CypherParseError(
                f"Expected {kind.value}, got {tok.kind.value if tok.kind.value else tok.kind}",
                line=tok.line,
                column=tok.column
            )
        return self.eat()

    def matches(self, kind: TokenType) -> bool:
        if self.peek().kind == kind:
            self.eat()
            return True
        return False

    def parse(self) -> ast.CypherQuery:
        """Entry point for parsing a complete query"""
        query_parts = []
        
        # Parse first QueryPart (MATCH ...)
        query_parts.append(self.parse_query_part())
        
        # Parse subsequent stages (WITH ...)
        while self.peek().kind == TokenType.WITH:
            with_clause = self.parse_with_clause()
            # Each WITH starts a new QueryPart
            part = self.parse_query_part()
            # Attached to the stage that just finished
            query_parts[-1].with_clause = with_clause
            query_parts.append(part)
            
        # Final projection (Optional in Cypher if updating clauses are present)
        return_clause = None
        if self.peek().kind == TokenType.RETURN:
            return_clause = self.parse_return_clause()
        
        # Optional clauses
        order_by = self.parse_order_by_clause()
        skip = self.parse_skip()
        limit = self.parse_limit()
        
        self.expect(TokenType.EOF)
        
        return ast.CypherQuery(
            query_parts=query_parts,
            return_clause=return_clause,
            order_by_clause=order_by,
            skip=skip,
            limit=limit
        )

    def parse_with_clause(self) -> ast.WithClause:
        """Parse WITH a, b.prop AS alias WHERE ..."""
        self.expect(TokenType.WITH)
        
        distinct = self.matches(TokenType.DISTINCT)
        items = []
        
        while True:
            expr = self.parse_expression()
            alias = None
            if self.matches(TokenType.AS):
                alias = self.expect(TokenType.IDENTIFIER).value
            
            items.append(ast.ReturnItem(expression=expr, alias=alias))
            
            if not self.matches(TokenType.COMMA):
                break
                
        where_clause = self.parse_where_clause()
        
        return ast.WithClause(items=items, distinct=distinct, where_clause=where_clause)

    def parse_query_part(self) -> ast.QueryPart:
        """Parse a single stage of a query (MATCH... UNWIND... WHERE... UPDATE...)"""
        clauses = []
        
        while True:
            tok = self.peek()
            kind = tok.kind
            if kind == TokenType.MATCH:
                clauses.append(self.parse_match_clause(optional=False))
            elif kind == TokenType.IDENTIFIER and tok.value and tok.value.upper() == "OPTIONAL":
                # Check for OPTIONAL MATCH
                self.eat() # OPTIONAL
                self.expect(TokenType.MATCH)
                clauses.append(self.parse_match_clause(optional=True))
            elif kind == TokenType.UNWIND:
                clauses.append(self.parse_unwind_clause())
            elif kind in (TokenType.CREATE, TokenType.DELETE, TokenType.MERGE, TokenType.SET, TokenType.REMOVE, TokenType.DETACH):
                clauses.append(self.parse_updating_clause())
            elif kind == TokenType.WHERE:
                clauses.append(self.parse_where_clause())
            else:
                break
            
        return ast.QueryPart(clauses=clauses)

    def parse_match_clause(self, optional: bool = False) -> ast.MatchClause:
        """Parse [OPTIONAL] MATCH (n:Label)-[r:TYPE]->(m), (x:Other)"""
        # Note: MATCH already eaten by caller if OPTIONAL
        if not optional:
            self.expect(TokenType.MATCH)
        
        # Cypher allows multiple comma-separated patterns in a single MATCH
        # For MVP, we'll return the first one or we need to update AST
        # Actually, let's update AST to support multiple patterns in MatchClause
        patterns = []
        while True:
            patterns.append(self.parse_graph_pattern())
            if not self.matches(TokenType.COMMA):
                break
        
        # Simplified for now: use first pattern if only one expected by downstream?
        # No, let's fix ast.py MatchClause
        return ast.MatchClause(patterns=patterns, optional=optional)

    def parse_unwind_clause(self) -> ast.UnwindClause:
        """Parse UNWIND [1,2,3] AS x"""
        self.expect(TokenType.UNWIND)
        expr = self.parse_expression()
        self.expect(TokenType.AS)
        alias_tok = self.expect(TokenType.IDENTIFIER)
        alias = alias_tok.value
        if alias is None:
            raise CypherParseError("Expected alias for UNWIND", line=alias_tok.line, column=alias_tok.column)
        return ast.UnwindClause(expression=expr, alias=alias)

    def parse_updating_clause(self) -> ast.UpdatingClause:
        """Parse CREATE, MERGE, DELETE, SET, REMOVE"""
        kind = self.peek().kind
        if kind == TokenType.CREATE: return self.parse_create_clause()
        if kind in (TokenType.DELETE, TokenType.DETACH): return self.parse_delete_clause()
        if kind == TokenType.MERGE: return self.parse_merge_clause()
        if kind == TokenType.SET: return self.parse_set_clause()
        if kind == TokenType.REMOVE: return self.parse_remove_clause()
        raise CypherParseError(f"Unexpected token {kind} in updating clause")

    def parse_create_clause(self) -> ast.CreateClause:
        self.expect(TokenType.CREATE)
        pattern = self.parse_graph_pattern()
        return ast.CreateClause(pattern=pattern)

    def parse_delete_clause(self) -> ast.DeleteClause:
        detach = self.matches(TokenType.DETACH)
        self.expect(TokenType.DELETE)
        vars = []
        while True:
            var_tok = self.expect(TokenType.IDENTIFIER)
            var_name = var_tok.value
            if var_name is None:
                raise CypherParseError("Expected variable for DELETE")
            vars.append(ast.Variable(var_name))
            if not self.matches(TokenType.COMMA):
                break
        return ast.DeleteClause(expressions=vars, detach=detach)

    def parse_merge_clause(self) -> ast.MergeClause:
        self.expect(TokenType.MERGE)
        pattern = self.parse_graph_pattern()
        
        on_create = None
        on_match = None
        
        while self.peek().kind == TokenType.ON:
            self.eat() # ON
            # action_type can be CREATE or MATCH keyword
            action_tok = self.eat()
            action_type = action_tok.kind.value.upper() if action_tok.kind in (TokenType.CREATE, TokenType.MATCH) else ""
            if not action_type:
                raise CypherParseError(f"Expected CREATE or MATCH after ON, got {action_tok.kind}")
                
            self.expect(TokenType.SET)
            items = self.parse_set_items()
            # Convert list of SetItem to list of UpdateItem for typing
            action = ast.MergeAction(items=[i for i in items])
            if action_type == "CREATE":
                on_create = action
            elif action_type == "MATCH":
                on_match = action
                
        return ast.MergeClause(pattern=pattern, on_create=on_create, on_match=on_match)

    def parse_set_clause(self) -> ast.SetClause:
        self.expect(TokenType.SET)
        return ast.SetClause(items=self.parse_set_items())

    def parse_set_items(self) -> List[ast.SetItem]:
        items = []
        while True:
            target = self.parse_primary_expression()
            if not isinstance(target, (ast.PropertyReference, ast.Variable)):
                raise CypherParseError("SET target must be property reference or variable")
            
            if self.matches(TokenType.EQUALS):
                value = self.parse_expression()
                items.append(ast.SetItem(expression=target, value=value))
            elif self.matches(TokenType.COLON):
                # SET n:Label
                label_tok = self.expect(TokenType.IDENTIFIER)
                label = label_tok.value if label_tok.value else ""
                items.append(ast.SetItem(expression=target, value=label))
            else:
                raise CypherParseError("Expected '=' or ':' in SET item")
                
            if not self.matches(TokenType.COMMA):
                break
        return items

    def parse_remove_clause(self) -> ast.RemoveClause:
        self.expect(TokenType.REMOVE)
        items = []
        while True:
            target = self.parse_primary_expression()
            if not isinstance(target, (ast.PropertyReference, ast.Variable)):
                raise CypherParseError("REMOVE target must be property reference or variable")
            items.append(ast.RemoveItem(expression=target))
            if not self.matches(TokenType.COMMA):
                break
        return ast.RemoveClause(items=items)

    def last_token_value(self) -> str:
        # Helper to get value of token just consumed
        return self.lexer.tokens[self.lexer.token_index - 1].value or ""

    def parse_graph_pattern(self) -> ast.GraphPattern:
        """Parse a full graph pattern (node)-[rel]->(node)"""
        nodes = []
        relationships = []
        
        nodes.append(self.parse_node_pattern())
        
        while self.peek().kind in (TokenType.MINUS, TokenType.ARROW_LEFT):
            relationships.append(self.parse_relationship_pattern())
            nodes.append(self.parse_node_pattern())
            
        return ast.GraphPattern(nodes=nodes, relationships=relationships)

    def parse_node_pattern(self) -> ast.NodePattern:
        """Parse (variable:Label {props})"""
        self.expect(TokenType.LPAREN)
        
        var = None
        if self.peek().kind == TokenType.IDENTIFIER:
            var = self.eat().value
            
        labels = []
        while self.matches(TokenType.COLON):
            label_tok = self.expect(TokenType.IDENTIFIER)
            if label_tok.value:
                labels.append(label_tok.value)
            
        props = {}
        if self.matches(TokenType.LBRACE):
            props = self.parse_map_literal()
            self.expect(TokenType.RBRACE)
            
        self.expect(TokenType.RPAREN)
        return ast.NodePattern(variable=var, labels=labels, properties=props)

    def parse_relationship_pattern(self) -> ast.RelationshipPattern:
        """Parse -[r:TYPE]-> or <-[r:TYPE]- or -[r:TYPE]-"""
        direction = ast.Direction.BOTH
        
        if self.matches(TokenType.ARROW_LEFT):
            direction = ast.Direction.INCOMING
            self.expect(TokenType.LBRACKET)
        else:
            self.expect(TokenType.MINUS)
            if self.matches(TokenType.LBRACKET):
                # -[...]
                pass
            else:
                # Malformed
                tok = self.peek()
                raise CypherParseError("Expected '[' after '-'", tok.line, tok.column)
        
        # Inside brackets [...]
        var = None
        if self.peek().kind == TokenType.IDENTIFIER:
            var = self.eat().value
            
        types = []
        if self.matches(TokenType.COLON):
            type_tok = self.expect(TokenType.IDENTIFIER)
            if type_tok.value:
                types.append(type_tok.value)
            while self.matches(TokenType.PIPE):
                next_type_tok = self.expect(TokenType.IDENTIFIER)
                if next_type_tok.value:
                    types.append(next_type_tok.value)
                
        # Optional variable length *1..3
        var_len = None
        if self.matches(TokenType.STAR):
            min_h = 1
            max_h = 1
            if self.peek().kind == TokenType.INTEGER_LITERAL:
                min_tok = self.eat()
                if min_tok.value:
                    min_h = int(min_tok.value)
                if self.matches(TokenType.DOT):
                    self.expect(TokenType.DOT)
                    max_tok = self.expect(TokenType.INTEGER_LITERAL)
                    if max_tok.value:
                        max_h = int(max_tok.value)
            var_len = ast.VariableLength(min_h, max_h)
            
        self.expect(TokenType.RBRACKET)
        
        # Closing arrow
        if direction == ast.Direction.INCOMING:
            self.expect(TokenType.MINUS)
        else:
            if self.matches(TokenType.ARROW_RIGHT):
                direction = ast.Direction.OUTGOING
            else:
                self.expect(TokenType.MINUS)
                direction = ast.Direction.BOTH
                
        return ast.RelationshipPattern(
            variable=var, 
            types=types, 
            direction=direction,
            variable_length=var_len
        )

    def parse_return_clause(self) -> ast.ReturnClause:
        """Parse RETURN a, b.prop AS alias"""
        self.expect(TokenType.RETURN)
        
        distinct = self.matches(TokenType.DISTINCT)
        items = []
        
        while True:
            expr = self.parse_expression()
            alias = None
            if self.matches(TokenType.AS):
                alias = self.expect(TokenType.IDENTIFIER).value
            
            items.append(ast.ReturnItem(expression=expr, alias=alias))
            
            if not self.matches(TokenType.COMMA):
                break
                
        return ast.ReturnClause(items=items, distinct=distinct)

    def parse_where_clause(self) -> Optional[ast.WhereClause]:
        """Parse WHERE ..."""
        if not self.matches(TokenType.WHERE):
            return None
        expr = self.parse_expression()
        return ast.WhereClause(expression=expr)

    def parse_expression(self) -> Any:
        """Parse boolean expression with OR precedence"""
        return self.parse_or_expression()

    def parse_or_expression(self) -> Any:
        left = self.parse_and_expression()
        while self.matches(TokenType.OR):
            right = self.parse_and_expression()
            left = ast.BooleanExpression(ast.BooleanOperator.OR, [left, right])
        return left

    def parse_and_expression(self) -> Any:
        left = self.parse_not_expression()
        while self.matches(TokenType.AND):
            right = self.parse_not_expression()
            left = ast.BooleanExpression(ast.BooleanOperator.AND, [left, right])
        return left

    def parse_not_expression(self) -> Any:
        if self.matches(TokenType.NOT):
            operand = self.parse_not_expression()
            return ast.BooleanExpression(ast.BooleanOperator.NOT, [operand])
        return self.parse_comparison_expression()

    def parse_comparison_expression(self) -> Any:
        left = self.parse_primary_expression()
        
        # Binary comparisons
        tok = self.peek()
        op = None
        match tok.kind:
            case TokenType.EQUALS: op = ast.BooleanOperator.EQUALS
            case TokenType.NOT_EQUALS: op = ast.BooleanOperator.NOT_EQUALS
            case TokenType.LESS_THAN: op = ast.BooleanOperator.LESS_THAN
            case TokenType.LESS_THAN_OR_EQUAL: op = ast.BooleanOperator.LESS_THAN_OR_EQUAL
            case TokenType.GREATER_THAN: op = ast.BooleanOperator.GREATER_THAN
            case TokenType.GREATER_THAN_OR_EQUAL: op = ast.BooleanOperator.GREATER_THAN_OR_EQUAL
            case TokenType.STARTS:
                self.eat() # STARTS
                self.expect(TokenType.WITH_KW)
                op = ast.BooleanOperator.STARTS_WITH
            case TokenType.ENDS:
                self.eat() # ENDS
                self.expect(TokenType.WITH_KW)
                op = ast.BooleanOperator.ENDS_WITH
            case TokenType.CONTAINS:
                op = ast.BooleanOperator.CONTAINS
            case TokenType.IN:
                op = ast.BooleanOperator.IN
            case TokenType.IS:
                self.eat() # IS
                if self.matches(TokenType.NOT):
                    self.expect(TokenType.NULL)
                    return ast.BooleanExpression(ast.BooleanOperator.IS_NOT_NULL, [left])
                self.expect(TokenType.NULL)
                return ast.BooleanExpression(ast.BooleanOperator.IS_NULL, [left])
        
        if op:
            self.eat()
            right = self.parse_primary_expression()
            return ast.BooleanExpression(op, [left, right])
            
        return left

    def parse_primary_expression(self) -> Any:
        """Parse atomic expression elements"""
        tok = self.peek()
        
        if tok.kind == TokenType.LPAREN:
            self.eat()
            expr = self.parse_expression()
            self.expect(TokenType.RPAREN)
            return expr

        if tok.kind == TokenType.IDENTIFIER:
            name = self.eat().value
            if name is None:
                raise CypherParseError("Expected identifier value", tok.line, tok.column)
            
            if self.matches(TokenType.LPAREN):
                # Function call or aggregation
                distinct = self.matches(TokenType.DISTINCT)
                args = []
                if not self.matches(TokenType.RPAREN):
                    while True:
                        args.append(self.parse_expression())
                        if not self.matches(TokenType.COMMA):
                            break
                    self.expect(TokenType.RPAREN)
                
                if name.lower() in ["count", "sum", "avg", "min", "max", "collect"]:
                    arg = args[0] if args else None
                    return ast.AggregationFunction(name.lower(), arg, distinct)
                else:
                    return ast.FunctionCall(name.lower(), args)

            if self.matches(TokenType.DOT):
                prop_tok = self.expect(TokenType.IDENTIFIER)
                if prop_tok.value is None:
                    raise CypherParseError("Expected property name", prop_tok.line, prop_tok.column)
                return ast.PropertyReference(name, prop_tok.value)
            return ast.Variable(name)

        if tok.kind == TokenType.PARAMETER:
            return ast.Variable(self.eat().value or "")

        if tok.kind == TokenType.LBRACKET:
            # List literal
            self.eat()
            items = []
            if not self.matches(TokenType.RBRACKET):
                while True:
                    items.append(self.parse_expression())
                    if not self.matches(TokenType.COMMA):
                        break
                self.expect(TokenType.RBRACKET)
            return ast.Literal(items)
            
        if tok.kind == TokenType.INTEGER_LITERAL:
            val = self.eat().value
            return ast.Literal(int(val) if val is not None else 0)
            
        if tok.kind == TokenType.FLOAT_LITERAL:
            val = self.eat().value
            return ast.Literal(float(val) if val is not None else 0.0)
            
        if tok.kind == TokenType.STRING_LITERAL:
            return ast.Literal(self.eat().value)
            
        if tok.kind == TokenType.STAR:
            self.eat()
            return ast.Literal("*")
            
        raise CypherParseError(f"Unexpected token in expression: {tok.kind}", tok.line, tok.column)

    def parse_map_literal(self) -> Dict[str, Any]:
        """Parse {key: value, ...}"""
        props = {}
        while self.peek().kind == TokenType.IDENTIFIER:
            key_tok = self.eat()
            key = key_tok.value
            if key is None:
                raise CypherParseError("Expected property key", key_tok.line, key_tok.column)
            self.expect(TokenType.COLON)
            val = self.parse_primary_expression()
            props[key] = val
            if not self.matches(TokenType.COMMA):
                break
        return props

    def parse_order_by_clause(self) -> Optional[ast.OrderByClause]:
        if not self.matches(TokenType.ORDER): return None
        self.expect(TokenType.BY)
        items = []
        while True:
            expr = self.parse_primary_expression()
            asc = True
            if self.matches(TokenType.DESC): asc = False
            else: self.matches(TokenType.ASC)
            
            if isinstance(expr, (ast.PropertyReference, ast.Variable)):
                items.append(ast.OrderByItem(expr, asc))
            if not self.matches(TokenType.COMMA): break
        return ast.OrderByClause(items=items)

    def parse_limit(self) -> Optional[int]:
        if self.matches(TokenType.LIMIT):
            tok = self.expect(TokenType.INTEGER_LITERAL)
            return int(tok.value) if tok.value is not None else None
        return None

    def parse_skip(self) -> Optional[int]:
        if self.matches(TokenType.SKIP):
            tok = self.expect(TokenType.INTEGER_LITERAL)
            return int(tok.value) if tok.value is not None else None
        return None

def parse_query(query_str: str, params: Optional[Dict[str, Any]] = None) -> ast.CypherQuery:
    """Convenience function to parse a Cypher query string"""
    lexer = Lexer(query_str)
    parser = Parser(lexer)
    return parser.parse()
