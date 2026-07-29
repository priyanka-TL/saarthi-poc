.PHONY: install run test migrate migrate-down db-shell lint venv

venv:
	test -d .venv || uv venv --python 3.10

install: venv
	uv pip install -r requirements.txt

run:
	uv run flask run

test:
	uv run pytest

migrate:
	uv run alembic upgrade head

migrate-down:
	uv run alembic downgrade -1

db-shell:
	docker compose exec db psql -U postgres

lint:
	uv run lint-imports
