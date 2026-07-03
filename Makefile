.DEFAULT_GOAL := help
.PHONY: help sync test lint format type-check build docs clean

STM_FILES := $(shell find . -path ./.venv -prune -o -name '*.stm' -print)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

sync: ## Install/refresh the virtualenv from pyproject + lock
	uv sync

test: ## Run the test suite
	uv run pytest

lint: ## Check style and imports without modifying files
	uv run ruff check .
	uv run ruff format --check .
	uv run harel-fmt --check $(STM_FILES)

format: ## Auto-format code and .stm files in place
	uv run ruff format .
	uv run ruff check --fix .
	uv run harel-fmt $(STM_FILES)

type-check: ## Run static type checking
	uv run mypy

docs: ## Build the HTML documentation
	uv run --with sphinx --with myst-parser --with sphinxcontrib-mermaid --with furo \
		sphinx-build -b html docs docs/_build

build: ## Build the sdist and wheel
	uv build

clean: ## Remove caches and build artifacts
	rm -rf dist build docs/_build *.egg-info src/*.egg-info .ruff_cache
	find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} +
	find . -path ./.venv -prune -o -type d -name .pytest_cache -exec rm -rf {} +
