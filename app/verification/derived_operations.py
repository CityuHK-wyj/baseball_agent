"""Bounded governed-operation resolution for derived computations.

Syntax alone is not evidence. A program that reads a factual operand and then returns
an unrelated constant has syntactic operand access but no semantic output dependency.
This module resolves a validated program's *output expressions* into the small,
governed analytical operations the system actually supports, over exact factual
operand cells, and recomputes the expected value so the Judge can compare it with the
recorded result.

It deliberately does not attempt general Python program analysis. Only bounded,
recognizable governed operations are proven: pass-through of an operand cell,
difference, ratio, sum, mean, product, min/max, ranking and aggregation. Anything
else fails closed and the output stays non-authoritative candidate material.

Inputs are reconstructed from the derived artifact's recorded operand binding map, so
no value is taken from the untrusted program itself. Literal constants are never
factual operands.
"""

from __future__ import annotations

import ast
from typing import Any

_UNKNOWN = object()


# ------------------------------------------------------------------ symbols
#
# Every resolved expression is a small tuple with a leading kind tag. The tags are the
# only vocabulary this module understands; unknown nodes degrade to ("unknown",).

_BIN_OPS = {
    ast.Add: "Add", ast.Sub: "Sub", ast.Mult: "Mult", ast.Div: "Div",
    ast.Mod: "Mod", ast.Pow: "Pow", ast.FloorDiv: "FloorDiv",
}
_UNARY_OPS = {ast.USub: "USub", ast.UAdd: "UAdd", ast.Not: "Not", ast.Invert: "Invert"}

_AGG_CALLS = {
    "sum": "SUM", "min": "MIN", "max": "MAX",
    "statistics.mean": "MEAN", "mean": "MEAN",
    "statistics.median": "MEDIAN", "median": "MEDIAN",
    "len": "COUNT", "abs": "ABSOLUTE", "round": "ROUND",
    "float": "CAST", "int": "CAST",
}

_SUPPORTED_OPERATIONS = {
    "PASSTHROUGH", "DIFFERENCE", "RATIO", "SUM", "MEAN", "MEDIAN", "PRODUCT",
    "MIN", "MAX", "RANK", "COUNT", "ABSOLUTE", "ROUND", "NEGATION", "MODULO", "POWER",
}


def _unknown():
    return ("unknown",)


def _const(value):
    return ("const", value)


# ------------------------------------------------------------------ resolution

def _resolve(node: ast.AST, env: dict, inputs: dict) -> tuple:
    if isinstance(node, ast.Constant):
        return _const(node.value)
    if isinstance(node, ast.Name):
        if node.id == "inputs":
            return ("inputs",)
        return env.get(node.id, _unknown())
    if isinstance(node, ast.Subscript):
        base = _resolve(node.value, env, inputs)
        return _resolve_subscript(base, node.slice, env, inputs)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in {"statistics", "math", "decimal"}:
            return ("func", f"{node.value.id}.{node.attr}")
        return _unknown()
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            return _unknown()
        return _fold_binop(op, _resolve(node.left, env, inputs), _resolve(node.right, env, inputs))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        operand = _resolve(node.operand, env, inputs)
        if op is None:
            return _unknown()
        if op in {"USub", "UAdd"} and operand[0] == "const" and isinstance(operand[1], (int, float)) and not isinstance(operand[1], bool):
            return _const(-operand[1] if op == "USub" else +operand[1])
        return ("unop", op, operand)
    if isinstance(node, ast.BoolOp):
        values = tuple(_resolve(v, env, inputs) for v in node.values)
        if all(v[0] == "const" for v in values):
            truth = all(bool(v[1]) for v in values) if isinstance(node.op, ast.And) else any(bool(v[1]) for v in values)
            return _const(truth)
        return ("boolop", "And" if isinstance(node.op, ast.And) else "Or", values)
    if isinstance(node, ast.Compare):
        return ("compare", tuple(_resolve(c, env, inputs) for c in node.comparators),
                tuple(type(op).__name__ for op in node.ops), _resolve(node.left, env, inputs))
    if isinstance(node, ast.IfExp):
        test = _resolve(node.test, env, inputs)
        if test[0] == "const":
            return _resolve(node.body if bool(test[1]) else node.orelse, env, inputs)
        body = _resolve(node.body, env, inputs)
        orelse = _resolve(node.orelse, env, inputs)
        return body if body == orelse else _unknown()
    if isinstance(node, (ast.List, ast.Tuple)):
        return ("list", tuple(_resolve(e, env, inputs) for e in node.elts))
    if isinstance(node, ast.Dict):
        pairs = []
        for key, value in zip(node.keys, node.values):
            if key is None:
                return _unknown()
            pairs.append((_resolve(key, env, inputs), _resolve(value, env, inputs)))
        return ("dict", tuple(pairs))
    if isinstance(node, ast.Call):
        return _resolve_call(node, env, inputs)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _resolve_comprehension(node, env, inputs)
    if isinstance(node, ast.Lambda):
        return ("lambda", tuple(a.arg for a in node.args.args), node.body)
    if isinstance(node, ast.JoinedStr):
        return ("joined", tuple(_resolve(v, env, inputs) for v in node.values))
    if isinstance(node, ast.Slice):
        return ("slice", _resolve(node.lower, env, inputs) if node.lower else None,
                _resolve(node.upper, env, inputs) if node.upper else None)
    return _unknown()


