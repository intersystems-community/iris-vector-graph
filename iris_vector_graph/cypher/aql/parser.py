from .lexer import AQLToken, AQLTokenType
from .ast import (AQLDirection, AQLQuery, ForClause, ShortestPathClause, FilterClause,
                   LetClause, CollectClause, SortClause, SortItem, LimitClause, ReturnClause,
                   AQLVariable, AQLPropertyAccess, AQLKeyAccess, AQLBindVar, AQLLiteral,
                   AQLBinaryOp, AQLUnaryOp, AQLFunctionCall, AQLObjectLiteral, AQLArrayLiteral)
from iris_vector_graph.cypher.aql import AQLParseError, AQLTranslationError


class AQLParser:
    def __init__(self, tokens: list):
        self._tokens = tokens
        self._pos = 0

    def _peek(self, offset: int = 0) -> AQLToken:
        p = self._pos + offset
        return self._tokens[p] if p < len(self._tokens) else self._tokens[-1]

    def _eat(self) -> AQLToken:
        t = self._tokens[self._pos]
        self._pos += 1
        return t

    def _expect(self, kind: AQLTokenType) -> AQLToken:
        t = self._peek()
        if t.kind != kind:
            raise AQLParseError(f"Expected {kind.value}, got '{t.value}'", t.line, t.column)
        return self._eat()

    def _match(self, *kinds) -> bool:
        return self._peek().kind in kinds

    def parse(self) -> AQLQuery:
        t = self._peek()
        if t.kind != AQLTokenType.FOR:
            raise AQLParseError(f"AQL query must start with FOR, got '{t.value}'", t.line, t.column)

        # Check for nested FOR before building — scan for second FOR before RETURN
        depth = 0
        for i, tok in enumerate(self._tokens):
            if tok.kind == AQLTokenType.FOR:
                depth += 1
                if depth > 1:
                    raise AQLTranslationError(
                        "nested FOR traversal not supported — use Cypher directly or await Spec 158",
                        "nested FOR"
                    )
            if tok.kind == AQLTokenType.RETURN:
                break

        self._expect(AQLTokenType.FOR)
        # Detect unsupported top-level keywords that appear instead of depth
        if self._match(AQLTokenType.SEARCH):
            raise AQLTranslationError("SEARCH not supported in Spec 157, await Spec 159", "SEARCH")
        if self._match(AQLTokenType.K_SHORTEST_PATHS):
            raise AQLTranslationError("K_SHORTEST_PATHS not supported in Spec 157, await Spec 158", "K_SHORTEST_PATHS")
        if self._match(AQLTokenType.K_PATHS):
            raise AQLTranslationError("K_PATHS not supported in Spec 157, await Spec 158", "K_PATHS")
        vertex_var = self._expect(AQLTokenType.IDENT).value
        edge_var = None
        path_var = None

        if self._match(AQLTokenType.COMMA):
            self._eat()
            edge_var = self._expect(AQLTokenType.IDENT).value
            if self._match(AQLTokenType.COMMA):
                self._eat()
                path_var = self._expect(AQLTokenType.IDENT).value

        self._expect(AQLTokenType.IN)

        # Check for SHORTEST_PATH pattern
        if self._match(AQLTokenType.OUTBOUND, AQLTokenType.INBOUND, AQLTokenType.ANY):
            dir_tok = self._eat()
            if self._match(AQLTokenType.SHORTEST_PATH):
                self._eat()
                return self._finish_shortest_path(vertex_var, edge_var, dir_tok)
            if self._match(AQLTokenType.K_SHORTEST_PATHS):
                raise AQLTranslationError("K_SHORTEST_PATHS not supported in Spec 157, await Spec 158", "K_SHORTEST_PATHS")
            if self._match(AQLTokenType.K_PATHS):
                raise AQLTranslationError("K_PATHS not supported in Spec 157, await Spec 158", "K_PATHS")

        # depth range: either N or N..M
        if self._match(AQLTokenType.INT):
            min_d = int(self._eat().value)
            if self._match(AQLTokenType.RANGE):
                self._eat()
                max_d = int(self._expect(AQLTokenType.INT).value)
            else:
                max_d = min_d
        else:
            raise AQLParseError("Expected depth (integer or range)", self._peek().line, self._peek().column)

        direction = self._parse_direction()
        start_expr = self._parse_expression()

        graph_or_collections = []
        is_graph = False
        if self._match(AQLTokenType.GRAPH):
            self._eat()
            name_tok = self._eat()
            graph_or_collections = [name_tok.value]
            is_graph = True
        elif self._match(AQLTokenType.DYN_COLLECTION):
            raise AQLTranslationError(
                "dynamic collection binding not supported — resolve @@collection before calling translate_aql",
                "@@collection"
            )
        else:
            while self._match(AQLTokenType.IDENT, AQLTokenType.STRING):
                graph_or_collections.append(self._eat().value)
                if self._match(AQLTokenType.COMMA):
                    if self._peek(1).kind == AQLTokenType.DYN_COLLECTION:
                        self._eat()
                        raise AQLTranslationError(
                            "dynamic collection binding not supported",
                            "@@collection"
                        )
                    self._eat()
                else:
                    break

        for_clause = ForClause(
            vertex_var=vertex_var, edge_var=edge_var, path_var=path_var,
            min_depth=min_d, max_depth=max_d, direction=direction,
            start_expr=start_expr, graph_or_collections=graph_or_collections, is_graph=is_graph
        )

        filter_clauses = []
        let_clauses = []
        collect_clause = None
        sort_clause = None
        limit_clause = None

        while not self._match(AQLTokenType.RETURN, AQLTokenType.EOF):
            if self._match(AQLTokenType.SEARCH):
                raise AQLTranslationError("SEARCH not supported in Spec 157, await Spec 159", "SEARCH")
            elif self._match(AQLTokenType.K_SHORTEST_PATHS):
                raise AQLTranslationError("K_SHORTEST_PATHS not supported", "K_SHORTEST_PATHS")
            elif self._match(AQLTokenType.FILTER):
                self._eat()
                filter_clauses.append(FilterClause(condition=self._parse_expression()))
            elif self._match(AQLTokenType.LET):
                self._eat()
                var = self._expect(AQLTokenType.IDENT).value
                self._expect(AQLTokenType.ASSIGN)
                let_clauses.append(LetClause(variable=var, value=self._parse_expression()))
            elif self._match(AQLTokenType.COLLECT):
                collect_clause = self._parse_collect()
            elif self._match(AQLTokenType.SORT):
                sort_clause = self._parse_sort()
            elif self._match(AQLTokenType.LIMIT):
                limit_clause = self._parse_limit()
            elif self._match(AQLTokenType.WITH):
                self._eat()
            else:
                t = self._peek()
                raise AQLParseError(f"Unexpected token '{t.value}'", t.line, t.column)

        if self._match(AQLTokenType.RETURN):
            return_clause = self._parse_return()
        else:
            raise AQLParseError("Expected RETURN", self._peek().line, self._peek().column)

        return AQLQuery(
            for_clause=for_clause, filter_clauses=filter_clauses, let_clauses=let_clauses,
            collect_clause=collect_clause, sort_clause=sort_clause,
            limit_clause=limit_clause, return_clause=return_clause
        )

    def _finish_shortest_path(self, vertex_var, edge_var, dir_tok):
        start_expr = self._parse_expression()
        self._expect(AQLTokenType.TO)
        end_expr = self._parse_expression()
        direction = {AQLTokenType.OUTBOUND: AQLDirection.OUTBOUND,
                     AQLTokenType.INBOUND: AQLDirection.INBOUND,
                     AQLTokenType.ANY: AQLDirection.ANY}[dir_tok.kind]
        graph_or_collections = []
        is_graph = False
        if self._match(AQLTokenType.GRAPH):
            self._eat(); graph_or_collections = [self._eat().value]; is_graph = True
        else:
            while self._match(AQLTokenType.IDENT, AQLTokenType.STRING):
                graph_or_collections.append(self._eat().value)
                if not self._match(AQLTokenType.COMMA): break
                self._eat()
        sp = ShortestPathClause(vertex_var=vertex_var, edge_var=edge_var,
                                direction=direction, start_expr=start_expr,
                                end_expr=end_expr, graph_or_collections=graph_or_collections,
                                is_graph=is_graph)
        filter_clauses = []
        while self._match(AQLTokenType.FILTER):
            self._eat()
            filter_clauses.append(FilterClause(condition=self._parse_expression()))
        return_clause = None
        if self._match(AQLTokenType.RETURN):
            return_clause = self._parse_return()
        return AQLQuery(for_clause=sp, filter_clauses=filter_clauses, return_clause=return_clause)

    def _parse_direction(self) -> AQLDirection:
        t = self._peek()
        if t.kind == AQLTokenType.OUTBOUND: self._eat(); return AQLDirection.OUTBOUND
        if t.kind == AQLTokenType.INBOUND: self._eat(); return AQLDirection.INBOUND
        if t.kind == AQLTokenType.ANY: self._eat(); return AQLDirection.ANY
        raise AQLParseError(f"Expected direction keyword, got '{t.value}'", t.line, t.column)

    def _parse_collect(self) -> CollectClause:
        self._eat()
        assignments = []
        with_count_into = None
        aggregate = []
        into_var = None
        while self._match(AQLTokenType.IDENT) and self._peek(1).kind == AQLTokenType.ASSIGN:
            var = self._eat().value; self._eat()
            expr = self._parse_expression()
            assignments.append((var, expr))
            if not self._match(AQLTokenType.COMMA): break
            self._eat()
        if self._match(AQLTokenType.WITH):
            self._eat()
            if self._match(AQLTokenType.COUNT):
                self._eat()
                if self._match(AQLTokenType.INTO):
                    self._eat()
                    with_count_into = self._expect(AQLTokenType.IDENT).value
        elif self._match(AQLTokenType.INTO):
            self._eat()
            into_var = self._expect(AQLTokenType.IDENT).value
        return CollectClause(assignments=assignments, with_count_into=with_count_into,
                             aggregate=aggregate, into_var=into_var)

    def _parse_sort(self) -> SortClause:
        self._eat()
        items = []
        while True:
            expr = self._parse_expression()
            ascending = True
            if self._match(AQLTokenType.ASC): self._eat()
            elif self._match(AQLTokenType.DESC): self._eat(); ascending = False
            items.append(SortItem(expression=expr, ascending=ascending))
            if not self._match(AQLTokenType.COMMA): break
            self._eat()
        return SortClause(items=items)

    def _parse_limit(self) -> LimitClause:
        self._eat()
        first = int(self._expect(AQLTokenType.INT).value)
        if self._match(AQLTokenType.COMMA):
            self._eat()
            second = int(self._expect(AQLTokenType.INT).value)
            return LimitClause(offset=first, count=second)
        return LimitClause(offset=None, count=first)

    def _parse_return(self) -> ReturnClause:
        self._eat()
        distinct = False
        if self._match(AQLTokenType.DISTINCT): self._eat(); distinct = True
        expr = self._parse_expression()
        return ReturnClause(distinct=distinct, expression=expr)

    def _parse_expression(self):
        return self._parse_or()

    def _parse_or(self):
        left = self._parse_and()
        while self._match(AQLTokenType.OR):
            op = self._eat().value
            left = AQLBinaryOp(operator=op, left=left, right=self._parse_and())
        return left

    def _parse_and(self):
        left = self._parse_not()
        while self._match(AQLTokenType.AND):
            op = self._eat().value
            left = AQLBinaryOp(operator=op, left=left, right=self._parse_not())
        return left

    def _parse_not(self):
        if self._match(AQLTokenType.NOT):
            self._eat()
            return AQLUnaryOp(operator="NOT", operand=self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self):
        left = self._parse_additive()
        ops = {AQLTokenType.EQ, AQLTokenType.NEQ, AQLTokenType.LT, AQLTokenType.LTE,
               AQLTokenType.GT, AQLTokenType.GTE, AQLTokenType.REGEX_MATCH,
               AQLTokenType.REGEX_NOTMATCH, AQLTokenType.IN}
        if self._peek().kind in ops:
            not_in = False
            if self._match(AQLTokenType.NOT):
                self._eat(); not_in = True
            op_tok = self._eat()
            right = self._parse_additive()
            op = ("NOT IN" if not_in else op_tok.value) if op_tok.kind == AQLTokenType.IN else op_tok.value
            return AQLBinaryOp(operator=op, left=left, right=right)
        return left

    def _parse_additive(self):
        left = self._parse_unary()
        while self._match(AQLTokenType.PLUS, AQLTokenType.MINUS):
            op = self._eat().value
            left = AQLBinaryOp(operator=op, left=left, right=self._parse_unary())
        return left

    def _parse_unary(self):
        if self._match(AQLTokenType.MINUS):
            self._eat(); return AQLUnaryOp(operator="-", operand=self._parse_unary())
        return self._parse_postfix()

    def _parse_postfix(self):
        node = self._parse_primary()
        while True:
            if self._match(AQLTokenType.DOT):
                self._eat()
                prop = self._eat().value
                if prop.startswith('_'):
                    node = AQLKeyAccess(object=node, key=prop)
                else:
                    node = AQLPropertyAccess(object=node, property=prop)
            elif self._match(AQLTokenType.LBRACKET):
                self._eat(); self._parse_expression(); self._expect(AQLTokenType.RBRACKET)
            else:
                break
        return node

    def _parse_primary(self):
        t = self._peek()

        if t.kind == AQLTokenType.BIND_VAR:
            self._eat(); return AQLBindVar(name=t.value[1:])

        if t.kind == AQLTokenType.DYN_COLLECTION:
            raise AQLTranslationError(
                "dynamic collection binding not supported — resolve @@collection before calling translate_aql",
                "@@collection"
            )

        if t.kind == AQLTokenType.IDENT:
            name = self._eat().value
            if self._match(AQLTokenType.LPAREN):
                self._eat()
                args = []
                while not self._match(AQLTokenType.RPAREN, AQLTokenType.EOF):
                    args.append(self._parse_expression())
                    if self._match(AQLTokenType.COMMA): self._eat()
                self._expect(AQLTokenType.RPAREN)
                return AQLFunctionCall(name=name.upper(), args=args)
            return AQLVariable(name=name)

        if t.kind == AQLTokenType.INT:
            self._eat(); return AQLLiteral(value=int(t.value))
        if t.kind == AQLTokenType.FLOAT:
            self._eat(); return AQLLiteral(value=float(t.value))
        if t.kind == AQLTokenType.STRING:
            self._eat(); return AQLLiteral(value=t.value)
        if t.kind == AQLTokenType.TRUE:
            self._eat(); return AQLLiteral(value=True)
        if t.kind == AQLTokenType.FALSE:
            self._eat(); return AQLLiteral(value=False)
        if t.kind == AQLTokenType.NULL:
            self._eat(); return AQLLiteral(value=None)

        if t.kind == AQLTokenType.LBRACE:
            self._eat()
            fields = {}
            while not self._match(AQLTokenType.RBRACE, AQLTokenType.EOF):
                k = self._eat().value
                self._expect(AQLTokenType.COLON)
                fields[k] = self._parse_expression()
                if self._match(AQLTokenType.COMMA): self._eat()
            self._expect(AQLTokenType.RBRACE)
            return AQLObjectLiteral(fields=fields)

        if t.kind == AQLTokenType.LBRACKET:
            self._eat()
            items = []
            while not self._match(AQLTokenType.RBRACKET, AQLTokenType.EOF):
                items.append(self._parse_expression())
                if self._match(AQLTokenType.COMMA): self._eat()
            self._expect(AQLTokenType.RBRACKET)
            return AQLArrayLiteral(items=items)

        if t.kind == AQLTokenType.LPAREN:
            self._eat(); expr = self._parse_expression(); self._expect(AQLTokenType.RPAREN)
            return expr

        raise AQLParseError(f"Unexpected token '{t.value}'", t.line, t.column)
