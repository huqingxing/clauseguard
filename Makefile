# Stage 1 本地工作流。基础设施命令依赖 .env（cp .env.example .env）。
COMPOSE := docker compose --env-file .env -f deploy/docker-compose.yml

# 评测用 Python：优先 venv（Windows / Linux 路径自适应），缺省回退 uv run。
ifeq ($(OS),Windows_NT)
EVAL_PY := python-agent/.venv/Scripts/python.exe
else
EVAL_PY := python-agent/.venv/bin/python
endif

.PHONY: up down logs gen test-py test-java eval eval-smoke eval-baseline fmt contracts-validate \
        demo-contract trace-demo bench run-py

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

# ---------------- Stage 14：可观测性与交付物 ----------------

# 演示合同（docx 不入库，脚本可复现；每个专家至少 2 条在范畴内 + 1 条高风险）。
demo-contract:
	@test -x "$(EVAL_PY)" || (cd python-agent && uv sync -q)
	$(EVAL_PY) deploy/demo/make_demo_contract.py

# 一条 trace 串起「Java 父上下文 + 6 个专家 span」：本地 OTLP 采集器断言父子关系。
# 指向 Langfuse：改 OTEL_EXPORTER_OTLP_TRACES_ENDPOINT / OTEL_EXPORTER_OTLP_HEADERS 即可。
trace-demo: demo-contract
	@test -x "$(EVAL_PY)" || (cd python-agent && uv sync -q)
	$(EVAL_PY) deploy/otel/verify_trace.py

# 本地起 Python 编排服务（无密钥演示/压测用确定性替身；生产去掉 MODEL_BACKEND 即真实模型）。
MODEL_BACKEND ?= mock
run-py:
	cd python-agent && CLAUSEGUARD_AGENT_MODEL_BACKEND=$(MODEL_BACKEND) \
		CLAUSEGUARD_AGENT_PG_DSN= uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 压测：20 并发 / 5 分钟，产出 eval/out/bench*.csv 与 bench-tokens.json（docs/benchmark.md 的原料）。
# 需先另开一个终端跑 make run-py。
USERS ?= 20
RATE ?= 4
TIME ?= 5m
bench:
	@test -x "$(EVAL_PY)" || (cd python-agent && uv sync -q)
	$(EVAL_PY) -m locust -f deploy/locust/locustfile.py --headless \
		-u $(USERS) -r $(RATE) -t $(TIME) --host http://127.0.0.1:8000 \
		--csv eval/out/bench --only-summary