def _resolve_subscript(base: tuple, slice_node: ast.AST, env: dict, inputs: dict) -> tuple:
    if base[0] == "inputs":
        if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
            return ("input", slice_node.value)
        return _unknown()
    if base[0] == "input":
        if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int) and not isinstance(slice_node.value, bool):
            return ("row", base[1], slice_node.value)
        return _unknown()
    if base[0] == "row":
        if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
            return ("operand", base[1], base[2], slice_node.value)
        return _unknown()
    if base[0] == "list":
        index = _const_index(slice_node, env, inputs)
        items = base[1]
        if index is not None and -len(items) <= index < len(items):
            return items[index]
        return _unknown()
    if base[0] == "dict":
        key = _resolve(slice_node, env, inputs)
        if key[0] == "const":
            for k, v in base[1]:
                if k[0] == "const" and k[1] == key[1]:
                    return v
        return _unknown()
    return _unknown()


def _const_index(node: ast.AST, env: dict, inputs: dict):
    value = _resolve(node, env, inputs)
    if value[0] == "const" and isinstance(value[1], int) and not isinstance(value[1], bool):
        return value[1]
    return None


def _fold_binop(op: str, left: tuple, right: tuple) -> tuple:
    if left[0] == "unknown" or right[0] == "unknown":
        return _unknown()
    if left[0] == "const" and right[0] == "const":
        try:
            return _const(_apply_binop(op, left[1], right[1]))
        except Exception:  # noqa: BLE001 - fold failure is simply not literal
            return _unknown()
    if op == "Add" and left[0] == "list" and right[0] == "list":
        return ("list", left[1] + right[1])
    return ("binop", op, left, right)


def _apply_binop(op: str, a, b):
    if op == "Add":
        return a + b
    if op == "Sub":
        return a - b
    if op == "Mult":
        return a * b
    if op == "Div":
        return a / b
    if op == "FloorDiv":
        return a // b
    if op == "Mod":
        return a % b
    if op == "Pow":
        return a ** b
    raise ValueError(op)


def _resolve_call(node: ast.Call, env: dict, inputs: dict) -> tuple:
    func = node.func
    args = tuple(_resolve(a, env, inputs) for a in node.args)
    kwargs = tuple(sorted((kw.arg or "", _resolve(kw.value, env, inputs)) for kw in node.keywords))
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "inputs":
        if func.attr in {"values", "items", "keys"}:
            return ("input_collection", func.attr)
        return _unknown()
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in {"statistics", "math", "decimal"}:
        return ("call", f"{func.value.id}.{func.attr}", args, kwargs)
    if isinstance(func, ast.Name):
        name = func.id
        if name == "sorted":
            return _resolve_sorted(args, kwargs, env)
        return ("call", name, args, kwargs)
    return _unknown()


def _resolve_sorted(args: tuple, kwargs: tuple, env: dict) -> tuple:
    if not args:
        return _unknown()
    key_field = None
    key_sym = dict(kwargs).get("key")
    if key_sym is not None and key_sym[0] == "lambda":
        key_field = _key_field(key_sym[2], set(key_sym[1]), env)
    reverse = False
    reverse_sym = dict(kwargs).get("reverse")
    if reverse_sym is not None and reverse_sym[0] == "const" and isinstance(reverse_sym[1], bool):
        reverse = reverse_sym[1]
    return ("rank", args[0], key_field, reverse)


