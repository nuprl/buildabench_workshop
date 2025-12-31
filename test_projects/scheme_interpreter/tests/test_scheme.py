import pytest

from scheme_interpreter import (
    SchemeError,
    eval_expr,
    parse,
    parse_many,
    run,
    standard_env,
    to_string,
)


def test_parse_numbers_and_symbols():
    assert parse("42") == 42
    assert parse("-7") == -7
    assert parse("3.5") == 3.5
    assert parse("foo") == "foo"


def test_parse_list():
    assert parse("(+ 1 2)") == ["+", 1, 2]


def test_parse_many():
    expressions = parse_many("(+ 1 2) (* 2 3)")
    assert expressions == [["+", 1, 2], ["*", 2, 3]]


def test_to_string_roundtrip():
    expr = parse("(if #t (+ 1 2) (- 5 3))")
    assert to_string(expr) == "(if #t (+ 1 2) (- 5 3))"


def test_arithmetic():
    env = standard_env()
    assert eval_expr(parse("(+ 1 2 3)"), env) == 6
    assert eval_expr(parse("(- 10 3 2)"), env) == 5
    assert eval_expr(parse("(- 5)"), env) == -5
    assert eval_expr(parse("(* 2 3 4)"), env) == 24
    assert eval_expr(parse("(/ 8 2)"), env) == pytest.approx(4)
    assert eval_expr(parse("(/ 2)"), env) == pytest.approx(0.5)


def test_comparisons():
    env = standard_env()
    assert eval_expr(parse("(> 5 3 1)"), env) is True
    assert eval_expr(parse("(> 5 5)"), env) is False
    assert eval_expr(parse("(<= 2 2 3)"), env) is True
    assert eval_expr(parse("(= 4 4 4)"), env) is True


def test_if_expression():
    env = standard_env()
    assert eval_expr(parse("(if #t 1 2)"), env) == 1
    assert eval_expr(parse("(if #f 1 2)"), env) == 2


def test_lambda_application():
    env = standard_env()
    expression = parse("((lambda (x y) (+ x y)) 3 4)")
    assert eval_expr(expression, env) == 7


def test_lambda_closure():
    env = standard_env()
    expression = parse("(((lambda (x) (lambda (y) (+ x y))) 5) 7)")
    assert eval_expr(expression, env) == 12


def test_higher_order_function():
    env = standard_env()
    expression = parse("((lambda (f x) (f x)) (lambda (n) (* n n)) 6)")
    assert eval_expr(expression, env) == 36


def test_variable_shadowing():
    env = standard_env()
    expression = parse("((lambda (x) ((lambda (x) (+ x 1)) 10)) 5)")
    assert eval_expr(expression, env) == 11


def test_closure_outlives_scope():
    env = standard_env()
    expression = parse(
        "((lambda (x) ((lambda (f) (f 5)) (lambda (y) (+ x y)))) 20)"
    )
    assert eval_expr(expression, env) == 25


def test_quote():
    env = standard_env()
    expression = parse("(quote (1 2 3))")
    assert eval_expr(expression, env) == [1, 2, 3]


def test_run_multiple():
    program = "(+ 1 2) (* 3 4)"
    assert run(program) == 12


def test_errors():
    env = standard_env()
    with pytest.raises(SchemeError):
        eval_expr(parse("()"), env)
    with pytest.raises(SchemeError):
        eval_expr(parse("(if #t 1)"), env)
    with pytest.raises(SchemeError):
        eval_expr(parse("(lambda x x)"), env)
    with pytest.raises(SchemeError):
        eval_expr(parse("(unknown 1 2)"), env)
