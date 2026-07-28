.PHONY: install run test migrate migrate-down db-shell lint

install:
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
	lint-imports
