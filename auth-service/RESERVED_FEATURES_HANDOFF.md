# 云端预留功能 Handoff：历史方法总结与 API 额度充值

> 状态：只预留入口、页面和接口契约；业务能力尚未实现
> 适用入口：`https://c2sml.cn/agent/`
> 接口版本：`v1`

## 1. 边界

本次只提供：

- 登录后可见的导航按钮；
- 两个说明页面；
- 两个机器可读状态接口；
- 两个始终 fail closed 的未来创建接口；
- `/agent` 路径前缀兼容；
- 认证、CSRF 和安全响应头保护。

本次明确没有：

- 调用 critic model 或总结模型；
- 读取、筛选或修改任何历史记录；
- 创建总结任务或派生知识；
- 接入 DeepSeek、Qwen 或支付渠道；
- 创建订单、余额、账单或充值记录；
- 收集、生成、存储或下发 API key；
- 修改数据库模型或执行 migration；
- 在客户端嵌入供应商主密钥。

## 2. 页面

登录用户可访问：

```text
GET /agent/features/history-synthesis/
GET /agent/features/api-credits/
```

匿名请求沿用 Django 登录跳转。两个页面均清楚标记“尚未开放”，操作按钮为禁用状态。

### 历史方法总结页面

展示计划流程：

```text
候选历史选择
  -> Critic model 纳入判断
  -> 共性流程与方法提取
  -> 证据关联
  -> 人工审核
```

未来实现必须保持 owner 隔离。Critic 的判断应保存理由、模型/提示版本和输入证据；总结只能使用通过判断的候选证据，产物在人工审核前应是草稿。

### API 额度充值页面

展示计划支持：

```text
DeepSeek
Qwen
客户端安全激活
```

页面当前没有 API key、支付 token、卡号或订单输入框，不接受任何付款或凭据。

## 3. 状态接口

### 3.1 历史方法总结

```http
GET /agent/api/v1/features/history-synthesis/
```

要求已有 Django Session。响应：

```json
{
  "schema_version": "v1",
  "feature": "history_synthesis",
  "status": "reserved",
  "available": false,
  "accepting_requests": false,
  "planned_stages": [
    "candidate_selection",
    "critic_eligibility_review",
    "common_process_synthesis",
    "evidence_linking",
    "human_review"
  ],
  "create_endpoint": "/agent/api/v1/features/history-synthesis/runs/"
}
```

### 3.2 API 额度充值

```http
GET /agent/api/v1/features/api-credits/
```

要求已有 Django Session。响应：

```json
{
  "schema_version": "v1",
  "feature": "api_credits",
  "status": "reserved",
  "available": false,
  "accepting_orders": false,
  "planned_providers": ["deepseek", "qwen"],
  "planned_delivery": "desktop_secure_activation",
  "create_endpoint": "/agent/api/v1/features/api-credits/orders/"
}
```

两个状态响应均包含：

```http
Cache-Control: no-store
```

客户端必须以 `available` 和 `accepting_*` 为准，不能仅根据路由存在就启用按钮。

## 4. 未来创建接口的当前行为

### 4.1 总结任务

```http
POST /agent/api/v1/features/history-synthesis/runs/
```

### 4.2 充值订单

```http
POST /agent/api/v1/features/api-credits/orders/
```

两者当前都：

- 要求登录；
- 要求有效 CSRF；
- 只接受 POST；
- 固定返回 HTTP 503；
- 不解析或持久化业务请求；
- 不进行任何业务写入。

响应形状：

```json
{
  "schema_version": "v1",
  "feature": "history_synthesis 或 api_credits",
  "error": "feature_not_available",
  "status": "reserved",
  "writes_performed": false
}
```

客户端收到 503 时应显示“功能尚未开放”，不得自动快速重试，也不得转用其他未声明路径。

## 5. 历史总结未来最小数据契约

未来实现前应先新增独立、owner-scoped 的派生层，不直接改写原始历史：

```text
SynthesisRun
- id
- owner
- requester
- status
- pipeline_version
- critic_model_version
- synthesis_model_version
- config
- idempotency_key
- created_at / started_at / finished_at

SynthesisItem
- run
- root_session
- input_hash
- critic_decision
- critic_reason
- evidence_refs
- attempts / lease / error

SynthesisDraft
- run
- title
- process_markdown
- method_markdown
- evidence_refs
- review_status
- reviewer / reviewed_at
```

关键要求：

- owner 从认证用户和被选择的源记录继承，普通用户不能覆盖；
- root session 和 subagent thread 不可被静默压平成一个无来源时间线；
- 使用稳定消息顺序和输入 hash；
- critic 与总结阶段分别版本化；
- 原始历史不可变；
- 产物可重建、可审查、可撤销；
- 未经人工确认不得发布为共同方法或 memory；
- 模型调用前再次脱敏。

## 6. 充值系统未来安全边界

“把 API 安在客户端”不应实现为把平台主密钥硬编码进安装包。推荐优先级：

1. 云端模型网关按用户/订单计量，客户端只持有可撤销的短期访问会话；
2. 若供应商支持，使用每用户或每设备的受限子密钥和限额；
3. 最后才考虑向客户端交付独立供应商密钥，而且必须通过 OS Keychain/Secret Service 保存、可撤销、可轮换且不可在 renderer/日志中出现。

未来最小模型建议：

```text
CreditOrder
- id
- owner
- provider
- product_sku
- amount
- currency
- status
- idempotency_key
- payment_reference
- created_at / paid_at / fulfilled_at

CreditGrant
- order
- owner
- provider
- granted_amount
- remaining_amount
- status
- expires_at

ClientActivation
- owner
- device_id
- grant
- one_time_token_hash
- expires_at
- redeemed_at
- revoked_at
```

必须单独设计：

- 实名/支付合规、发票、退款和对账；
- 支付回调签名与重放保护；
- 金额和币种由服务端 SKU 决定，不能信任客户端；
- 订单幂等、状态机和审计；
- 供应商额度到账验证；
- 设备解绑、凭据撤销和轮换；
- 消费限额、余额不足和滥用控制；
- 不在聊天、Git、命令参数、网页 HTML 或日志中传递密钥。

## 7. 客户端当前接入规则

客户端当前只能：

1. 使用现有 Django Session 登录；
2. 查询两个状态接口；
3. 在 `available=false` 时显示禁用入口和“尚未开放”；
4. 未来再根据版本化契约启用创建操作。

客户端当前不能：

- 调用供应商 API 充值；
- 发送支付信息；
- 提交 API key；
- 认为存在页面就代表功能可用；
- 轮询 POST 创建接口；
- 绕过 HTTPS、登录或 CSRF。

现有登录和历史上传说明见：

```text
CLIENT_LOGIN_HANDOFF.md
```

## 8. 源码位置

```text
路由：       history/urls.py
页面/接口：  history/views.py
全局按钮：   templates/base.html
概览按钮：   templates/history/dashboard.html
总结页面：   templates/history/history_synthesis.html
充值页面：   templates/history/api_credits.html
样式：       history/static/history/app.css
测试：       history/tests/test_reserved_features.py
前缀测试：   history/tests/test_subpath.py
```