def _key_field(body: ast.AST, params: set, env: dict):
    """Extract ``row["field"]``-style ordering keys from a sort lambda."""
    node = body
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        root = node.value
        if isinstance(root, ast.Name) and root.id in params:
            return node.slice.value
        # row[0]["field"] over a single-column tuple
        if isinstance(root, ast.Subscript) and isinstance(root.value, ast.Name) and root.value.id in params:
            return node.slice.value
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _resolve_comprehension(node: ast.AST, env: dict, inputs: dict) -> tuple:
    results: list[tuple] = []

    def walk(index: int, local: dict) -> None:
        if index == len(node.generators):
            results.append(_resolve(node.elt, local, inputs))
            return
        gen = node.generators[index]
        items = _iter_items(_resolve(gen.iter, local, inputs), inputs)
        if items is None:
            results.append(_unknown())
            return
        for item in items:
            branch = dict(local)
            _bind_target(gen.target, item, branch)
            if not _ifs_pass(gen.ifs, branch, inputs):
                continue
            walk(index + 1, branch)

    walk(0, dict(env))
    if isinstance(node, ast.SetComp):
        return ("list", tuple(dict.fromkeys(results)))
    return ("list", tuple(results))


def _ifs_pass(ifs, env: dict, inputs: dict) -> bool:
    for condition in ifs:
        resolved = _resolve(condition, env, inputs)
        if resolved[0] == "const" and not bool(resolved[1]):
            return False
    return True


def _bind_target(target: ast.AST, value: tuple, env: dict) -> None:
    if isinstance(target, ast.Name):
        env[target.id] = value
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        items = value[1] if value[0] == "list" else ()
        for index, element in enumerate(target.elts):
            _bind_target(element, items[index] if index < len(items) else _unknown(), env)


def _iter_items(sym: tuple, inputs: dict):
    if sym[0] == "list":
        return sym[1]
    if sym[0] == "input":
        rows = inputs.get(sym[1])
        if isinstance(rows, list):
            return tuple(("row", sym[1], index) for index in range(len(rows)))
        return ()
    if sym[0] == "input_collection":
        if sym[1] == "values":
            return tuple(("input", name) for name in inputs)
        if sym[1] == "items":
            return tuple(("list", (("const", name), ("input", name))) for name in inputs)
        if sym[1] == "keys":
            return tuple(("const", name) for name in inputs)
        return None
    if sym[0] == "binop" and sym[1] == "Add":
        left = _iter_items(sym[2], inputs)
        right = _iter_items(sym[3], inputs)
        if left is not None and right is not None:
            return left + right
    return None


# ------------------------------------------------------------------ statements

def _build_env(program: str, inputs: dict) -> dict:
    tree = ast.parse(program)
    env: dict = {}
    _exec_body(tree.body, env, inputs)
    return env


def _exec_body(body, env: dict, inputs: dict) -> None:
    for stmt in body:
        if isinstance(stmt, ast.Assign):
            value = _resolve(stmt.value, env, inputs)
            for target in stmt.targets:
                _bind_target(target, value, env)
        elif isinstance(stmt, ast.AugAssign):
            op = _BIN_OPS.get(type(stmt.op))
            if op is None:
                continue
            left = _resolve(stmt.target, env, inputs)
            right = _resolve(stmt.value, env, inputs)
            _bind_target(stmt.target, _fold_binop(op, left, right), env)
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value is not None:
                _bind_target(stmt.target, _resolve(stmt.value, env, inputs), env)
        elif isinstance(stmt, ast.For):
            items = _iter_items(_resolve(stmt.iter, env, inputs), inputs)
            if items is None:
                continue
            for item in items:
                local = dict(env)
                _bind_target(stmt.target, item, local)
                _exec_body(stmt.body, local, inputs)
                env.update(local)
        elif isinstance(stmt, ast.If):
            test = _resolve(stmt.test, env, inputs)
            if test[0] == "const":
                _exec_body(stmt.body if bool(test[1]) else stmt.orelse, env, inputs)
            else:
                left = dict(env)
                _exec_body(stmt.body, left, inputs)
                right = dict(env)
                _exec_body(stmt.orelse, right, inputs)
                for key in set(left) | set(right):
                    env[key] = left.get(key) if left.get(key) == right.get(key) else _unknown()
        elif isinstance(stmt, ast.Try):
            _exec_body(stmt.body, env, inputs)
        elif isinstance(stmt, ast.FunctionDef):
            env[stmt.name] = ("funcdef", stmt)
        elif isinstance(stmt, ast.Expr):
            # A bare expression is a statement, not an output.
            continue


