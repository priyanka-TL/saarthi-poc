.PHONY: install run test migrate lint

install:
	uv pip install -r requirements.txt

run:
	flask run

test:
	pytest

migrate:
	alembic upgrade head

lint:
	lint-imports
