.DEFAULT_GOAL := help
PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn

help: ## показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

venv: ## создать окружение и поставить зависимости
	uv venv --python 3.12
	uv pip install -e ".[dev]"

dev: ## поднять сайт локально (http://127.0.0.1:8040)
	$(UVICORN) app.main:app --reload --port 8040

check-sources: ## проверить доступность всех источников новостей
	$(PY) -m pipeline.cli check-sources

ingest: ## собрать свежие новости из лент
	$(PY) -m pipeline.cli ingest

test: ## прогнать тесты (без сети)
	.venv/bin/pytest -q -m "not network"

test-network: ## прогнать тесты, которые ходят в интернет
	.venv/bin/pytest -q -m network

lint: ## проверить стиль кода
	.venv/bin/ruff check .

deploy: ## выложить на сервер
	./scripts/deploy.sh

.PHONY: help venv dev check-sources ingest test test-network lint deploy
