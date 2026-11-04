"""Read code the way it runs, not the file it came from (4.6.0 R128).

`inspect.getsource(fn)` reads the source file at the loaded code's line numbers, so a file changed on disk during a run
(a commit merged into the checkout mid-suite) hands a test other lines than the ones that run (R091). These helpers
read the compiled code instead:

* `names(obj)`: every global, attribute and local name the code uses, nested functions and comprehensions included.
* `strings(obj)` / `constants(obj)`: every string constant / every constant, docstrings left out, literal tuples and
  sets opened. The compiler joins adjacent literals and folds `10**9`; an f-string gives its literal pieces.
* `const_args(obj, attr)`: the literal arguments of each `.attr(...)` call (`row.get("id")` gives `("id",)`).
* `tokens(obj)`: names and string constants in the order the instructions use them, a nested function's in place,
  for "A happens before B" checks.
* `module_names(name)` / `module_strings(name)`: the same for a whole module, compiled from its file as it was when
  first asked (read once, through `importlib.util.find_spec(name).origin`). A module keeps no code object of its own.
"""

from __future__ import annotations

import dis
import functools
import importlib.util
import inspect
import types
from typing import Iterator

_NAME_OPS = {
    "LOAD_GLOBAL", "LOAD_NAME", "LOAD_ATTR", "LOAD_METHOD", "LOAD_DEREF", "LOAD_FAST", "LOAD_FAST_CHECK",
    "LOAD_FAST_AND_CLEAR", "LOAD_SUPER_ATTR", "STORE_ATTR", "STORE_GLOBAL", "STORE_NAME", "STORE_FAST",
    "IMPORT_NAME", "IMPORT_FROM", "LOAD_FROM_DICT_OR_GLOBALS", "LOAD_FROM_DICT_OR_DEREF",
}


def code_of(obj) -> types.CodeType:
    """The code object behind a function, method, property, staticmethod or fixture (`__wrapped__` followed)."""
    if isinstance(obj, types.CodeType):
        return obj
    if isinstance(obj, property):
        obj = obj.fget
    if isinstance(obj, (staticmethod, classmethod)):
        obj = obj.__func__
    obj = inspect.unwrap(obj)
    code = getattr(obj, "__code__", None)
    if code is None:
        raise TypeError(f"no code object behind {obj!r}")
    return code


def _codes(code: types.CodeType) -> Iterator[types.CodeType]:
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _codes(const)


def _docstring(code: types.CodeType):
    """The docstring constant, when the code has one. A module or class body stores it in `__doc__`; a def keeps it as
    its first constant (through 3.13; 3.14 flags it with CO_HAS_DOCSTRING)."""
    if code.co_name == "<module>" or not code.co_flags & inspect.CO_NEWLOCALS:
        prev = None
        for ins in dis.get_instructions(code):
            if ins.opname == "STORE_NAME" and ins.argval == "__doc__" and prev is not None:
                return prev.argval if prev.opname == "LOAD_CONST" else None
            prev = ins
        return None
    if code.co_name.startswith("<") or not code.co_consts or not isinstance(code.co_consts[0], str):
        return None
    flag = getattr(inspect, "CO_HAS_DOCSTRING", None)
    if flag is not None and not code.co_flags & flag:
        return None
    return code.co_consts[0]


def names(obj) -> set[str]:
    out: set[str] = set()
    for code in _codes(code_of(obj)):
        out.update(code.co_names)
        out.update(code.co_varnames)
        out.update(code.co_freevars)
        out.update(code.co_cellvars)
    return out


def _flat(const) -> Iterator:
    """A constant, or the items of a constant tuple or frozenset (a literal set or tuple); code objects left out."""
    if isinstance(const, (tuple, frozenset)):
        for item in const:
            yield from _flat(item)
    elif not isinstance(const, types.CodeType):
        yield const


def constants(obj) -> list:
    """Every constant the code holds (folded: `10**9` is 1000000000), docstrings left out."""
    out: list = []
    for code in _codes(code_of(obj)):
        doc = _docstring(code)
        for const in code.co_consts:
            if const is not doc:
                out.extend(_flat(const))
    return out


def strings(obj) -> list[str]:
    return [c for c in constants(obj) if isinstance(c, str)]


def const_args(obj, attr: str) -> list[tuple]:
    """For each call `<x>.attr(c1, c2, ...)`, the leading constant positional arguments: `row.get("id")` gives
    `("id",)`."""
    out: list[tuple] = []
    for code in _codes(code_of(obj)):
        ins = list(dis.get_instructions(code))
        for i, op in enumerate(ins):
            if op.opname in ("LOAD_ATTR", "LOAD_METHOD") and op.argval == attr:
                args = []
                for nxt in ins[i + 1:]:
                    if nxt.opname != "LOAD_CONST":
                        break
                    args.append(nxt.argval)
                out.append(tuple(args))
    return out


def tokens(obj) -> list[str]:
    """Names and string constants in instruction order; a nested code object's tokens where it is made."""
    out: list[str] = []
    code = code_of(obj)
    for ins in dis.get_instructions(code):
        if ins.opname in _NAME_OPS and isinstance(ins.argval, str):
            out.append(ins.argval)
        elif ins.opname in ("LOAD_CONST", "RETURN_CONST"):
            if isinstance(ins.argval, str):
                out.append(ins.argval)
            elif isinstance(ins.argval, types.CodeType):
                out.extend(tokens(ins.argval))
    return out


@functools.lru_cache(maxsize=None)
def module_code(name: str) -> types.CodeType:
    """The module `name` compiled from its file, read once."""
    origin = importlib.util.find_spec(name).origin
    with open(origin, encoding="utf-8") as f:
        return compile(f.read(), origin, "exec")


def module_names(name: str) -> set[str]:
    return names(module_code(name))


def module_strings(name: str) -> list[str]:
    return strings(module_code(name))
