# Ansatz 对话历史 Mem0 后端实施报告

实施日期：2026-09-02
目标主机：root@39.105.190.93
部署目录：/data/ansatz-agent/voice-trace

## 1. 执行结论

已完成以下安全范围内的上线工作：

- 读取并核验远端认证服务的 SQLite 数据结构；SQLite 仍是对话历史的权威数据源。
- 在认证服务中加入 Mem0 SDK、owner-scoped outbox、脱敏分块、重试 worker、搜索/删除 API 和用户擦除命令。
- 新增独立的 pgvector/pg17 数据库容器，不改动 Langfuse 使用的 PostgreSQL。
- 对 SQLite 做了在线一致性备份，并成功应用 history.0009_memory_ingest_job 迁移。
- 将既有历史安全地写入 outbox ledger（只保存消息 ID、哈希和状态，不调用 LLM）。
- 新认证镜像已运行且健康；Mem0 pgvector collection `ansatz_memory_v2` 已创建（1024 维）。初始 canary 清理后为 0，当前回填核验已有 4 条向量记录（属于 1 个历史 owner），回填仍在继续。
- 已将用户批准的 DashScope OpenAI-compatible 配置写入云服务器受保护的环境文件；API key 未写入仓库、镜像或本报告。
- 已完成合成 canary：GLM LLM 调用、qwen3.7 embedding（1024 维）、qwen3.8 judger 重排，以及 Mem0 写入/检索/删除链路均成功；测试记忆已删除。

用户已明确授权将所有用户的脱敏对话历史发送至 DashScope；当前生产开关为 `MEMORY_ENABLED=1`、`MEMORY_OUTBOX_ENABLED=1`。常驻 `memory-worker` 已启动，正在按 4,000 字符 chunk、单副本限速处理 outbox，失败任务采用指数退避。

## 2. 远端现有数据结构

检查对象：认证服务挂载的 /data/db.sqlite3。检查结果：PRAGMA integrity_check = ok。

| 表/对象 | 现状 | 用途 |
|---|---:|---|
| auth_user | 11 个用户 | Django 账号；只有拥有历史的账号才会被映射到 Mem0 |
| history_historysession | 62 个会话 | 17 个根会话、45 个子 agent 会话；owner 为显式归属字段 |
| history_historymessage | 4,521 条消息 | 角色为 user/assistant/tool；原始历史保留在 SQLite |
| history_usermemorypool | 2 行 | 现有手工 MEMORY.md/USER.md 文本池，未替换 |
| history_importbatch | 2 行 | 导入批次审计 |
| history_memoryingestjob | 新增 | Mem0 outbox：分块、幂等、重试、删除审计 |

已做的数据质量检查：无会话计数不一致、工具调用计数不一致、跨 owner 父子关系、循环父子关系、重复 external id、无效 JSON 或孤儿消息。

当前历史时间范围为 2026-08-14 至 2026-08-20；原始内容约 990 万字符，单条消息最大约 82,543 字符。为控制托管 reasoning 模型的请求时延，当前实现采用最多 10 条消息、最多 4,000 字符的 chunk，并对超长消息二次切片。

## 3. Mem0 设计

### 3.1 数据边界和身份

AccountIdentity.account_id 是稳定 UUID，作为 Mem0 user_id。API、搜索、删除和 delete_all 均强制按该 ID 过滤；动态删除接口还要求 memory ID 出现在该账号的本地 outbox ledger 中，避免拿到其他账号的 ID 后越权删除。

SQLite 中的历史数据不会被 Mem0 覆盖。每个 HistorySession 生成确定性的 source_key（owner、session、chunk index、脱敏内容 SHA-256），重复回填只会命中已有 job，不会重复入队。

### 3.2 进入 Mem0 的内容

仅选择非空的 user 和 assistant 消息；tool 消息、Hermes 控制事件和上下文 artifact 排除。内容沿用现有 importer 的 redact_text 规则后才进入 outbox/模型调用。Mem0 metadata 包含来源、历史 session 标识、模型、时间和脱敏版本，不包含凭据。

### 3.3 存储和异步处理

- Mem0 SDK：mem0ai==2.0.19。
- 向量库：独立 PostgreSQL mem0 数据库，官方 PGVector adapter，`ansatz_memory_v2` collection，1024 维（匹配 qwen3.7-text-embedding-flash）。旧的 v1 collection 保留但不使用。
- PostgreSQL 扩展：vector 0.8.6；HNSW 和全文索引由 Mem0 collection 初始化。
- worker：memory_worker 管理命令，SQLite 事务抢占、指数退避、最大尝试次数，模型调用不在上传请求内执行。
- telemetry：默认关闭 MEM0_TELEMETRY=false。

