# PartScope
#
# The targets here are thin. All real work lives in cross-platform Python and
# npm entry points, because `make` does not exist on a stock Windows box and
# this project has to run for whoever clones it. Windows users get the same
# target names via `./make.ps1 <target>`.

SHELL := /bin/bash
ML := ml-service

# Everything Python runs out of a project-local venv. Installing this project's
# pins into a user's global interpreter is how you break their other projects.
VENV := .venv
PY := $(VENV)/bin/python

.DEFAULT_GOAL := help
.PHONY: help setup up down seed reseed verify dev test clean demo

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: .env ## Create the venv and install Python, API and web dependencies
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r $(ML)/requirements.txt
	cd api && npm install
	cd web && npm install

# Created here rather than left as a manual step, so that clone -> setup -> demo
# is genuinely three commands. The defaults in .env.example match what
# docker-compose brings up, so there is nothing to edit and no key to obtain.
.env:
	@cp .env.example .env
	@echo "created .env from .env.example (defaults work as-is)"

up: ## Start Postgres + Mongo
	docker compose up -d
	@echo "waiting for databases to report healthy..."
	$(PY) scripts/wait_for_db.py

down: ## Stop the databases (data is kept)
	docker compose down

seed: ## Generate the synthetic catalog and price history
	$(PY) scripts/seed.py

reseed: ## Wipe and regenerate all synthetic data
	$(PY) scripts/seed.py --force

verify: ## Prove the seeded data has the properties we claim
	$(PY) scripts/verify_seed.py

dev: ## Run ml-service, api and web together
	$(PY) scripts/dev.py

test: ## Run the Python and Node test suites
	# Run pytest from the repo root, not from inside ml-service: PY is a
	# relative path, so `cd ml-service && $(PY)` resolves to a venv that does
	# not exist there. npm is on PATH, so the API line can cd safely.
	$(PY) -m pytest -q $(ML)/tests
	cd api && npm test --silent

demo: .env up seed dev ## Databases up, data seeded, everything running

clean: ## Remove containers and volumes (destroys seeded data)
	docker compose down -v
