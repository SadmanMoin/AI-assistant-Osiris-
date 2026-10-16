"""Static checks for automation scripts.

Passing this check is required and is not treated as a security boundary by
itself. The runner also executes the script with restricted builtins in a
separate process.
"""

from __future__ import annotations

import ast

from osiris.automation.api import TOOL_PERMISSIONS

_ALLOWED_NODES = {
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.Expr,
    ast.Assign,
    ast.AugAssign,
    ast.If,
    ast.For,
    ast.While,
    ast.Break,
    ast.Continue,
    ast.Pass,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Attribute,
    ast.Name,
    ast.Constant,
    ast.List,
    ast.Dict,
    ast.Tuple,
    ast.Set,
    ast.Subscript,
    ast.Slice,
    ast.ListComp,
    ast.DictComp,
    ast.comprehension,
    ast.IfExp,
    ast.keyword,
    ast.Try,
    ast.ExceptHandler,
    ast.Raise,
    ast.Assert,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Load,
    ast.Store,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
}

_SAFE_CALLS = {
    "len",
    "range",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "tuple",
    "set",
    "enumerate",
    "zip",
    "min",
    "max",
    "sum",
    "sorted",
    "abs",
    "round",
    "isinstance",
    "repr",
    "print",
    "reversed",
    "any",
    "all",
}

_SAFE_NAMES = _SAFE_CALLS | {
    "True",
    "False",
    "None",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "RuntimeError",
}

_SAFE_METHODS = {
    "strip",
    "split",
    "join",
    "lower",
    "upper",
    "startswith",
    "endswith",
    "replace",
    "format",
    "keys",
    "items",
    "values",
    "get",
    "append",
    "update",
    "title",
    "capitalize",
}


class ScriptValidator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.assigned: set[str] = {"tools", "params"}
        self.used_tools: set[str] = set()
        self.has_run = False

    def visit(self, node):
        if type(node) not in _ALLOWED_NODES:
            self.errors.append(f"Disallowed syntax: {type(node).__name__}.")
            return
        super().visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.decorator_list:
            self.errors.append("Decorators are not allowed.")
        if node.name == "run":
            self.has_run = True
            arg_names = [arg.arg for arg in node.args.args]
            if arg_names != ["tools", "params"] or node.args.vararg or node.args.kwarg:
                self.errors.append("The entry point must be run(tools, params).")
        self.assigned.update(arg.arg for arg in node.args.args)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            if node.id.startswith("_"):
                self.errors.append(f"Names starting with '_' are not allowed ({node.id}).")
            self.assigned.add(node.id)
            return
        if node.id not in self.assigned and node.id not in _SAFE_NAMES:
            self.errors.append(f"Unknown name: {node.id}.")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            self.errors.append("Dunder and private attributes are not allowed.")
            return
        chain = _attribute_chain(node)
        if chain and chain[0] == "tools":
            dotted = ".".join(chain[1:])
            if dotted in TOOL_PERMISSIONS:
                self.used_tools.add(dotted)
            elif not any(name.startswith(dotted + ".") for name in TOOL_PERMISSIONS):
                self.errors.append(f"Unknown tool: tools.{dotted}.")
        elif node.attr not in _SAFE_METHODS:
            self.errors.append(f"Method is not allowed: {node.attr}.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id not in _SAFE_CALLS:
            self.errors.append(f"Call is not allowed: {node.func.id}.")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == "tools":
            self.errors.append("Tool access must use attributes, not subscripts.")
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        for generator in node.generators:
            self.visit(generator)
        self.visit(node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        for generator in node.generators:
            self.visit(generator)
        self.visit(node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        for generator in node.generators:
            self.visit(generator)
        self.visit(node.key)
        self.visit(node.value)


def _attribute_chain(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        return parts
    return None


def validate_source(source: str, declared_permissions: list[str] | None = None, *, max_chars: int = 30000) -> dict:
    errors: list[str] = []
    if not isinstance(source, str) or not source.strip():
        return _result(False, ["The script is empty."], [], [])
    if "\x00" in source:
        return _result(False, ["The script contains a null character."], [], [])
    if len(source) > max_chars:
        return _result(False, ["The script is too long."], [], [])
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return _result(False, [f"Syntax error: {error.msg}."], [], [])

    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node, ast.FunctionDef):
            errors.append("Only functions are allowed at the top level.")

    checker = ScriptValidator()
    checker.visit(tree)
    errors.extend(checker.errors)
    if not checker.has_run:
        errors.append("The script must define run(tools, params).")

    required = sorted(
        {
            TOOL_PERMISSIONS[name]
            for name in checker.used_tools
            if TOOL_PERMISSIONS.get(name)
        }
    )
    declared = sorted(set(declared_permissions or []))
    unknown = [name for name in declared if name not in set(TOOL_PERMISSIONS.values())]
    if unknown:
        errors.append("Unknown permissions: " + ", ".join(unknown) + ".")
    missing = [name for name in required if name not in declared]
    if missing:
        errors.append("The script uses tools that are not in its permissions: " + ", ".join(missing) + ".")

    return _result(not errors, errors, required, sorted(checker.used_tools), declared)


def _result(ok: bool, errors: list[str], required: list[str], used: list[str], declared: list[str] | None = None) -> dict:
    return {
        "ok": ok,
        "errors": errors,
        "required_permissions": required,
        "declared_permissions": declared or [],
        "used_tools": used,
    }
