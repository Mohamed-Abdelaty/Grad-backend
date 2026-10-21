"""Safe expression evaluator for the ``filter`` transform.

Restricted, documented grammar (no ``eval()`` anywhere):

- literals: numbers, strings, booleans, ``None``
- record fields referenced by bare name, e.g. ``amount > 100``
- comparisons: ``== != < <= > >=``, plus ``in`` / ``not in`` against
  list/tuple literals, e.g. ``status in ["ok", "paid"]``
- boolean logic: ``and``, ``or``, ``not``
- arithmetic: ``+ - * / %`` and unary ``-``
- ``is None`` / ``is not None`` checks

Anything else (function calls, attribute access, subscripts, lambdas,
comprehensions, imports, dunder access) raises :class:`UnsafeExpressionError`.

Missing fields evaluate to ``None`` (so ``missing == None`` is true and
ordering comparisons against ``None`` are false rather than crashing).
"""

from __future__ import annotations

import ast
import functools
from typing import Any


class UnsafeExpressionError(ValueError):
    """Raised when a filter expression uses constructs outside the grammar."""


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
_ALLOWED_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)
_MAX_EXPR_DEPTH = 50


@functools.lru_cache(maxsize=1024)
def compile_expression(expression: str) -> ast.Expression:
    """Parse ``expression`` and reject disallowed syntax eagerly."""
    if not expression or not expression.strip():
        raise UnsafeExpressionError("Filter expression must not be empty")
    if len(expression) > 10000:
        raise UnsafeExpressionError("Expression is too long (maximum 10,000 characters)")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Invalid filter expression: {exc}") from exc
    _check_node(tree, depth=0)
    return tree


def evaluate(expression: str, record: dict[str, Any]) -> bool:
    """Evaluate ``expression`` against ``record``; missing fields are ``None``."""
    tree = compile_expression(expression)
    return bool(_eval(tree.body, record))


def _check_node(node: ast.AST, depth: int = 0) -> None:
    """Recursively reject AST nodes outside the allow-list with depth protection."""
    if depth > _MAX_EXPR_DEPTH:
        raise UnsafeExpressionError(f"Expression exceeds maximum nesting depth of {_MAX_EXPR_DEPTH}")
    if isinstance(node, ast.Expression):
        _check_node(node.body, depth + 1)
    elif isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise UnsafeExpressionError(f"Operator not allowed: {type(node.op).__name__}")
        for value in node.values:
            _check_node(value, depth + 1)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.Not, ast.USub)):
            raise UnsafeExpressionError(f"Unary operator not allowed: {type(node.op).__name__}")
        _check_node(node.operand, depth + 1)
    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise UnsafeExpressionError(f"Operator not allowed: {type(node.op).__name__}")
        _check_node(node.left, depth + 1)
        _check_node(node.right, depth + 1)
    elif isinstance(node, ast.Compare):
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            if isinstance(op, (ast.Is, ast.IsNot)):
                if not (isinstance(comparator, ast.Constant) and comparator.value is None):
                    raise UnsafeExpressionError("'is' comparison is only allowed against None")
                continue
            if not isinstance(op, _ALLOWED_CMPOPS):
                raise UnsafeExpressionError(f"Comparison not allowed: {type(op).__name__}")
        _check_node(node.left, depth + 1)
        for comparator in node.comparators:
            _check_node(comparator, depth + 1)
    elif isinstance(node, ast.Name):
        if node.id in {"True", "False", "None"}:
            return
        # field reference: allowed (resolved from the record at eval time)
    elif isinstance(node, ast.Constant):
        pass
    elif isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            _check_node(elt, depth + 1)
    else:
        raise UnsafeExpressionError(f"Expression construct not allowed: {type(node).__name__}")


def _eval(node: ast.AST, record: dict[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for value in node.values:
                result = _eval(value, record)
                if not result:
                    return False
            return result
        result = False
        for value in node.values:
            result = _eval(value, record)
            if result:
                return True
        return result
    if isinstance(node, ast.UnaryOp):
        val = _eval(node.operand, record)
        if isinstance(node.op, ast.Not):
            return not val
        if val is None:
            return None
        try:
            return -val
        except TypeError:
            return None
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, record)
        right = _eval(node.right, record)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return None if right == 0 else left / right
            if isinstance(node.op, ast.Mod):
                return None if right == 0 else left % right
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return None
    if isinstance(node, ast.Compare):
        left = _eval(node.left, record)
        for op, comparator_node in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator_node, record)
            if isinstance(op, (ast.Is, ast.IsNot)):
                if (
                    not isinstance(comparator_node, ast.Constant)
                    or comparator_node.value is not None
                ):
                    raise UnsafeExpressionError("'is' comparison is only allowed against None")
                matched = left is None
                if isinstance(op, ast.IsNot):
                    matched = not matched
                if not matched:
                    return False
            else:
                if not _compare(op, left, right):
                    return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id == "None":
            return None
        return record.get(node.id)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval(e, record) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(e, record) for e in node.elts)
    raise UnsafeExpressionError(f"Expression construct not allowed: {type(node).__name__}")


def _try_number(value: Any) -> tuple[bool, float]:
    """Try to interpret ``value`` as a number (numeric strings coerce)."""
    if isinstance(value, bool):
        return False, 0.0
    if isinstance(value, (int, float)):
        return True, float(value)
    if isinstance(value, str):
        try:
            return True, float(value.strip())
        except ValueError:
            return False, 0.0
    return False, 0.0


def _compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    # Numeric coercion: CSV/JSON sources often produce strings, while the
    # task's canonical example filters `amount > 100` directly on CSV data.
    # When one (or both) side is a numeric string, compare numerically.
    if not isinstance(op, (ast.In, ast.NotIn)):
        left_is_num, left_num = _try_number(left)
        right_is_num, right_num = _try_number(right)
        if left_is_num and right_is_num:
            left, right = left_num, right_num
    try:
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.In):
            return left in right
        if isinstance(op, ast.NotIn):
            return left not in right
        # Ordering with None never matches (avoids TypeError on missing fields).
        if left is None or right is None:
            return False
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Gt):
            return left > right
        return left >= right  # GtE
    except TypeError:
        return False
