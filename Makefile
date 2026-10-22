# Stage 1 本地工作流。基础设施命令依赖 .env（cp .env.example .env）。
COMPOSE := docker compose --env-file .env -f deploy/docker-compose.yml

.PHONY: up down logs gen test-py test-java eval fmt contracts-validate

up:
	@test -f .env || cp .env.example .env
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

gen contracts-validate:
	bash contracts/generate.sh

test-py:
	cd python-agent && uv run pytest

test-java:
	cd java-service && mvn -q test

# 评测基建在后续阶段接入；此处只占位，避免假实现。
eval:
	@echo "eval harness lands in a later stage; nothing to run yet"

fmt:
	cd python-agent && uv run ruff format app tests && uv run ruff check --fix app tests
