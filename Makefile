.PHONY: test dep

dep:
	uv sync

test:
	uv run -m pytest tests -v