### 3.4 DashScope 模型映射

通过 Mem0 的 OpenAI-compatible provider，三个组件默认使用同一个 DashScope base URL；如需故障切换，可为 LLM、Embedding 和 judger 分别配置 endpoint 与 API key，避免 Embedding 能力不足时影响整个记忆链路：

| 组件 | 配置 |
|---|---|
| 记忆分析 LLM | `ZHIPU/GLM-5.3-Flash` |
| Embedding | `qwen3.7-text-embedding-flash`，1024 维 |
| Search judger/reranker | `qwen3.8-flash`，Mem0 `llm_reranker`，`top_k=5` |
| 默认 Base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 默认凭据 | `MEMORY_PROVIDER_API_KEY`，仅位于远端 `server.env`（0600） |

角色级覆盖变量为 `MEMORY_LLM_OPENAI_BASE_URL` / `MEMORY_LLM_API_KEY`、
`MEMORY_EMBEDDER_OPENAI_BASE_URL` / `MEMORY_EMBEDDER_API_KEY` 和
`MEMORY_JUDGE_OPENAI_BASE_URL` / `MEMORY_JUDGE_API_KEY`。未设置角色级变量时，
代码回退到上述默认值。

这里的“judger”落到 Mem0 官方配置中的 `llm_reranker`，只在搜索候选记忆时进行相关性重排，不替代记忆提取 LLM。

### 3.5 HTTP API

认证方式沿用现有 Django/Hermes session：

    POST   /history/api/v1/memory/search/       {"query": "...", "limit": 5}
    GET    /history/api/v1/memory/
    DELETE /history/api/v1/memory/<memory_id>/
    DELETE /history/api/v1/memory/delete-all/

所有接口都使用当前登录账号的 Mem0 user scope；空查询、未归属 memory ID 和未启用 provider 会返回明确错误，不会返回其他用户数据。

## 4. 已部署构件

源码变更集中在 history app：

- history/memory_service.py：身份、脱敏分块、Mem0/DashScope 配置和 CRUD 封装。
- history/models.py + history/migrations/0009_memory_ingest_job.py：outbox ledger。
- history/management/commands/memory_backfill.py：历史回填/预览。
- history/management/commands/memory_worker.py：异步处理。
- history/management/commands/memory_erase_user.py：按用户擦除并标记 ledger。
- history/importer.py：导入成功事务内幂等入队。
- history/views.py、history/urls.py：owner-scoped API。
- docker-compose.mem0.yml：独立 pgvector、认证服务环境变量和 worker overlay。

远端认证镜像已切换为 `localhost/ansatz-auth-service:mem0-20260902-r7`；原镜像标签和部署环境文件均保留，可回滚。远端 overlay 位于 `/data/ansatz-agent/voice-trace/deploy/docker-compose.mem0.yml`。

## 5. 本次验证结果

本地：

- Django check：通过。
- 迁移到 history.0009：通过。
- 全量 Django 测试：271 项通过。
- Ruff（本次 Mem0 代码）：通过。
- Python compileall：通过。

远端：

- auth-service 和 mem0-postgres 均为 healthy；其余现有栈容器保持 healthy。
- SQLite 迁移后：11 users、62 sessions、4,521 messages，完整性仍为 ok。
- 回填 dry-run（初始 12,000 字符 chunk）：62 sessions、205 个可入选消息、74 chunks；随后为降低 DashScope 单请求超时改为 4,000 字符 chunk，重新入队。
- 重新入队后的任务数量以远端 `memory_backfill --dry-run` 为准；旧任务保留为 deleted 审计记录。
- `memory_worker --once` 在关闭开关时会明确不处理 job；本次授权后已启用常驻 worker。
- pgvector collection 已创建；`ansatz_memory_v2` 当前核验为 4 条记录，属于 1 个历史 owner。
- 容器内 mem0、psycopg、psycopg_pool、ollama 均可导入。

DashScope canary（全部为合成文本，不来自 SQLite）：

- `get_memory()` 初始化成功，读取到 GLM、Embedding、judger 三个模型配置。
- LLM JSON 诊断调用成功。
- Embedding 返回 1024 维向量。
- judger 对两个合成候选返回 1 条重排结果。
- 一条明确标记为“需长期记忆”的合成偏好写入 1 条 Mem0 记录，随后删除；删除后该 canary user 的记录数为 0。