# ------------------------------------------------------------------ dependency

def _deps(sym: tuple, inputs: dict) -> set:
    kind = sym[0]
    if kind == "operand":
        return {(sym[1], sym[2], sym[3])} if sym[1] in inputs else set()
    if kind == "row":
        return {(sym[1], sym[2], None)} if sym[1] in inputs else set()
    if kind == "input":
        rows = inputs.get(sym[1])
        count = len(rows) if isinstance(rows, list) else 0
        return {(sym[1], index, None) for index in range(count)} or {("input", sym[1], None)}
    if kind == "input_collection":
        return {("input", name, None) for name in inputs}
    deps: set = set()
    for child in _children(sym):
        deps |= _deps(child, inputs)
    return deps


def _children(sym: tuple):
    kind = sym[0]
    if kind in {"binop", "unop"}:
        return [child for child in sym[2:] if isinstance(child, tuple)]
    if kind == "call":
        return [child for child in sym[2] if isinstance(child, tuple)] + [v for _, v in sym[3]]
    if kind == "list":
        return list(sym[1])
    if kind == "dict":
        return [value for _, value in sym[1]]
    if kind == "rank":
        return [sym[1], sym[2]] if isinstance(sym[2], tuple) else [sym[1]]
    if kind == "boolop":
        return list(sym[2])
    if kind == "compare":
        return [sym[3], *sym[1]]
    if kind == "joined":
        return list(sym[1])
    return []


def _is_factual(sym: tuple, inputs: dict) -> bool:
    kind = sym[0]
    if kind in {"operand", "row", "input"}:
        return sym[1] in inputs
    if kind == "input_collection":
        return bool(inputs)
    if kind == "const":
        return sym[1] in _operand_scalars(inputs)
    if kind in {"binop", "unop", "call", "rank", "boolop", "compare", "joined"}:
        return bool(_deps(sym, inputs))
    if kind == "list":
        return all(_is_factual(item, inputs) for item in sym[1])
    if kind == "dict":
        return all(_is_factual(value, inputs) for _, value in sym[1])
    return False


def _operand_scalars(inputs: dict) -> set:
    values: set = set()
    stack = list(inputs.values())
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
        elif isinstance(current, (str, int, float, bool)) and current not in values:
            values.add(current)
    return values


def _is_row_source(sym: tuple, inputs: dict) -> bool:
    """True when a ranking iterable is made only of rows from the declared operands."""
    if sym[0] == "input":
        return sym[1] in inputs
    if sym[0] in {"row", "operand"}:
        return True
    if sym[0] == "list":
        return bool(sym[1]) and all(_is_row_source(item, inputs) for item in sym[1])
    if sym[0] == "binop" and sym[1] == "Add":
        return _is_row_source(sym[2], inputs) and _is_row_source(sym[3], inputs)
    return False


# ------------------------------------------------------------------ evaluation

