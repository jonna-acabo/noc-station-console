.PHONY: install dev test lint docker up monitoring

install:
	cd backend && pip install -r requirements-dev.txt

dev:
	cd backend && SIM_INTERVAL_SECONDS=30 uvicorn app.main:app --reload

test:
	cd backend && SIM_ENABLED=false pytest --cov=app

lint:
	cd backend && ruff check .

docker:
	docker build -t noc-station-console:local .

up:
	docker compose up --build

monitoring:
	docker compose --profile monitoring up --build
