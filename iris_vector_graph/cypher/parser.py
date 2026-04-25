"""
Recursive-Descent Cypher Parser

Translates Cypher query strings into an Abstract Syntax Tree (AST).
Replaces the temporary regex-based implementation.
"""

from typing import List, Optional, Any, Dict
from .lexer import Lexer, Token, TokenType
from . import ast
import logging

logger = logging.getLogger(__name__)


class CypherParseError(Exception):
    """Raised when Cypher parsing fails"""

    def __init__(
        self,
        message: str,
        line: int = 0,
        column: int = 0,
        suggestion: Optional[str] = None,
    ):
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
                column=tok.column,
            )
        return self.eat()

    def matches(self, kind: TokenType) -> bool:
        if self.peek().kind == kind:
            self.eat()
            return True
        return False

    def parse_procedure_call(self) -> ast.CypherProcedureCall:
        """Parse CALL ivg.vector.search(args...) YIELD node, score [, ...]"""
        self.expect(TokenType.CALL)

        # Parse dotted procedure name: IDENTIFIER (DOT IDENTIFIER)*
        name_tok = self.expect(TokenType.IDENTIFIER)
        procedure_name = name_tok.value or ""
        while self.peek().kind == TokenType.DOT:
            self.eat()  # consume DOT
            part_tok = self.expect(TokenType.IDENTIFIER)
            procedure_name += "." + (part_tok.value or "")

        # Parse argument list
        self.expect(TokenType.LPAREN)
        arguments = []
        options: Dict[str, Any] = {}
        if self.peek().kind != TokenType.RPAREN:
            while True:
                # Options map literal as last argument: {key: value, ...}
                if self.peek().kind == TokenType.LBRACE:
                    self.eat()  # consume LBRACE
                    options = self.parse_map_literal()
                    self.expect(TokenType.RBRACE)
                else:
                    arguments.append(self.parse_expression())
                if not self.matches(TokenType.COMMA):
                    break
        self.expect(TokenType.RPAREN)

        # Parse YIELD items
        yield_items: List[str] = []
        if self.peek().kind == TokenType.YIELD:
            self.eat()  # consume YIELD
            while True:
                tok = self.expect(TokenType.IDENTIFIER)
                yield_items.append(tok.value or "")
                if not self.matches(TokenType.COMMA):
                    break

        return ast.CypherProcedureCall(
            procedure_name=procedure_name,
            arguments=arguments,
            yield_items=yield_items,
            options=options,
        )

    def parse(self) -> ast.CypherQuery:
        graph_context = None
        if (
            self.peek().kind == TokenType.IDENTIFIER
            and self.peek().value
            and self.peek().value.upper() == "USE"
            and self.lexer.peek_ahead(1).kind == TokenType.IDENTIFIER
            and self.lexer.peek_ahead(1).value
            and self.lexer.peek_ahead(1).value.upper() == "GRAPH"
        ):
            self.eat()
            self.eat()
            graph_name_tok = self.peek()
            if graph_name_tok.kind == TokenType.STRING_LITERAL:
                graph_context = self.eat().value
            elif graph_name_tok.kind == TokenType.IDENTIFIER:
                graph_context = self.eat().value

        query_parts = []

        if self.peek().kind == TokenType.CALL:
            if self.lexer.peek_ahead(1).kind == TokenType.LBRACE:
                pass  # fall through to normal query_part parsing below
            else:
                proc = self.parse_procedure_call()

                while self.peek().kind in (TokenType.MATCH, TokenType.WITH) or (
                    self.peek().kind == TokenType.IDENTIFIER
                    and self.peek().value
                    and self.peek().value.upper() == "OPTIONAL"
                ):
                    if self.peek().kind == TokenType.WITH:
                        with_clause = self.parse_with_clause()
                        part = self.parse_query_part()
                        if query_parts:
                            query_parts[-1].with_clause = with_clause
                        query_parts.append(part)
                    else:
                        query_parts.append(self.parse_query_part())

                return_clause = None
                if self.peek().kind == TokenType.RETURN:
                    return_clause = self.parse_return_clause()

                order_by = self.parse_order_by_clause()
                skip = self.parse_skip()
                limit = self.parse_limit()

                self.expect(TokenType.EOF)

                return ast.CypherQuery(
                    query_parts=query_parts,
                    return_clause=return_clause,
                    order_by_clause=order_by,
                    skip=skip,
                    limit=limit,
                    procedure_call=proc,
                )

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

        union_queries = []
        while self.peek().kind == TokenType.UNION:
            self.eat()
            union_all = self.matches(TokenType.ALL)
            branch = self._parse_union_branch()
            union_queries.append({"query": branch, "all": union_all})

        self.expect(TokenType.EOF)

        q = ast.CypherQuery(
            query_parts=query_parts,
            return_clause=return_clause,
            order_by_clause=order_by,
            skip=skip,
            limit=limit,
            graph_context=graph_context,
        )
        q.union_queries = union_queries
        return q

    def parse_with_clause(self) -> ast.WithClause:
        self.expect(TokenType.WITH)

        if self.peek().kind == TokenType.STAR:
            self.eat()
            where_clause = self.parse_where_clause()
            return ast.WithClause(items=[], distinct=False, where_clause=where_clause, star=True)

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
            elif (
                kind == TokenType.IDENTIFIER
                and tok.value
                and tok.value.upper() == "OPTIONAL"
            ):
                # Check for OPTIONAL MATCH
                self.eat()  # OPTIONAL
                self.expect(TokenType.MATCH)
                clauses.append(self.parse_match_clause(optional=True))
            elif kind == TokenType.UNWIND:
                clauses.append(self.parse_unwind_clause())
            elif kind in (
                TokenType.CREATE,
                TokenType.DELETE,
                TokenType.MERGE,
                TokenType.SET,
                TokenType.REMOVE,
                TokenType.DETACH,
            ):
                clauses.append(self.parse_updating_clause())
            elif kind == TokenType.FOREACH:
                clauses.append(self.parse_foreach_clause())
            elif kind == TokenType.WHERE:
                clauses.append(self.parse_where_clause())
            elif (
                kind == TokenType.CALL
                and self.lexer.peek_ahead(1).kind == TokenType.LBRACE
            ):
                clauses.append(self.parse_subquery_call())
            elif kind == TokenType.CALL:
                part = ast.QueryPart(clauses=clauses)
                part.procedure_call = self.parse_procedure_call()
                return part
            else:
                break

        return ast.QueryPart(clauses=clauses)

    def parse_match_clause(self, optional: bool = False) -> ast.MatchClause:
        if not optional:
            self.expect(TokenType.MATCH)

        patterns = []
        named_paths = []
        while True:
            path_var = None
            if (
                self.peek().kind == TokenType.IDENTIFIER
                and self.lexer.peek_ahead(1).kind == TokenType.EQUALS
            ):
                path_var = self.eat().value
                self.eat()

            if self.peek().kind == TokenType.IDENTIFIER and self.peek().value in (
                "shortestPath",
                "allShortestPaths",
            ):
                fn_name = self.eat().value
                self.expect(TokenType.LPAREN)
                pattern = self.parse_graph_pattern()
                self.expect(TokenType.RPAREN)
                is_all = fn_name == "allShortestPaths"
                for rel in pattern.relationships:
                    if rel.variable_length is None:
                        rel.variable_length = ast.VariableLength(
                            min_hops=1,
                            max_hops=5,
                            shortest=not is_all,
                            all_shortest=is_all,
                        )
                    else:
                        rel.variable_length.shortest = not is_all
                        rel.variable_length.all_shortest = is_all
            else:
                pattern = self.parse_graph_pattern()

            patterns.append(pattern)

            if path_var:
                named_paths.append(ast.NamedPath(variable=path_var, pattern=pattern))

            if not self.matches(TokenType.COMMA):
                break

        return ast.MatchClause(
            patterns=patterns, named_paths=named_paths, optional=optional
        )

    def parse_unwind_clause(self) -> ast.UnwindClause:
        """Parse UNWIND [1,2,3] AS x"""
        self.expect(TokenType.UNWIND)
        expr = self.parse_expression()
        self.expect(TokenType.AS)
        alias_tok = self.expect(TokenType.IDENTIFIER)
        alias = alias_tok.value
        if alias is None:
            raise CypherParseError(
                "Expected alias for UNWIND",
                line=alias_tok.line,
                column=alias_tok.column,
            )
        return ast.UnwindClause(expression=expr, alias=alias)

    def parse_subquery_call(self) -> ast.SubqueryCall:
        self.expect(TokenType.CALL)
        self.expect(TokenType.LBRACE)

        import_variables: list = []
        if self.peek().kind == TokenType.WITH:
            self.eat()
            while True:
                var_tok = self.expect(TokenType.IDENTIFIER)
                if var_tok.value:
                    import_variables.append(var_tok.value)
                if not self.matches(TokenType.COMMA):
                    break

        inner_parts = [self.parse_query_part()]

        while (
            self.peek().kind == TokenType.WITH and self.peek().kind != TokenType.RBRACE
        ):
            with_clause = self.parse_with_clause()
            part = self.parse_query_part()
            inner_parts[-1].with_clause = with_clause
            inner_parts.append(part)

        inner_return = None
        if self.peek().kind == TokenType.RETURN:
            inner_return = self.parse_return_clause()

        if inner_return is None:
            tok = self.peek()
            raise CypherParseError(
                "Subquery must contain a RETURN clause",
                line=tok.line,
                column=tok.column,
            )

        self.expect(TokenType.RBRACE)

        inner_query = ast.CypherQuery(
            query_parts=inner_parts,
            return_clause=inner_return,
        )

        in_transactions = False
        batch_size = None
        if self.peek().kind == TokenType.IN:
            self.eat()
            if self.peek().kind == TokenType.TRANSACTIONS:
                self.eat()
                in_transactions = True
                if (
                    self.peek().kind == TokenType.IDENTIFIER
                    and self.peek().value
                    and self.peek().value.upper() == "OF"
                ):
                    self.eat()
                    size_tok = self.expect(TokenType.INTEGER_LITERAL)
                    batch_size = int(size_tok.value) if size_tok.value else None
                    if self.peek().kind == TokenType.ROWS:
                        self.eat()

        return ast.SubqueryCall(
            inner_query=inner_query,
            import_variables=import_variables,
            in_transactions=in_transactions,
            transactions_batch_size=batch_size,
        )

    def parse_case_expression(self) -> ast.CaseExpression:
        self.expect(TokenType.CASE)
        test_expr = None
        if self.peek().kind not in (TokenType.WHEN, TokenType.EOF):
            test_expr = self.parse_expression()
        when_clauses = []
        while self.peek().kind == TokenType.WHEN:
            self.eat()
            condition = self.parse_expression()
            self.expect(TokenType.THEN)
            result = self.parse_expression()
            when_clauses.append(ast.CaseWhenClause(condition=condition, result=result))
        else_result = None
        if self.peek().kind == TokenType.ELSE:
            self.eat()
            else_result = self.parse_expression()
        self.expect(TokenType.END)
        return ast.CaseExpression(
            when_clauses=when_clauses,
            else_result=else_result,
            test_expression=test_expr,
        )

    def _parse_union_branch(self) -> ast.CypherQuery:
        parts = []
        while self.peek().kind not in (
            TokenType.EOF,
            TokenType.UNION,
            TokenType.RETURN,
        ):
            parts.append(self.parse_query_part())
        ret = None
        skip_val = None
        limit_val = None
        if self.peek().kind == TokenType.RETURN:
            ret = self.parse_return_clause()
            skip_val = self.parse_skip()
            limit_val = self.parse_limit()
        q = ast.CypherQuery(query_parts=parts, return_clause=ret)
        q.union_queries = []
        q.skip = skip_val
        q.limit = limit_val
        return q

    def parse_foreach_clause(self) -> ast.ForeachClause:
        self.expect(TokenType.FOREACH)
        self.expect(TokenType.LPAREN)
        var_tok = self.expect(TokenType.IDENTIFIER)
        var_name = var_tok.value
        self.expect(TokenType.IN)
        source = self.parse_expression()
        self.expect(TokenType.PIPE)
        update_clauses = []
        while self.peek().kind in (
            TokenType.CREATE,
            TokenType.DELETE,
            TokenType.MERGE,
            TokenType.SET,
            TokenType.REMOVE,
            TokenType.DETACH,
            TokenType.FOREACH,
        ):
            if self.peek().kind == TokenType.FOREACH:
                update_clauses.append(self.parse_foreach_clause())
            else:
                update_clauses.append(self.parse_updating_clause())
        self.expect(TokenType.RPAREN)
        return ast.ForeachClause(
            variable=var_name, source=source, update_clauses=update_clauses
        )

    def parse_updating_clause(self) -> ast.UpdatingClause:
        """Parse CREATE, MERGE, DELETE, SET, REMOVE"""
        kind = self.peek().kind
        if kind == TokenType.CREATE:
            return self.parse_create_clause()
        if kind in (TokenType.DELETE, TokenType.DETACH):
            return self.parse_delete_clause()
        if kind == TokenType.MERGE:
            return self.parse_merge_clause()
        if kind == TokenType.SET:
            return self.parse_set_clause()
        if kind == TokenType.REMOVE:
            return self.parse_remove_clause()
        raise CypherParseError(f"Unexpected token {kind} in updating clause")

    def parse_create_clause(self) -> ast.CreateClause:
        self.expect(TokenType.CREATE)
        patterns = [self.parse_graph_pattern()]
        while self.matches(TokenType.COMMA):
            patterns.append(self.parse_graph_pattern())
        return ast.CreateClause(patterns=patterns)

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
            self.eat()  # ON
            # action_type can be CREATE or MATCH keyword
            action_tok = self.eat()
            action_type = (
                action_tok.kind.value.upper()
                if action_tok.kind in (TokenType.CREATE, TokenType.MATCH)
                else ""
            )
            if not action_type:
                raise CypherParseError(
                    f"Expected CREATE or MATCH after ON, got {action_tok.kind}"
                )

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
                raise CypherParseError(
                    "SET target must be property reference or variable"
                )

            if self.matches(TokenType.EQUALS):
                value = self.parse_expression()
                items.append(ast.SetItem(expression=target, value=value))
            elif self.peek().kind == TokenType.PLUS_EQUAL:
                self.eat()
                value = self.parse_expression()
                items.append(ast.SetItem(expression=target, value=value, merge=True))
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
                raise CypherParseError(
                    "REMOVE target must be property reference or variable"
                )
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
                if self.peek().kind == TokenType.MINUS:
                    self.eat()
                    return ast.RelationshipPattern(
                        variable=None,
                        types=[],
                        direction=ast.Direction.BOTH,
                        properties={},
                        variable_length=None,
                    )
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
            elif self.peek().kind == TokenType.DOT:
                self.eat()
                self.expect(TokenType.DOT)
                max_tok = self.expect(TokenType.INTEGER_LITERAL)
                if max_tok.value:
                    max_h = int(max_tok.value)
            var_len = ast.VariableLength(min_h, max_h)

        props = {}
        if self.peek().kind == TokenType.LBRACE:
            self.eat()
            props = self.parse_map_literal()
            self.expect(TokenType.RBRACE)

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
            variable=var, types=types, direction=direction, variable_length=var_len,
            properties=props,
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
            if (
                self.peek().kind == TokenType.IDENTIFIER
                and self.peek().value
                and self.peek().value.lower() == "exists"
                and self.lexer.peek_ahead(1).kind == TokenType.LBRACE
            ):
                self.eat()
                self.eat()
                if self.peek().kind == TokenType.MATCH:
                    self.eat()
                pattern = self.parse_graph_pattern()
                self.expect(TokenType.RBRACE)
                return ast.ExistsExpression(pattern=pattern, negated=True)
            operand = self.parse_not_expression()
            return ast.BooleanExpression(ast.BooleanOperator.NOT, [operand])
        return self.parse_comparison_expression()

    def parse_additive_expression(self) -> Any:
        left = self.parse_multiplicative_expression()
        while self.peek().kind in (TokenType.PLUS, TokenType.MINUS):
            op = "+" if self.peek().kind == TokenType.PLUS else "-"
            self.eat()
            right = self.parse_multiplicative_expression()
            left = ast.FunctionCall(
                function_name=f"__arith_{op}", arguments=[left, right]
            )
        return left

    def parse_multiplicative_expression(self) -> Any:
        left = self.parse_power_expression()
        while self.peek().kind in (TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            if self.peek().kind == TokenType.STAR:
                op = "*"
            elif self.peek().kind == TokenType.SLASH:
                op = "/"
            else:
                op = "%"
            self.eat()
            right = self.parse_power_expression()
            left = ast.FunctionCall(
                function_name=f"__arith_{op}", arguments=[left, right]
            )
        return left

    def parse_power_expression(self) -> Any:
        base = self.parse_primary_expression()
        if self.peek().kind == TokenType.CARET:
            self.eat()
            exp = self.parse_power_expression()
            return ast.FunctionCall(function_name="__arith_^", arguments=[base, exp])
        while self.peek().kind in (TokenType.LBRACKET, TokenType.DOT):
            if self.peek().kind == TokenType.LBRACKET:
                self.eat()
                if self.peek().kind == TokenType.DOT and self.lexer.peek_ahead(1).kind == TokenType.DOT:
                    self.eat()
                    self.eat()
                    end = self.parse_primary_expression()
                    self.expect(TokenType.RBRACKET)
                    base = ast.SliceExpression(expression=base, start=ast.Literal(0), end=end)
                else:
                    first = self.parse_primary_expression()
                    if self.peek().kind == TokenType.DOT and self.lexer.peek_ahead(1).kind == TokenType.DOT:
                        self.eat()
                        self.eat()
                        second = self.parse_primary_expression()
                        self.expect(TokenType.RBRACKET)
                        base = ast.SliceExpression(expression=base, start=first, end=second)
                    else:
                        self.expect(TokenType.RBRACKET)
                        base = ast.SubscriptExpression(expression=base, index=first)
            elif self.peek().kind == TokenType.DOT:
                self.eat()
                prop_tok = self.expect(TokenType.IDENTIFIER)
                base = ast.PropertyAccessExpression(
                    expression=base, property_name=prop_tok.value or ""
                )
        return base

    def parse_comparison_expression(self) -> Any:
        left = self.parse_additive_expression()

        if isinstance(left, ast.Variable) and self.peek().kind == TokenType.COLON:
            self.eat()
            label_tok = self.expect(TokenType.IDENTIFIER)
            return ast.LabelPredicate(variable=left.name, label=label_tok.value or "")

        # Binary comparisons
        tok = self.peek()
        op = None
        already_consumed = (
            False  # Track if operator tokens were consumed in the match block
        )
        match tok.kind:
            case TokenType.EQUALS:
                op = ast.BooleanOperator.EQUALS
            case TokenType.NOT_EQUALS:
                op = ast.BooleanOperator.NOT_EQUALS
            case TokenType.LESS_THAN:
                op = ast.BooleanOperator.LESS_THAN
            case TokenType.LESS_THAN_OR_EQUAL:
                op = ast.BooleanOperator.LESS_THAN_OR_EQUAL
            case TokenType.GREATER_THAN:
                op = ast.BooleanOperator.GREATER_THAN
            case TokenType.GREATER_THAN_OR_EQUAL:
                op = ast.BooleanOperator.GREATER_THAN_OR_EQUAL
            case TokenType.STARTS:
                self.eat()  # STARTS
                self.expect(TokenType.WITH_KW)
                op = ast.BooleanOperator.STARTS_WITH
                already_consumed = True
            case TokenType.ENDS:
                self.eat()  # ENDS
                self.expect(TokenType.WITH_KW)
                op = ast.BooleanOperator.ENDS_WITH
                already_consumed = True
            case TokenType.CONTAINS:
                op = ast.BooleanOperator.CONTAINS
            case TokenType.REGEX_MATCH:
                op = ast.BooleanOperator.REGEX_MATCH
            case TokenType.IN:
                op = ast.BooleanOperator.IN
            case TokenType.IS:
                self.eat()  # IS
                if self.matches(TokenType.NOT):
                    self.expect(TokenType.NULL)
                    return ast.BooleanExpression(
                        ast.BooleanOperator.IS_NOT_NULL, [left]
                    )
                self.expect(TokenType.NULL)
                return ast.BooleanExpression(ast.BooleanOperator.IS_NULL, [left])

        if op:
            if not already_consumed:
                self.eat()
            right = self.parse_primary_expression()
            return ast.BooleanExpression(op, [left, right])

        return left

    def parse_primary_expression(self) -> Any:
        """Parse atomic expression elements"""
        tok = self.peek()

        if tok.kind == TokenType.CASE:
            return self.parse_case_expression()

        if tok.kind == TokenType.LPAREN:
            self.eat()
            expr = self.parse_expression()
            self.expect(TokenType.RPAREN)
            return expr

        if tok.kind == TokenType.LBRACE:
            self.eat()
            entries = self.parse_map_literal()
            self.expect(TokenType.RBRACE)
            return ast.MapLiteral(entries=entries)

        if tok.kind == TokenType.IDENTIFIER:
            name = self.eat().value
            if name is None:
                raise CypherParseError(
                    "Expected identifier value", tok.line, tok.column
                )

            if name.lower() == "exists" and self.peek().kind == TokenType.LBRACE:
                self.eat()
                if self.peek().kind == TokenType.MATCH:
                    self.eat()
                pattern = self.parse_graph_pattern()
                self.expect(TokenType.RBRACE)
                return ast.ExistsExpression(pattern=pattern, negated=False)

            if name.lower() in ("shortestpath", "allshortestpaths") and self.peek().kind == TokenType.LPAREN:
                self.eat()
                pattern = self.parse_graph_pattern()
                self.expect(TokenType.RPAREN)
                is_all = name.lower() == "allshortestpaths"
                for rel in pattern.relationships:
                    if rel.variable_length is None:
                        rel.variable_length = ast.VariableLength(
                            min_hops=1, max_hops=5, shortest=not is_all, all_shortest=is_all
                        )
                    else:
                        rel.variable_length.shortest = not is_all
                        rel.variable_length.all_shortest = is_all
                return ast.FunctionCall(function_name=name.lower(), arguments=[ast.Literal(pattern)])

            if (
                name.lower() in ("any", "none", "single", "all")
                and self.peek().kind == TokenType.LPAREN
            ):
                self.eat()
                var_tok = self.expect(TokenType.IDENTIFIER)
                var_name = var_tok.value
                self.expect(TokenType.IN)
                source = self.parse_expression()
                predicate = None
                if self.matches(TokenType.WHERE):
                    predicate = self.parse_expression()
                self.expect(TokenType.RPAREN)
                return ast.ListPredicateExpression(
                    quantifier=name.lower(),
                    variable=var_name,
                    source=source,
                    predicate=predicate or ast.Literal(True),
                )

            if name.lower() == "reduce" and self.peek().kind == TokenType.LPAREN:
                self.eat()
                acc_tok = self.expect(TokenType.IDENTIFIER)
                acc_name = acc_tok.value
                self.expect(TokenType.EQUALS)
                init_expr = self.parse_expression()
                self.expect(TokenType.COMMA)
                var_tok = self.expect(TokenType.IDENTIFIER)
                var_name = var_tok.value
                self.expect(TokenType.IN)
                source = self.parse_expression()
                self.expect(TokenType.PIPE)
                body = self.parse_expression()
                self.expect(TokenType.RPAREN)
                return ast.ReduceExpression(
                    accumulator=acc_name,
                    init=init_expr,
                    variable=var_name,
                    source=source,
                    body=body,
                )

            if (
                name.lower() in ("filter", "extract")
                and self.peek().kind == TokenType.LPAREN
            ):
                self.eat()
                var_tok = self.expect(TokenType.IDENTIFIER)
                var_name = var_tok.value
                self.expect(TokenType.IN)
                source = self.parse_expression()
                predicate = None
                if self.matches(TokenType.WHERE):
                    predicate = self.parse_expression()
                projection = None
                if self.matches(TokenType.PIPE):
                    projection = self.parse_expression()
                self.expect(TokenType.RPAREN)
                return ast.ListComprehension(
                    variable=var_name,
                    source=source,
                    predicate=predicate,
                    projection=projection,
                )
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
                    call = ast.FunctionCall(name.lower(), args)
                if self.matches(TokenType.DOT):
                    prop_tok = self.expect(TokenType.IDENTIFIER)
                    return ast.FunctionCall(
                        "__prop__",
                        [call, ast.Literal(prop_tok.value)]
                    )
                return call

            if self.matches(TokenType.DOT):
                prop_tok = self.expect(TokenType.IDENTIFIER)
                if prop_tok.value is None:
                    raise CypherParseError(
                        "Expected property name", prop_tok.line, prop_tok.column
                    )
                return ast.PropertyReference(name, prop_tok.value)
            return ast.Variable(name)

        if tok.kind == TokenType.PARAMETER:
            return ast.Variable(self.eat().value or "")

        if tok.kind == TokenType.LBRACKET:
            self.eat()
            items = []
            if not self.matches(TokenType.RBRACKET):
                if self.peek().kind == TokenType.LPAREN:
                    pattern = self.parse_graph_pattern()
                    predicate = None
                    if self.matches(TokenType.WHERE):
                        predicate = self.parse_expression()
                    projection = None
                    if self.matches(TokenType.PIPE):
                        projection = self.parse_expression()
                    self.expect(TokenType.RBRACKET)
                    return ast.PatternComprehension(
                        pattern=pattern, predicate=predicate, projection=projection
                    )
                if (
                    self.peek().kind == TokenType.IDENTIFIER
                    and self.lexer.peek_ahead(1).kind == TokenType.IN
                ):
                    var_name = self.eat().value
                    self.eat()
                    source = self.parse_expression()
                    predicate = None
                    if self.matches(TokenType.WHERE):
                        predicate = self.parse_expression()
                    projection = None
                    if self.matches(TokenType.PIPE):
                        projection = self.parse_expression()
                    self.expect(TokenType.RBRACKET)
                    return ast.ListComprehension(
                        variable=var_name,
                        source=source,
                        predicate=predicate,
                        projection=projection,
                    )
                first = self.parse_expression()
                items.append(first)
                while self.matches(TokenType.COMMA):
                    items.append(self.parse_expression())
                self.expect(TokenType.RBRACKET)
            return ast.Literal(items)

        if tok.kind == TokenType.MINUS:
            self.eat()
            inner = self.parse_primary_expression()
            if isinstance(inner, ast.Literal) and isinstance(inner.value, (int, float)):
                return ast.Literal(-inner.value)
            return (
                ast.UnaryOp(op="-", operand=inner) if hasattr(ast, "UnaryOp") else inner
            )

        if (
            tok.kind == TokenType.ALL
            and self.lexer.peek_ahead(1).kind == TokenType.LPAREN
        ):
            self.eat()
            self.eat()
            var_tok = self.expect(TokenType.IDENTIFIER)
            var_name = var_tok.value
            self.expect(TokenType.IN)
            source = self.parse_expression()
            predicate = None
            if self.matches(TokenType.WHERE):
                predicate = self.parse_expression()
            self.expect(TokenType.RPAREN)
            return ast.ListPredicateExpression(
                quantifier="all",
                variable=var_name,
                source=source,
                predicate=predicate or ast.Literal(True),
            )

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

        if tok.kind == TokenType.TRUE:
            self.eat()
            return ast.Literal(True)

        if tok.kind == TokenType.FALSE:
            self.eat()
            return ast.Literal(False)

        if tok.kind == TokenType.NULL:
            self.eat()
            return ast.Literal(None)

        raise CypherParseError(
            f"Unexpected token in expression: {tok.kind}", tok.line, tok.column
        )

    def parse_map_literal(self) -> Dict[str, Any]:
        """Parse {key: value, ...}"""
        props = {}
        while self.peek().kind == TokenType.IDENTIFIER:
            key_tok = self.eat()
            key = key_tok.value
            if key is None:
                raise CypherParseError(
                    "Expected property key", key_tok.line, key_tok.column
                )
            self.expect(TokenType.COLON)
            val = self.parse_primary_expression()
            props[key] = val
            if not self.matches(TokenType.COMMA):
                break
        return props

    def parse_order_by_clause(self) -> Optional[ast.OrderByClause]:
        if not self.matches(TokenType.ORDER):
            return None
        self.expect(TokenType.BY)
        items = []
        while True:
            expr = self.parse_expression()
            asc = True
            if self.matches(TokenType.DESC):
                asc = False
            else:
                self.matches(TokenType.ASC)

            if expr is not None:
                items.append(ast.OrderByItem(expr, asc))
            if not self.matches(TokenType.COMMA):
                break
        return ast.OrderByClause(items=items)

    def parse_limit(self) -> Any:
        """Parse LIMIT clause. Accepts integer literals or parameter references ($name)."""
        if self.matches(TokenType.LIMIT):
            tok = self.peek()
            if tok.kind == TokenType.PARAMETER:
                self.eat()
                return ast.Variable(tok.value or "")  # resolved later against params
            tok = self.expect(TokenType.INTEGER_LITERAL)
            return int(tok.value) if tok.value is not None else None
        return None

    def parse_skip(self) -> Any:
        """Parse SKIP clause. Accepts integer literals or parameter references ($name)."""
        if self.matches(TokenType.SKIP):
            tok = self.peek()
            if tok.kind == TokenType.PARAMETER:
                self.eat()
                return ast.Variable(tok.value or "")  # resolved later against params
            tok = self.expect(TokenType.INTEGER_LITERAL)
            return int(tok.value) if tok.value is not None else None
        return None


def parse_query(
    query_str: str, params: Optional[Dict[str, Any]] = None
) -> ast.CypherQuery:
    """Convenience function to parse a Cypher query string"""
    lexer = Lexer(query_str)
    parser = Parser(lexer)
    return parser.parse()
