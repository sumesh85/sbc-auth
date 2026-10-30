.PHONY: install lint format check security test db-upgrade db-migrate run run-otel docker-up docker-down docker-build

install:
	poetry install

lint:
	poetry run ruff check .

format:
	poetry run ruff format .

check: lint
	poetry run ruff check . --fix

security:
	poetry run bandit -r . -c pyproject.toml

test:
	poetry run pytest

db-upgrade:
	poetry run flask db upgrade

db-migrate:
	poetry run flask db migrate

run:
	poetry run flask run

run-otel:
	OTEL_SDK_DISABLED=false OTEL_SERVICE_NAME=auth-api poetry run flask run

docker-up:
	cd ../docker && docker-compose up -d

docker-down:
	cd ../docker && docker-compose down

docker-build:
	docker build . -t auth-api

ci: lint security test