授权后回填状态（最近核验快照）：旧的 27 条大 chunk 审计行保留为 deleted；新的 4,000 字符分块共 121 条，其中 succeeded=21、running=1、pending=98、failed=1。失败项为一次 DashScope 请求超时，worker 按退避策略重试；v2 向量表有 4 条、属于 1 个历史 owner，worker 仍在后台运行。SQLite 原始历史未被改写。

SQLite 备份位于：

    /data/ansatz-agent/voice-trace/data/auth/backups/auth-db-pre-mem0-20260902.sqlite3

备份目录权限为 0700，备份文件权限为 0600。部署前环境文件副本为 secrets/server.env.pre-mem0-20260902，未将任何密钥写入仓库或报告。

## 6. 回填运行与运维步骤

用户已完成“允许将所有用户的脱敏历史发送至 DashScope”的授权，远端已在维护窗口启用 `MEMORY_ENABLED=1` 并启动常驻 worker。后续运维按以下规则执行：

1. 保持 Compose 项目名 `ansatz-voice-trace-20260823`，每次都同时加载生产基础栈、SJTU 端口 overlay 和 Mem0 overlay：

       docker compose -p ansatz-voice-trace-20260823 \
         -f deploy/docker-compose.yml \
         -f deploy/docker-compose.sjtu.yml \
         -f deploy/docker-compose.mem0.yml config --quiet

   只有上述 `config --quiet` 成功后才执行 `up -d`。`docker-compose.sjtu.yml` 不能省略：它发布认证服务的 `127.0.0.1:8000`（以及 trace/langfuse 端口）；缺失时容器内部虽可能 healthy，OpenResty 仍会收到 connection refused/502。
2. 通过 `history_memoryingestjob` 观察 pending/failed/succeeded 数量、模型延迟、PostgreSQL 磁盘和 API 限流；失败任务由 worker 指数退避，达到上限后人工复核。
3. 新增历史会在导入事务内幂等入队；常驻 worker 在队列为空时仍保持监听，后续任务无需人工重启。
4. embedding 模型或维度变化时必须新建 collection；不能在同一 pgvector collection 混用维度。
5. 如撤回云端处理授权，将 `MEMORY_ENABLED` 改回 0 并停止 worker；outbox 可保留为待处理状态，不会改写 SQLite 原始历史。

## 7. 回滚和擦除

应用回滚：恢复 AUTH_SERVICE_IMAGE 为原镜像标签，移除或不加载 Mem0 overlay，然后按原 Compose 项目名执行 up -d auth-service。history_memoryingestjob 是向后兼容的附加表，不影响旧版本读取历史。

数据恢复：仅在确认需要时使用部署前 SQLite 备份恢复；恢复前必须停止 auth-service、保留当前数据库副本并核验备份 SHA-256。Mem0 数据库是独立卷，不会因 SQLite 回滚自动删除。

按用户擦除使用 memory_erase_user 命令（必须带 --username 或 --user-id 及 --confirm）。该命令调用 Mem0 delete_all(user_id=...) 并把该用户 ledger 标记为 deleted。删除操作必须保留工单/审计记录；不要使用 Mem0 的全局 reset。

## 8. 官方手册依据

本实现遵循 Mem0 官方 OSS/PGVector/操作手册：

- [OSS overview](https://docs.mem0.ai/open-source/overview)
- [Configuration](https://docs.mem0.ai/open-source/configuration)
- [PGVector](https://docs.mem0.ai/components/vectordbs/dbs/pgvector)
- [Add memories](https://docs.mem0.ai/core-concepts/memory-operations/add)
- [Search memories and scope by user](https://docs.mem0.ai/core-concepts/memory-operations/search)
- [Delete and delete-all](https://docs.mem0.ai/core-concepts/memory-operations/delete)
- [Entity-scoped memory](https://docs.mem0.ai/platform/features/entity-scoped-memory)
- [REST server security](https://docs.mem0.ai/open-source/features/rest-api)

DashScope 兼容性依据阿里云官方文档：

- [DashScope OpenAI-compatible base URL](https://help.aliyun.com/en/model-studio/base-url)
- [GLM-5.3-Flash by Zhipu](https://help.aliyun.com/zh/model-studio/glm-5-3-flash-by-zhipu)
- [Embedding interfaces compatible with OpenAI](https://help.aliyun.com/en/model-studio/embedding-interfaces-compatible-with-openai)

本次选择 SDK + 内部 API，而不是直接公网暴露 Mem0 REST server，是因为现有系统已有 Django session、账号 owner 和脱敏边界；Mem0 只作为记忆层，SQLite 继续承担完整历史留存与审计。
