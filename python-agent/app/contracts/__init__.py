"""从 events.schema.json 生成并镜像的 SSE 事件 pydantic 模型所在包。

models.py 由 contracts/generate.sh 经 datamodel-code-generator 生成，禁止手改；
改契约必须先改 schema 再重跑生成脚本。
"""
