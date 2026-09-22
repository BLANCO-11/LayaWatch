VENV ?= .venv
PY = $(VENV)/bin/python
UV ?= $(HOME)/.local/bin/uv

.PHONY: all bootstrap dev migrate test lint web-build smoke

all: lint test

bootstrap:
	@test -x $(VENV)/bin/python || $(UV) venv --python 3.12 $(VENV)
	$(UV) pip install -p $(VENV) -r requirements-dev.txt

dev:
	$(PY) -m layawatch

migrate:
	$(PY) -m layawatch --migrate-only

test:
	$(PY) -m pytest -q

lint:
	$(VENV)/bin/ruff check .

web-build:
	@test -f web/package.json || { echo "web/ lands in Phase 4"; exit 0; }
	cd web && npm ci && npm run build

# Needs the engine deps (laya, torch) installed.
smoke:
	$(PY) scripts/smoke_laya.py
