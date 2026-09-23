VENV ?= .venv
PY = $(VENV)/bin/python
UV ?= $(HOME)/.local/bin/uv

.PHONY: all bootstrap dev migrate test test-ui web-check lint web-build smoke image up down

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

test-ui:
	$(PY) -m pytest -q -m ui tests/ui

web-check:
	$(PY) scripts/web_check.py catalog bundle a11y

# Needs the engine deps (laya, torch) installed.
smoke:
	$(PY) scripts/smoke_laya.py

# Container-first deploy path (state and model cache live in named volumes).
image:
	docker build -t layawatch:0.1.0 .

up:
	docker compose up -d --build

down:
	docker compose down
