"""Safe-expression grammar tests: allowed constructs + unsafe rejections."""

from __future__ import annotations

import pytest

from engine.expression import UnsafeExpressionError, compile_expression, evaluate

RECORD = {"amount": 150, "status": "paid", "customer_id": "c1", "flag": True, "nick": None}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("amount > 100", True),
        ("amount > 1000", False),
        ("amount >= 150", True),
        ("status == 'paid'", True),
        ("status != 'paid'", False),
        ("amount > 100 and status == 'paid'", True),
        ("amount > 100 and status == 'x'", False),
        ("amount < 10 or status == 'paid'", True),
        ("not (amount < 10)", True),
        ("customer_id in ['c1', 'c2']", True),
        ("customer_id not in ['c9']", True),
        ("nick is None", True),
        ("status is not None", True),
        ("amount + 50 > 150", True),
        ("amount * 2 == 300", True),
        ("-amount < 0", True),
        ("True", True),
        ("missing == None", True),  # missing fields evaluate to None
        ("missing > 100", False),  # ordering vs None never matches
        ("'150' == 150", True),  # numeric-string coercion
        ("amount == '150'", True),
    ],
)
def test_allowed_expressions(expr: str, expected: bool) -> None:
    assert evaluate(expr, RECORD) is expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('x')",
        "open('/etc/passwd').read()",
        "amount.__class__",
        "record['amount']",
        "len(status) > 1",
        "lambda x: x",
        "[x for x in [1]]",
        "eval('1')",
        "amount | 1",
        "amount & 1",
        "",
        "   ",
        "amount >",
        "amount is 5",
    ],
)
def test_unsafe_or_invalid_expressions_rejected(expr: str) -> None:
    with pytest.raises(UnsafeExpressionError):
        compile_expression(expr)
    with pytest.raises(UnsafeExpressionError):
        evaluate(expr, RECORD)
