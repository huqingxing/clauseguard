# ADR-0008：审批写入与评测接口纳入 ADMIN 保护

- 状态：已实施并验收（2026-11-12）
- 取代：ADR-0005 中「审批写入端点 `POST /api/v1/reviews/{id}/approve` 仍在 `permitAll`」的已知缺口
- 关联：ADR-0005（按角色拆两端）、ADR-0007（状态/事件原子性）

## 背景

`SecurityConfig` 只保护 `/api/v1/admin/**`，其余 `/api/v1/**` 一律 `permitAll`。结果是：

- `POST /api/v1/reviews/{id}/approve` 不需要任何凭证，`curl` 不带 `-u` 就是 200——
  任何人可以直接批准合同（实测：只要带一个客户端自称的 `X-Operator` 头就能穿过 Security 到达业务层）。
- `GET /api/v1/eval/summary` 不需要任何凭证，`curl` 不带 `-u` 直接 200——任何人能拿到 token 用量与成本结构。

`X-Operator` 是客户端自称的 HTTP 头，不是服务端认定的身份：等于谁都能以别人的名义签字，审计表不可信。

## 约束

- 用户端（提交者）**暂不**加登录：给整个用户端上鉴权需要先做用户认证，是另一个阶段的产品决策，不在本任务内（ADR-0005 已登记为取舍）。
- 不能用共享密钥头（如固定 `X-Api-Key`）代替认证：那只是另一层客户端自称，没有身份主体。
- 不改审批数据结构（`approval.operator` 列照旧），不改事件 schema。
- 不引入新中间件（Kafka/MQ）。

## 决策

1. **最小改动路径**：把两个端点挪到 `/api/v1/admin/` 下，直接复用 `SecurityConfig` 已有的
   `hasRole("ADMIN")` 规则——不新增任何鉴权代码。
   - 审批：`POST /api/v1/admin/reviews/{id}/approve`（新增 `AdminReviewController`）。
   - 评测：`GET /api/v1/admin/eval/summary`（`EvalController` 的 `@RequestMapping` 改前缀）。
2. **操作人取认证主体**：`AdminReviewController.approve` 从 `Authentication.getName()` 取审批人，
   写入 `approval.operator`；不再信任 `X-Operator` 头。
3. **X-Operator 头保留接收但废弃**：仍出现时仅触发 `WARN`（解码后用于告警），值**不**参与任何持久化。
   等所有客户端升级完毕可彻底删除该头与 `decodeOperator`。
4. **前端同步**：`web` 的 `approveReview` / `getEvalSummary` 改调新路径；`http.ts` 的 `ADMIN_PATH`
   正则 `/^\/api\/v1\/admin/` 自动给这些路径附加 Basic 凭证，无需额外改动。前端不再向 `approveReview`
   传 operator（界面展示仍用 `getUsername()`，但不回传后端）。

## 备选与否决

- **B：加 ADMIN 规则匹配旧路径（不动 URL）**：也能锁住端点，但 URL 语义与「用户端 `/api/v1/reviews`」
  混在一起，运维/网关按前缀分流时容易误判；挪路径让「用户端 vs 管理端」在 URL 上彻底分开，最省认知成本。
- **C：给两个端点单独配安全规则**：与现有「按前缀统一保护」的写法不一致，规则会散落，否决。

## 影响面

- 后端：`ReviewController` 移除 `approve` 与 `decodeOperator`；新增 `AdminReviewController`；
  `EvalController` 改前缀。
- 前端：`web/src/api/reviews.ts`、`web/src/api/eval.ts`、`web/src/views/ApprovalView.vue`。
- 契约：`contracts/openapi.yaml` 两处 path 改写，`X-Operator` 标 `deprecated: true`。
- 测试：`ReviewControllerTest` 的 4 个 approve 用例迁到 `AdminReviewControllerTest`（新路径 + ADMIN 鉴权），
  并新增「匿名→401 / USER→403 / ADMIN→200 且 operator=认证主体」；`AdminControllerTest` 增加评测看板三态。

## 验收（真实输出）

服务以真实 Basic 认证 + 真实 MySQL 运行（`ADMIN=admin/admin123`，`USER=user/user123`，端口 18080）：

```
# 修复前（pre-fix jar）：鉴权缺失
curl /api/v1/eval/summary                                  -> 200  (无凭证即返回成本结构，泄露)
curl /api/v1/reviews/repro-auth-fix-1/approve \
     -H "X-Operator: attacker"                             -> 409  (已穿过 Security 到达业务层，仅 contract 一致性拦截)

# 修复后（同样服务配置，仅端点迁到 /admin）

# --- 评测看板 GET /api/v1/admin/eval/summary ---
curl /api/v1/admin/eval/summary                            -> 401  (未带凭证)
curl -u user:user123 /api/v1/admin/eval/summary            -> 403  ({"code":"FORBIDDEN","message":"当前账号没有管理员权限"})
curl -u admin:admin123 /api/v1/admin/eval/summary          -> 200  (ADMIN 放行，返回真实成本结构 JSON)

# --- 审批写入 POST /api/v1/admin/reviews/repro-auth-fix-1/approve ---
curl /api/v1/admin/reviews/repro-auth-fix-1/approve (无凭证) -> 401
curl -u admin:WRONGPASSWORD .../approve                    -> 401  (密码错误，证明鉴权真实生效，非形同虚设)
curl -u user:user123 .../approve                           -> 403  (USER 无管理员权限)
curl -u admin:admin123 -X POST .../approve \
     -H "X-Operator: attacker" \
     -d '[{"clause_id":"c1","decision":"approve","contract_version":"1","content_hash":"h-fix"}]'
                                                        -> 200  {"status":"accepted","decided_at":"2026-11-12T09:45:40Z"}
DB> SELECT operator FROM approval WHERE review_id='repro-auth-fix-1';
   operator = 'admin'   -- 认证主体，非伪造的 attacker（X-Operator 头被忽略并记 WARN）

# --- 取舍核对：用户端接口仍 permitAll（已登记，非疏漏）---
curl /api/v1/contracts                                    -> 200  (用户端无鉴权，属 ADR-0005 登记的取舍)
curl /api/v1/reviews/repro-auth-fix-1/approve (旧路径)      -> 404  (已迁移到 /admin，旧路径不再存在)
```

- `AdminReviewControllerTest` / `AdminControllerTest` 全绿：匿名 401、USER 403、ADMIN 200 且 operator 取主体。
- 前端管理端审批流程：登录后 `http.ts` 自动附加 Basic 头，`/api/v1/admin/reviews/{id}/approve` 走通。
- 禁止项核对：未给用户端加登录（用户端接口仍 `permitAll`，属登记取舍）；未用共享密钥头；审批数据结构未变；
  无 Kafka/MQ；异常一律走 Spring Security 的 401/403 JSON（无 catch 吞掉）。
