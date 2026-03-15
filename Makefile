.PHONY: clean install test lint format run

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check gbot/

format:
	uv run ruff format gbot/

run:
	uv run uvicorn gbot.api.app:app --reload --port 8000
