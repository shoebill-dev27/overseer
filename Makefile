.PHONY: setup setup-backend setup-agent setup-frontend
.PHONY: run-backend run-agent run-frontend dev
.PHONY: lint test db-init help

BACKEND_DIR := backend
AGENT_DIR   := agent

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Setup:"
	@echo "  setup           Install all dependencies"
	@echo "  setup-backend   Install backend dependencies"
	@echo "  setup-agent     Install agent dependencies"
	@echo ""
	@echo "Run:"
	@echo "  run-backend     Start FastAPI (port 8000)"
	@echo "  run-agent       Start Local Agent"
	@echo "  dev             Start backend + agent"
	@echo ""
	@echo "DB:"
	@echo "  db-init         Initialize SQLite database"
	@echo ""
	@echo "Quality:"
	@echo "  lint            Run ruff + mypy"
	@echo "  test            Run pytest"

setup: setup-backend setup-agent

setup-backend:
	cd $(BACKEND_DIR) && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

setup-agent:
	cd $(AGENT_DIR) && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

db-init:
	cd $(BACKEND_DIR) && .venv/bin/python -m app.database

run-backend:
	cd $(BACKEND_DIR) && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

run-agent:
	cd $(AGENT_DIR) && .venv/bin/python agent.py

dev:
	make run-backend & make run-agent

lint:
	cd $(BACKEND_DIR) && .venv/bin/ruff check app/ && .venv/bin/ruff format --check app/
	cd $(AGENT_DIR)   && .venv/bin/ruff check . && .venv/bin/ruff format --check .

test:
	cd $(BACKEND_DIR) && .venv/bin/pytest tests/ -v
	cd $(AGENT_DIR)   && .venv/bin/pytest tests/ -v
