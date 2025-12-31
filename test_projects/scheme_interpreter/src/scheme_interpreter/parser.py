# Copyright (c) 2025 Northeastern University
# S-expression parser for a Scheme-like language.

from __future__ import annotations

from typing import Any

from .errors import SchemeError


def tokenize(source: str) -> list[str]:
    source = source.replace("(", " ( ").replace(")", " ) ")
    return [token for token in source.split() if token]


def parse(source: str) -> Any:
    tokens = tokenize(source)
    if not tokens:
        raise SchemeError("empty input")
    expression = _read_from_tokens(tokens)
    if tokens:
        raise SchemeError(f"unexpected token after expression: {tokens[0]}")
    return expression


def parse_many(source: str) -> list[Any]:
    tokens = tokenize(source)
    expressions: list[Any] = []
    while tokens:
        expressions.append(_read_from_tokens(tokens))
    return expressions


def _read_from_tokens(tokens: list[str]) -> Any:
    if not tokens:
        raise SchemeError("unexpected EOF while reading")
    token = tokens.pop(0)
    if token == "(":
        items: list[Any] = []
        while True:
            if not tokens:
                raise SchemeError("unexpected EOF while reading list")
            if tokens[0] == ")":
                tokens.pop(0)
                return items
            items.append(_read_from_tokens(tokens))
    if token == ")":
        raise SchemeError("unexpected )")
    return _atom(token)


def _atom(token: str) -> Any:
    if token == "#t":
        return True
    if token == "#f":
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token