def _evaluate(sym: tuple, inputs: dict):
    kind = sym[0]
    if kind == "const":
        return sym[1]
    if kind == "operand":
        rows = inputs.get(sym[1])
        if isinstance(rows, list) and 0 <= sym[2] < len(rows) and isinstance(rows[sym[2]], dict) and sym[3] in rows[sym[2]]:
            return rows[sym[2]][sym[3]]
        return _UNKNOWN
    if kind == "row":
        rows = inputs.get(sym[1])
        if isinstance(rows, list) and 0 <= sym[2] < len(rows):
            return rows[sym[2]]
        return _UNKNOWN
    if kind == "input":
        rows = inputs.get(sym[1])
        return rows if isinstance(rows, list) else _UNKNOWN
    if kind == "input_collection":
        if sym[1] == "values":
            return [value for value in inputs.values()]
        if sym[1] == "keys":
            return list(inputs.keys())
        if sym[1] == "items":
            return [[name, value] for name, value in inputs.items()]
        return _UNKNOWN
    if kind == "list":
        values = [_evaluate(item, inputs) for item in sym[1]]
        return _UNKNOWN if any(v is _UNKNOWN for v in values) else values
    if kind == "dict":
        result = {}
        for key, value in sym[1]:
            evaluated_key = _evaluate(key, inputs)
            evaluated_value = _evaluate(value, inputs)
            if evaluated_key is _UNKNOWN or evaluated_value is _UNKNOWN:
                return _UNKNOWN
            result[evaluated_key] = evaluated_value
        return result
    if kind == "binop":
        left = _evaluate(sym[2], inputs)
        right = _evaluate(sym[3], inputs)
        if left is _UNKNOWN or right is _UNKNOWN:
            return _UNKNOWN
        try:
            return _apply_binop(sym[1], left, right)
        except Exception:  # noqa: BLE001
            return _UNKNOWN
    if kind == "unop":
        value = _evaluate(sym[2], inputs)
        if value is _UNKNOWN:
            return _UNKNOWN
        if sym[1] == "USub":
            return -value
        if sym[1] == "UAdd":
            return +value
        return _UNKNOWN
    if kind == "call":
        return _evaluate_call(sym, inputs)
    if kind == "rank":
        values = _evaluate(sym[1], inputs)
        if values is _UNKNOWN or not isinstance(values, list):
            return _UNKNOWN
        try:
            if sym[2] is None:
                return sorted(values, reverse=bool(sym[3]))
            return sorted(values, key=lambda row: row.get(sym[2]) if isinstance(row, dict) else None, reverse=bool(sym[3]))
        except Exception:  # noqa: BLE001
            return _UNKNOWN
    return _UNKNOWN


def _evaluate_call(sym: tuple, inputs: dict):
    qual = sym[1]
    args = [_evaluate(arg, inputs) for arg in sym[2]]
    if any(arg is _UNKNOWN for arg in args):
        return _UNKNOWN
    kwargs = {name: _evaluate(value, inputs) for name, value in sym[3]}
    try:
        if qual == "sum":
            return sum(args[0])
        if qual == "min":
            return min(args[0]) if len(args) == 1 else min(args)
        if qual == "max":
            return max(args[0]) if len(args) == 1 else max(args)
        if qual == "len":
            return len(args[0])
        if qual == "abs":
            return abs(args[0])
        if qual == "round":
            return round(*args, **kwargs)
        if qual in {"statistics.mean", "mean"}:
            return float(sum(args[0]) / len(args[0]))
        if qual in {"statistics.median", "median"}:
            import statistics as _statistics
            return _statistics.median(args[0])
        if qual == "float":
            return float(args[0])
        if qual == "int":
            return int(args[0])
        if qual in {"list", "tuple", "set"}:
            return {"list": list, "tuple": tuple, "set": set}[qual](args[0])
        if qual == "sorted":
            return sorted(args[0], **kwargs)
    except Exception:  # noqa: BLE001
        return _UNKNOWN
    return _UNKNOWN


# ------------------------------------------------------------------ operations

def operation_of(sym: tuple) -> str:
    kind = sym[0]
    if kind == "binop":
        op = sym[1]
        if op == "Sub":
            return "DIFFERENCE"
        if op == "Div":
            return "MEAN" if _is_mean_shape(sym) else "RATIO"
        if op == "Mult":
            return "PRODUCT"
        if op == "Add":
            return "SUM"
        if op == "Mod":
            return "MODULO"
        if op == "Pow":
            return "POWER"
        if op == "FloorDiv":
            return "FLOOR_DIVISION"
        return "UNSUPPORTED"
    if kind == "call":
        return _AGG_CALLS.get(sym[1], "UNSUPPORTED")
    if kind == "rank":
        return "RANK"
    if kind in {"operand", "row"}:
        return "PASSTHROUGH"
    if kind == "unop":
        return "NEGATION" if sym[1] == "USub" else "UNSUPPORTED"
    if kind == "const":
        return "CONSTANT"
    return "UNSUPPORTED"


def _is_mean_shape(sym: tuple) -> bool:
    if sym[0] != "binop" or sym[1] != "Div":
        return False
    left, right = sym[2], sym[3]
    return (left[0] == "call" and left[1] == "sum" and right[0] == "call" and right[1] == "len"
            and bool(left[2]) and bool(right[2]) and left[2][0] == right[2][0])


