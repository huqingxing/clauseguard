# Stage 1 本地工作流。基础设施命令依赖 .env（cp .env.example .env）。
COMPOSE := docker compose --env-file .env -f deploy/docker-compose.yml

# 评测用 Python：优先 venv（Windows / Linux 路径自适应），缺省回退 uv run。
ifeq ($(OS),Windows_NT)
EVAL_PY := python-agent/.venv/Scripts/python.exe
else
EVAL_PY := python-agent/.venv/bin/python
endif

.PHONY: up down logs gen test-py test-java test-web eval eval-smoke eval-baseline fmt contracts-validate \
        demo-contract trace-demo bench run-py lab lab-rerank lab-verify

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

# Stage 12：前端纯逻辑单测。用 Node 内置 runner + 原生 TS，不引测试框架（铁律 5）。
test-web:
	cd web && npm run test:unit

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

# ---------------- 阶段 4：检索实验台矩阵跑批 ----------------
# 一条命令跑完 strategy×embedding 矩阵，产出 lab/out/<LABEL>/report.md 与 metrics.json。
# 不依赖 GNU make 的等价命令（本机未装 make 时用这个）：
#   cd python-agent && uv run python ../lab/run.py --label $(LABEL)
# 可选覆盖：make lab LABEL=v1 STRATEGIES=S1_whole,S2_fixed512 EMBEDDINGS=E1,E2,E3 \
#                  RERANK=off BM25=on K=5 PROVIDER=deterministic
STRATEGIES ?= S1_whole,S2_fixed512,S3_fixed256,S4_paragraph,S5_context
EMBEDDINGS ?= E1,E2,E3
RERANK ?= off
BM25 ?= on
K ?= 5
PROVIDER ?= deterministic
LAB_ARGS := --strategies $(STRATEGIES) --embeddings $(EMBEDDINGS) --rerank $(RERANK) \
	--bm25 $(BM25) --k $(K) --provider $(PROVIDER)

lab:
	cd python-agent && uv run python ../lab/run.py --label $(LABEL) $(LAB_ARGS)

# 阶段 5：固定矩阵最优组合，只切换 rerank on/off；结果写进同一 label 的
# rerank.json，并由 report.py 自动在 report.md 追加「rerank 收益」节。
RERANKER ?= live
RERANK_ARGS := --reranker $(RERANKER)

lab-rerank:
	cd python-agent && uv run python ../lab/run_rerank.py --label $(LABEL) $(RERANK_ARGS)

# 结构断言；两次重跑比对需额外传 VERIFY_ARGS（见 docs/retrieval-stage5-acceptance.md）。
lab-verify:
	cd python-agent && uv run python ../lab/verify.py \
		--metrics ../lab/out/$(LABEL)/metrics.json \
		--rerank ../lab/out/$(LABEL)/rerank.json $(VERIFY_ARGS)
