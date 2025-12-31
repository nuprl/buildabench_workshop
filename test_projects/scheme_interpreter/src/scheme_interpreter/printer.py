# Copyright (c) 2025 Northeastern University
# Printer for Scheme-like expressions.

from __future__ import annotations

from typing import Any


def to_string(expression: Any) -> str:
    if isinstance(expression, bool):
        return "#t" if expression else "#f"
    if isinstance(expression, list):
        return "(" + " ".join(to_string(item) for item in expression) + ")"
    if isinstance(expression, float):
        if expression.is_integer():
            return str(int(expression))
    return str(expression)
