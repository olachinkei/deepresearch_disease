.PHONY: install dev web agent test quality corpus-seed

install:
	pnpm install
	cd services/agent && uv sync --all-groups

dev:
	pnpm dev

web:
	pnpm --filter @deepresearch/web dev

agent:
	cd services/agent && uv run --env-file ../../.env deepresearch-agent

test:
	pnpm test
	cd services/agent && uv run pytest

quality:
	pnpm lint
	pnpm typecheck
	cd services/agent && uv run ruff check .
	cd services/agent && uv run mypy src

corpus-seed:
	cd services/agent && uv run --env-file ../../.env collect-public-seed --output ../../data/public_seed
