# Stage 10 验收报告｜Word 批注回写

- 日期：2026-10-24
- 样本：20 份确定性生成合同（多 run 段落/空段/表格/页眉/超链接）

## 验收结果

| 验收点 | 结果 |
|---|---|
| Word/WPS 打开无「需要修复」（zip CRC + python-docx 重开 + 四件套 id 一致 + rels/ContentTypes 齐备 + UTF-8 + 无关部件不变） | ✅ 20/20 |
| 20 份样本回写后文件结构完整 | ✅ 20/20，每份 5 条批注 |
| 抽检 10 条锚点与原文 span 对齐 | ✅ 全量 100 条逐字对齐（超过抽检基线） |
| 中文批注内容无乱码 | ✅ 摘要/建议/法条逐字回念 |
| run.completed 携带带签名下载 URL | ✅ artifact 四字段经断点->resume 全链路 |

## 实现要点

- lxml 直接操作四件套；Clause.start/end 全文偏移 -> run 级锚点，跨 run 拆段保连续；
- 高风险 w:highlight=yellow，中风险 w:shd=FFFFCC，低风险不着色；
- 坐标系与解析层逐块自校验，不一致即 RenderError（挂错批注比不挂更糟）；
- 源不可用/非 docx/存储未配置 -> 显式 PLACEHOLDER_RENDER 占位，不假装修写成功。
