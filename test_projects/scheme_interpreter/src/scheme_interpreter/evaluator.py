# Copyright (c) 2025 Northeastern University
# Evaluator and main execution loop for Scheme-like expressions.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import SchemeError
from .parser import parse_many


@dataclass
class Env:
    values: dict[str, Any]
    outer: "Env | None" = None

    def find(self, name: str) -> "Env":
        if name in self.values:
            return self
        if self.outer is None:
            raise SchemeError(f"unbound symbol: {name}")
        return self.outer.find(name)


@dataclass
class Procedure:
    params: list[str]
    body: Any
    env: Env

    def __call__(self, *args: Any) -> Any:
        if len(args) != len(self.params):
            raise SchemeError("arity mismatch")
        local = Env(dict(zip(self.params, args)), self.env)
        return eval_expr(self.body, local)


def standard_env() -> Env:
    def _variadic_sum(*items: Any) -> Any:
        if not items:
            return 0
        return sum(items)

    def _variadic_product(*items: Any) -> Any:
        result = 1
        for item in items:
            result *= item
        return result

    def _minus(*items: Any) -> Any:
        if not items:
            raise SchemeError("'-' expects at least one argument")
        if len(items) == 1:
            return -items[0]
        head, *tail = items
        result = head
        for item in tail:
            result -= item
        return result

    def _divide(*items: Any) -> Any:
        if not items:
            raise SchemeError("'/' expects at least one argument")
        head, *tail = items
        if not tail:
            return 1 / head
        result = head
        for item in tail:
            result /= item
        return result

    def _comparison_chain(op):
        def _inner(*items: Any) -> bool:
            if len(items) < 2:
                return True
            return all(op(a, b) for a, b in zip(items, items[1:]))

        return _inner

    builtins: dict[str, Any] = {
        "+": _variadic_sum,
        "*": _variadic_product,
        "-": _minus,
        "/": _divide,
        ">": _comparison_chain(lambda a, b: a > b),
        "<": _comparison_chain(lambda a, b: a < b),
        ">=": _comparison_chain(lambda a, b: a >= b),
        "<=": _comparison_chain(lambda a, b: a <= b),
        "=": _comparison_chain(lambda a, b: a == b),
    }
    return Env(builtins)


def eval_expr(expression: Any, env: Env | None = None) -> Any:
    if env is None:
        env = standard_env()
    if isinstance(expression, str):
        return env.find(expression).values[expression]
    if not isinstance(expression, list):
        return expression
    if not expression:
        raise SchemeError("cannot evaluate empty list")

    head, *rest = expression
    if head == "if":
        if len(rest) != 3:
            raise SchemeError("if expects 3 arguments")
        test, conseq, alt = rest
        branch = conseq if eval_expr(test, env) else alt
        return eval_expr(branch, env)
    if head == "lambda":
        if len(rest) != 2:
            raise SchemeError("lambda expects parameters and body")
        params, body = rest
        if not isinstance(params, list) or not all(isinstance(p, str) for p in params):
            raise SchemeError("lambda parameters must be symbols")
        return Procedure(list(params), body, env)
    if head == "quote":
        if len(rest) != 1:
            raise SchemeError("quote expects 1 argument")
        return rest[0]

    proc = eval_expr(head, env)
    args = [eval_expr(arg, env) for arg in rest]
    if not callable(proc):
        raise SchemeError("first element is not callable")
    return proc(*args)


def run(source: str, env: Env | None = None) -> Any:
    env = env or standard_env()
    result: Any = None
    for expr in parse_many(source):
        result = eval_expr(expr, env)
    return result
