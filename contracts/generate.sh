#!/usr/bin/env bash
# Stage 2 契约代码生成。
# 生成物只读、幂等：改契约必须先改 schema/yaml，再重跑本脚本，禁止手改生成文件。
# 幂等保证：openapi-generator 传 hideGenerationTimestamp 去掉文件头日期；
# datamodel-code-generator 头部仅含稳定版本注释；两者同一环境重跑字节一致。
set -euo pipefail

# 为什么留 PYTHON 入口：Windows 上裸 python 常指向无依赖的系统解释器，
# 调用方可用 PYTHON=../python-agent/.venv/Scripts/python.exe 指定带依赖的环境。
PYTHON="${PYTHON:-python}"
if [ -f "$PYTHON" ]; then
  PYTHON="$(cd "$(dirname "$PYTHON")" && pwd)/$(basename "$PYTHON")"
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> [1/3] 校验契约自洽（schema/yaml 解析 + 字段约束 + examples 必填）"
"$PYTHON" - <<'PY'
import json
import sys
from pathlib import Path

schema = json.loads(Path("contracts/events.schema.json").read_text(encoding="utf-8"))
if "$defs" not in schema:
    print("FATAL: events.schema.json 缺少 $defs", file=sys.stderr)
    sys.exit(1)
# 每个事件定义必须 additionalProperties=false 且至少一条 example（验收硬要求）。
for name, definition in schema["$defs"].items():
    if definition.get("additionalProperties") is not False:
        print(f"FATAL: $defs.{name} 必须 additionalProperties=false（铁律3：禁止私加字段）", file=sys.stderr)
        sys.exit(1)
    examples = definition.get("examples") or []
    if not examples:
        print(f"FATAL: $defs.{name} 必须至少有一条 example（验收要求）", file=sys.stderr)
        sys.exit(1)
event_count = len(schema["$defs"])
print(f"OK events.schema.json: {event_count} 个事件定义，均 additionalProperties=false 且含 example")

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:
    print("WARN: 未安装 pyyaml，跳过 openapi.yaml 解析校验")
else:
    spec = yaml.safe_load(Path("contracts/openapi.yaml").read_text(encoding="utf-8"))
    assert spec.get("openapi", "").startswith("3.1"), "openapi 必须为 3.1.0"
    print(f"OK openapi.yaml: OpenAPI {spec['openapi']}，paths={len(spec.get('paths', {}))}")
PY

echo "==> [2/3] openapi-generator -> Java DTO (java-service/generated)"
# 为什么 supportingFiles=false + *Tests=false：只产出 model/client 源码，不污染主模块、不触发额外依赖。
# 为什么 library=resttemplate + useJakartaEe=true：与 Spring Boot 3（jakarta）栈一致，编译期只需 spring-web（已含）。
# 为什么 hideTimestamp：保证两次生成字节一致，满足 make gen 幂等验收。
GENERATOR="npx --yes @openapitools/openapi-generator-cli"
$GENERATOR generate \
  -i contracts/openapi.yaml \
  -g java \
  -o java-service/generated \
  --global-property=apis=true,models=true,apiTests=false,modelTests=false,supportingFiles=false,hideGenerationTimestamp=true \
  --additional-properties=sourceFolder=src/main/java,invokerPackage=com.clauseguard.api,modelPackage=com.clauseguard.api.model,apiPackage=com.clauseguard.api.client,dateLibrary=java8,useJakartaEe=true,library=resttemplate,serializableModel=true

echo "==> [3/3] datamodel-code-generator -> Python models (python-agent/app/contracts/models.py)"
"$PYTHON" -m datamodel_code_generator \
  --input contracts/events.schema.json \
  --input-file-type jsonschema \
  --output python-agent/app/contracts/models.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 \
  --use-standard-collections \
  --use-schema-description

echo "==> DONE: 生成完成。产物已写入 java-service/generated 与 python-agent/app/contracts/models.py"
