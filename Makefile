# Stage 1 本地工作流。基础设施命令依赖 .env（cp .env.example .env）。
COMPOSE := docker compose --env-file .env -f deploy/docker-compose.yml

# 评测用 Python：优先 venv（Windows / Linux 路径自适应），缺省回退 uv run。
ifeq ($(OS),Windows_NT)
EVAL_PY := python-agent/.venv/Scripts/python.exe
else
EVAL_PY := python-agent/.venv/bin/python
endif

.PHONY: up down logs gen test-py test-java eval eval-smoke eval-baseline fmt contracts-validate

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

# Stage 13 评测：一条命令产出 eval/out/report.md（与 baseline 对比）。
# 本地 venv 不存在时回退 uv（CI 同款执行路径）。
LABEL ?= run
EVAL_CMD := PYTHONPATH=python-agent:. $(EVAL_PY) -m eval.run

eval:
	@test -x "$(EVAL_PY)" || (cd python-agent && uv sync -q)
	$(EVAL_CMD) --label $(LABEL)

eval-smoke:
	@test -x "$(EVAL_PY)" || (cd python-agent && uv sync -q)
	$(EVAL_CMD) --smoke 10 --label smoke

# 首次建基线 / prompt 变更后重设基线。
eval-baseline:
	@test -x "$(EVAL_PY)" || (cd python-agent && uv sync -q)
	$(EVAL_CMD) --label baseline --set-baseline

fmt:
	cd python-agent && uv run ruff format app tests && uv run ruff check --fix app tests
