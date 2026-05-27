.PHONY: format test

PYTHON ?= python
RUFF ?= ruff

format:
	$(RUFF) format src tests

test:
	$(PYTHON) -m pytest tests/
