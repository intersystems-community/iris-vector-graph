from typing import Optional


class AQLParseError(Exception):
    def __init__(self, message: str, line: int = 0, column: int = 0):
        self.message = message
        self.line = line
        self.column = column
        super().__init__(f"AQL syntax error at line {line}, col {column}: {message}")


class AQLTranslationError(Exception):
    def __init__(self, message: str, aql_construct: str = ""):
        self.message = message
        self.aql_construct = aql_construct
        detail = f" ({aql_construct})" if aql_construct else ""
        super().__init__(f"AQL translation error{detail}: {message}")


def translate_aql(aql: str, bind_vars: Optional[dict] = None) -> tuple:
    from iris_vector_graph.cypher.aql.lexer import AQLLexer
    from iris_vector_graph.cypher.aql.parser import AQLParser
    from iris_vector_graph.cypher.aql.translator import AQLTranslator
    tokens = AQLLexer(aql).tokenize()
    aql_ast = AQLParser(tokens).parse()
    translator = AQLTranslator()
    cypher_str, resolved_params = translator.translate_to_cypher(aql_ast, bind_vars or {})
    return cypher_str, resolved_params
