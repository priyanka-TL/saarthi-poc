.PHONY: install run test migrate migrate-down db-shell lint venv

venv:
	uv venv --python 3.10

install: venv
	uv pip install -r requirements.txt

run:
	flask run

test:
	pytest

migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

db-shell:
	docker compose exec db psql -U postgres

lint:
	uv run lint-imports
