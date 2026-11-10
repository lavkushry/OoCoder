.PHONY: setup test lint run demo eval migrate fmt

PYTHON ?= python3.12
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"
	$(VENV)/bin/pre-commit install || true

test:
	$(PYTEST) -q

lint:
	$(RUFF) check src tests
	$(RUFF) format --check src tests
	$(MYPY) src/oss_agent

fmt:
	$(RUFF) check --fix src tests
	$(RUFF) format src tests

migrate:
	$(PY) -m alembic upgrade head

run:
	$(PY) -m oss_agent.cli.main serve --host 127.0.0.1 --port 8787

demo:
	$(PY) -m oss_agent.cli.main demo --offline

eval:
	$(PY) -m oss_agent.eval.harness --out docs/EVAL_REPORT.md