# ------------------------------------------------------------------ fields

def _dict_pairs(sym: tuple):
    pairs = sym[1]
    result = []
    for key, value in pairs:
        if key[0] == "const" and isinstance(key[1], str):
            result.append((key[1], value))
        else:
            return None
    return result


def _output_fields(output_sym: tuple):
    if output_sym[0] == "dict":
        return _dict_pairs(output_sym)
    if output_sym[0] == "list":
        items = output_sym[1]
        if items and all(item[0] == "dict" for item in items):
            fields: dict = {}
            for item in items:
                pairs = _dict_pairs(item)
                if pairs is None:
                    return None
                for name, value in pairs:
                    if name in fields and fields[name] != value:
                        fields[name] = _unknown()
                    else:
                        fields[name] = value
            return list(fields.items())
    return None


# ------------------------------------------------------------------ public API

class DerivedAnalysis:
    def __init__(self, *, ok: bool, reasons: list[str], operations: set[str], operands: list[tuple]):
        self.ok = ok
        self.reasons = reasons
        self.operations = operations
        self.operands = operands


def _recorded_field(rows, field):
    if not rows or not all(isinstance(row, dict) for row in rows):
        return _UNKNOWN
    values = [row.get(field, _UNKNOWN) for row in rows]
    if any(value is _UNKNOWN for value in values):
        return _UNKNOWN
    if all(_values_match(values[0], value) for value in values):
        return values[0]
    return _UNKNOWN


def _values_match(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def analyze_output(program: str, inputs: dict, rows) -> DerivedAnalysis:
    """Resolve the program's output to governed operations over exact operand cells."""
    if not isinstance(inputs, dict) or not inputs:
        return DerivedAnalysis(ok=False, reasons=["no inspectable operand binding map"], operations=set(), operands=[])
    try:
        env = _build_env(program, inputs)
    except SyntaxError:
        return DerivedAnalysis(ok=False, reasons=["program is not inspectable"], operations=set(), operands=[])
    output_sym = env.get("output")
    if output_sym is None:
        return DerivedAnalysis(ok=False, reasons=["program assigns no output"], operations=set(), operands=[])

    reasons: list[str] = []
    operations: set[str] = set()
    operands: list[tuple] = []

    if output_sym[0] == "rank" or (output_sym[0] == "list" and output_sym[1] and all(item[0] == "row" for item in output_sym[1])):
        if output_sym[0] == "rank" and not _is_row_source(output_sym[1], inputs):
            return DerivedAnalysis(ok=False, reasons=["ranked output is not made of declared operand rows"],
                                   operations=set(), operands=[])
        evaluated = _evaluate(output_sym, inputs)
        if evaluated is _UNKNOWN or not _values_match(evaluated, list(rows or [])):
            return DerivedAnalysis(ok=False, reasons=["recorded output does not match the ranked operands"],
                                   operations=set(), operands=[])
        operations.add("RANK")
        operands.extend(sorted(_deps(output_sym, inputs)))
        if output_sym[2]:
            # The ordering key is the operand field this ranking depends on.
            operands.append(("__rank_key__", None, output_sym[2]))
        return DerivedAnalysis(ok=True, reasons=[], operations=operations, operands=operands)

    fields = _output_fields(output_sym)
    if not fields:
        return DerivedAnalysis(
            ok=False,
            reasons=["program output is not a governed computation over factual operands"],
            operations=set(), operands=[])

    for name, value in fields:
        if not _is_factual(value, inputs):
            reasons.append(f"output field '{name}' has no factual operand dependency")
            continue
        operation = operation_of(value)
        if operation not in _SUPPORTED_OPERATIONS:
            reasons.append(f"output field '{name}' is not a supported governed operation")
            continue
        expected = _evaluate(value, inputs)
        recorded = _recorded_field(rows, name)
        if expected is _UNKNOWN or recorded is _UNKNOWN or not _values_match(expected, recorded):
            reasons.append(f"output field '{name}' does not match its governed operation over the operands")
            continue
        operations.add(operation)
        operands.extend(sorted(_deps(value, inputs)))

    if reasons:
        return DerivedAnalysis(ok=False, reasons=list(dict.fromkeys(reasons)), operations=set(), operands=[])
    return DerivedAnalysis(ok=True, reasons=[], operations=operations, operands=operands)
