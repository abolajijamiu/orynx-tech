.PHONY: install db test lint api fmt

install:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev,postgres]"

db:
	docker compose up -d db

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check src tests

fmt:
	.venv/bin/ruff format src tests

api:
	.venv/bin/uvicorn orynx.api.app:app --reload --port 8000
