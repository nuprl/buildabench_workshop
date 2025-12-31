import unittest

from test_projects.scheme_interpreter.scheme import (
    SchemeError,
    eval_expr,
    parse,
    parse_many,
    run,
    standard_env,
    to_string,
)


class SchemeInterpreterTests(unittest.TestCase):
    def test_parse_numbers_and_symbols(self):
        self.assertEqual(parse("42"), 42)
        self.assertEqual(parse("-7"), -7)
        self.assertEqual(parse("3.5"), 3.5)
        self.assertEqual(parse("foo"), "foo")

    def test_parse_list(self):
        self.assertEqual(parse("(+ 1 2)"), ["+", 1, 2])

    def test_parse_many(self):
        expressions = parse_many("(+ 1 2) (* 2 3)")
        self.assertEqual(expressions, [["+", 1, 2], ["*", 2, 3]])

    def test_to_string_roundtrip(self):
        expr = parse("(if #t (+ 1 2) (- 5 3))")
        self.assertEqual(to_string(expr), "(if #t (+ 1 2) (- 5 3))")

    def test_arithmetic(self):
        env = standard_env()
        self.assertEqual(eval_expr(parse("(+ 1 2 3)"), env), 6)
        self.assertEqual(eval_expr(parse("(- 10 3 2)"), env), 5)
        self.assertEqual(eval_expr(parse("(- 5)"), env), -5)
        self.assertEqual(eval_expr(parse("(* 2 3 4)"), env), 24)
        self.assertAlmostEqual(eval_expr(parse("(/ 8 2)"), env), 4)
        self.assertAlmostEqual(eval_expr(parse("(/ 2)"), env), 0.5)

    def test_comparisons(self):
        env = standard_env()
        self.assertTrue(eval_expr(parse("(> 5 3 1)"), env))
        self.assertFalse(eval_expr(parse("(> 5 5)"), env))
        self.assertTrue(eval_expr(parse("(<= 2 2 3)"), env))
        self.assertTrue(eval_expr(parse("(= 4 4 4)"), env))

    def test_if_expression(self):
        env = standard_env()
        self.assertEqual(eval_expr(parse("(if #t 1 2)"), env), 1)
        self.assertEqual(eval_expr(parse("(if #f 1 2)"), env), 2)

    def test_lambda_application(self):
        env = standard_env()
        expression = parse("((lambda (x y) (+ x y)) 3 4)")
        self.assertEqual(eval_expr(expression, env), 7)

    def test_lambda_closure(self):
        env = standard_env()
        expression = parse("(((lambda (x) (lambda (y) (+ x y))) 5) 7)")
        self.assertEqual(eval_expr(expression, env), 12)

    def test_higher_order_function(self):
        env = standard_env()
        expression = parse("((lambda (f x) (f x)) (lambda (n) (* n n)) 6)")
        self.assertEqual(eval_expr(expression, env), 36)

    def test_variable_shadowing(self):
        env = standard_env()
        expression = parse("((lambda (x) ((lambda (x) (+ x 1)) 10)) 5)")
        self.assertEqual(eval_expr(expression, env), 11)

    def test_closure_outlives_scope(self):
        env = standard_env()
        expression = parse(
            "((lambda (x) ((lambda (f) (f 5)) (lambda (y) (+ x y)))) 20)"
        )
        self.assertEqual(eval_expr(expression, env), 25)

    def test_quote(self):
        env = standard_env()
        expression = parse("(quote (1 2 3))")
        self.assertEqual(eval_expr(expression, env), [1, 2, 3])

    def test_run_multiple(self):
        program = "(+ 1 2) (* 3 4)"
        self.assertEqual(run(program), 12)

    def test_errors(self):
        env = standard_env()
        with self.assertRaises(SchemeError):
            eval_expr(parse("()"), env)
        with self.assertRaises(SchemeError):
            eval_expr(parse("(if #t 1)"), env)
        with self.assertRaises(SchemeError):
            eval_expr(parse("(lambda x x)"), env)
        with self.assertRaises(SchemeError):
            eval_expr(parse("(unknown 1 2)"), env)


if __name__ == "__main__":
    unittest.main()
