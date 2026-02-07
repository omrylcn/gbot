# Contributing to GraphBot

Thanks for your interest! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/<user>/graphbot.git
cd graphbot
uv sync --extra dev
cp .env.example .env          # fill in at least one LLM API key
```

## Running Tests

```bash
uv run pytest tests/ -v
```

## Code Style

We use [ruff](https://docs.astral.sh/ruff/) for linting:

```bash
uv run ruff check graphbot/
uv run ruff format graphbot/
```

- Line length: 100
- Target: Python 3.11+
- Flat pytest functions (no class-based TestCase)
- Docstrings: numpy style, English

## Branch Strategy

- `main` — stable releases only
- `dev` — active development
- `feature/*` — new features (branch from `dev`)
- `fix/*` — bug fixes (branch from `dev`)

## Pull Requests

1. Fork the repo and create your branch from `dev`
2. Add tests for new functionality
3. Ensure `uv run pytest tests/ -v` passes
4. Ensure `uv run ruff check graphbot/` is clean
5. Open a PR against `dev`

## Architecture

See [CLAUDE.md](CLAUDE.md) for architecture rules and key files.
See [mimari_kararlar.md](mimari_kararlar.md) for detailed architectural decisions.
