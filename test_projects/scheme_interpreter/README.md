# Scheme-Lite Interpreter (Test Project)

This directory contains a small, dependency-free Scheme-like interpreter used
as a deterministic target for LLM testing.

## Features
- S-expression parser
- Evaluator with lexical scoping
- Printer for Scheme-like expressions
- Supported core forms: `lambda`, `if`
- Built-in arithmetic and comparison operators

## Run Tests

```bash
python -m unittest discover -s test_projects/scheme_interpreter/tests
```
