# Agent History Portal：网络架构与重制需求说明

> 文档版本：1.1
> 编制日期：2026-08-20
> 当前生产代码基线：`45263f2`（功能提交：`926bff0`）
> 当前入口：`https://c2sml.cn/agent/`
> 长期目标入口：`https://agent.c2sml.cn/`
> 文档目的：在不依赖当前 Django、Podman、SQLite 或 Nginx Proxy Manager 具体实现的前提下，重新制作 Agent History Portal 时可作为产品、网络、安全、数据和验收的统一需求基线。

---

## 0. 阅读方式与范围

### 0.1 本文覆盖范围

本文的主体是 **Agent History Portal**，即让多个授权用户安全地导入、浏览、搜索、导出和维护 Hermes/Agent 对话历史的 Web 门户。

它同时记录门户当前挂载在 `c2sml.cn` 主站 `/agent/` 路径下时必须兼容的网络边界，避免重制时误伤主站既有路径。

### 0.2 本文不包含

- 任何密码、Cookie、Token、私钥、`.env` 内容或数据库原文；
- 真实用户导出的聊天内容；
- 尚未实现的历史总结、模型调用、API 充值、支付、订单、余额或 API Key 管理功能；
- 对 NPM、Podman、Django、SQLite 的强制绑定。它们是当前实现，不是未来唯一技术选择。

### 0.3 术语

| 术语 | 含义 |
|---|---|
| Owner | 历史数据的逻辑归属用户；普通用户仅可读取自己的 Owner 数据。 |
| Uploader | 实际执行导入的人；可与 Owner 不同，仅管理员可选择其他 Owner。 |
| Root session | 一个顶层 Agent 对话。 |
| Direct subagent thread | 隶属于一个 root session 的一层子 Agent 线程。当前产品不支持孙线程。 |
| Message | 导入的单条原始消息（user、assistant、tool、system 等）。 |
| Turn | 展示层派生结构：以真实 user 请求为起点组织消息，非原始持久化实体。 |
| 精确用量 | 上游 Hermes 导出的会话累计 Input/Output/Cache/Reasoning token 字段。 |
| Context allocation | 按保存消息内容估算的推理、代码、对话、工具、系统/控制内容比例；不是账单精确数。 |
| 当前实现 | 已在生产运行并经验证的事实。 |
| 重制建议 | 为降低风险、改善扩展性而提出的后续实现建议，可用不同技术达成。 |

### 0.4 网站设计目的（详细列表）

这一节回答“为什么要做这个网站”。它不是页面清单，也不是当前技术栈约束；未来重制时，若某项实现、接口或交互发生争议，应优先回到这些设计目的判断是否仍满足产品本意。

| 目的 ID | 网站要解决的问题 | 对用户/管理员的价值 | 重制时必须体现的设计原则 |
|---|---|---|---|
| PUR-01 | Agent 对话散落在本机 session、CLI 导出和临时运行记录中，难以长期查找与复盘。 | 把历史变成可浏览、可搜索、可导出的档案，而不是一次性终端输出。 | 历史必须持久化、按时间有序、支持稳定外部 ID；不能只保留摘要或最后几轮内容。 |
| PUR-02 | 原始对话中充满工具调用、空 assistant 消息、运行时通知和上下文压缩标记，直接阅读成本高。 | 用户能够先看到真实问题与最终回答，需要时再展开过程证据。 | 用派生 Turn 改善阅读，但不能删除、篡改或伪造原始消息。 |
| PUR-03 | 多人使用同一门户时，聊天记录、工具结果和 personal memory 是高敏感数据。 | 普通用户可以放心浏览自己的历史，而不用担心被其他普通用户查看。 | Owner 隔离必须在服务端数据访问层实现，并覆盖列表、详情、搜索、统计、导入、导出与分页。 |
| PUR-04 | 管理员需要协助导入、排查和账号维护，但不应让管理便利变成任意改写历史。 | 管理员能完成必要运维，历史内容仍保持可追溯。 | 管理后台对历史和导入批次默认只读；账号管理与内容修改权限分离。 |
| PUR-05 | 历史导入可能携带密码、Cookie、Authorization、私钥或上游 metadata 中的秘密。 | 降低把个人或服务器凭据再次扩散到门户、备份、日志或导出的风险。 | 可信本机先脱敏，服务器再递归脱敏；任何单层脱敏都不能被当作充分保证。 |
| PUR-06 | 上传失败、坏 JSON、错误 parent 关系或超大文件不能造成半份数据、关系损坏或资源耗尽。 | 用户可明确知道导入是否成功；管理员可审计失败原因。 | 导入应先完整校验、受大小/数量限制、在事务中原子写入，并记录 ImportBatch。 |
| PUR-07 | 用户需要反复上传同一份导出或恢复导入，重复写入会使历史和统计膨胀。 | 可安全重试导入并知道哪些 session 被跳过。 | 以 Owner + stable external session ID 做幂等；当前语义是整 session 跳过，不偷偷合并 message。 |
| PUR-08 | Agent 常把工作委派给 subagent；只展示根对话会丢失执行脉络，单独平铺又会混乱。 | 用户既能从 root 任务进入，又能查看相关 delegated work。 | 仅支持同 Owner 的一层 direct thread；child 只在 parent 详情内嵌，不能膨胀为独立根历史。 |
| PUR-09 | 工具调用与工具结果是复盘执行过程的重要证据，但默认全部展开会淹没人类对话。 | 用户既保留证据，也获得低噪声阅读体验。 | 工具上下文默认折叠、可按需展开；memory 调用仅展示最小必要 arguments，不展示无关结果 payload。 |
| PUR-10 | 用户需要把自己的历史带走、迁移或保存，而不能被某一实现锁定。 | 支持备份、迁移、复现与灾难恢复。 | 导出必须 owner-safe、可流式处理并尽可能 round-trip 回当前导入格式；保留允许的 metadata 与 token 字段。 |
| PUR-11 | “Token 用量”若用估算冒充计费数据，会误导成本判断与模型使用复盘。 | 用户能区分来源可靠的累计用量与页面推导的内容构成。 | 精确 session counters 与 Context allocation 必须在数据模型、UI 和文案中明确分层；未知不显示为 0。 |
| PUR-12 | 仅看 Input/Output 无法判断一条历史为何变长、代码和工具是否主导上下文。 | 用户可以理解推理、代码、聊天、工具和系统内容的相对组成。 | Context allocation 仅作为可重建的启发式估算；图表百分比、无数据状态与可访问标签必须正确。 |
| PUR-13 | 用户可能需要维护自己的 MEMORY.md/USER.md，但不应把它和对话自动归纳、多人共享记忆混为一谈。 | 每个账号拥有独立、可编辑、可预览的个人运行资料。 | Memory pool 必须 Owner 独立；不自动从历史提取、合并或跨用户传播偏好。 |
| PUR-14 | 门户需要让外部用户能到达登录页，同时不能因公开入口而公开历史或运维接口。 | 公开入口与受保护数据可以共存。 | 登录页公开；历史、导出、导入、Memory、Admin 和预留 API 必须认证、CSRF 防护、限速和绝对 session 校验。 |
| PUR-15 | 网站长期运行需要能回答“谁导入了什么、何时失败、如何恢复、当前运行哪个版本”。 | 降低运维依赖个人记忆，便于交接和故障恢复。 | 记录 Uploader、ImportBatch、版本、迁移、备份与恢复点；备份必须能实际恢复验证。 |
| PUR-16 | 当前门户挂在主站路径下，任何不谨慎的反代改动都可能破坏不相关的 PHP、静态、BBS 或文件服务。 | 门户可以持续演进而不以主站故障为代价。 | 网络配置必须将 `/agent/` 视为受控子系统；修改前后回归验证既有主站关键路径。 |
| PUR-17 | 小屏设备上长代码、命令、路径和高密度统计容易被裁切或无法理解。 | 用户可在桌面和移动端可靠阅读、复制和定位重要历史。 | 不允许整页或不可访问的局部横向裁切；代码可滚动/换行并有明确提示，图表不只依赖颜色。 |
| PUR-18 | 当前实现采用单机 SQLite、路径前缀和容器编排，未来技术栈可能改变。 | 可逐步升级数据库、独立域名、观测和部署平台而不丢失业务语义。 | 将 Owner、stable ID、导入幂等、导出、审计、安全边界和数据口径作为不变契约，而非把 Django/Podman 当作产品本身。 |
| PUR-19 | 历史总结、自动偏好抽取、API 充值等看似便利的能力，会引入模型调用、付款、隐私和治理风险。 | 用户不会误以为预留页面已经在处理数据、收费或调用模型。 | 预留功能必须显式不可用、无写入、无模型调用；启用前独立设计与审查。 |
| PUR-20 | 重制项目往往因只复制 UI 而丢失权限、数据迁移、回滚和验收细节。 | 后续团队能够依据同一份可验证清单重建而不是依赖口头传承。 | 文档、架构图、数据契约、测试、部署流程、备份恢复与已实现/预留边界必须一同交付。 |

---

## 1. 产品目标与非目标

### 1.1 产品目标

门户应成为一个安全、可审计、按账号隔离的 Agent 历史档案系统，满足以下目标：

1. 保留可读的完整原始历史，而不是只保存摘要；
2. 按真实用户请求组织对话，降低工具循环和运行时通知对阅读的干扰；
3. 支持可信本机生成的已脱敏 JSON/JSONL 导入；
4. 用 Owner 隔离所有普通用户数据访问；
5. 让管理员管理账号、查看全局数据和为指定 Owner 导入；
6. 支持导出可见历史，使数据可迁移、可备份；
7. 提供会话级精确 Token 累计和内容构成估算；
8. 在公开登录入口的前提下，仍保持 CSRF、会话、限速、权限和安全头边界；
9. 可部署在反向代理之后，且不直接将应用端口暴露到公网。

### 1.2 明确非目标

下列能力不属于当前产品，未来若要实现必须单独立项、设计数据模型与安全评审：

- 对 Hermes 历史的后台自动同步或活动 SQLite 复制；
- 对已导入重复 session 做 message-level 补写或冲突合并；
- 自动从历史提取偏好、长期 memory、技能或共享知识；
- 由模型生成历史总结、共性流程、评分、评价或推荐；
- API Key 托管、额度充值、支付、订单、账单、余额；
- 公共注册、自助找回/修改密码、邀请流；
- 媒体原件存储和图像 OCR；
- 多层 subagent 树、跨 Owner 线程关系。

---

## 2. 当前已验证网络与部署架构（As-Is）

### 2.1 外部入口与路由边界

当前门户复用主站 HTTPS origin：

```text
Internet Browser
  └─ HTTPS :443 → c2sml.cn
       └─ Nginx Proxy Manager (NPM)
            ├─ /agent/          → agent-history-web:8000（私有容器网络）
            ├─ /cv/             → cv-php8:9000
            ├─ /xzqtest/        → 静态/PHP 主站内容
            ├─ /xuzhiqin/       → 静态主站内容
            ├─ /bbs、/minio 等  → 既有主站服务
            └─ /                → 既有静态主站
```

当前公网入口与行为：

| 路径 | 行为 | 重制兼容要求 |
|---|---|---|
| `/agent/` | NPM 去除 `/agent` 前缀，转发到门户；应用再跳转 dashboard/login | 必须保持前缀、重写、Cookie Path 和 URL 反向解析一致，直至迁移专用子域。 |
| `/agent/accounts/login/` | 对外公开的登录页 | 必须公开可达，但不得泄露任何历史内容。 |
| `/agent/history/` | 已登录后的历史列表 | 匿名必须跳转登录。 |
| `/agent/history/usage/` | 已登录后的 Token 总览 | 匿名必须跳转登录。 |
| `/agent/api/session/` | 客户端会话状态 API | 匿名返回 401 JSON，不缓存。 |
| `/agent/healthz` | 边缘固定 404 | 不能把内部健康信息暴露到公网。 |
| `/agent/accounts/password_reset/` | 不提供 | 必须保持 404，除非另行设计安全账号生命周期。 |
| `/cv/`、`/xzqtest/`、`/xuzhiqin/` | 既有主站路由 | 门户变更后仍必须回归验证。 |

### 2.2 当前网络组件

| 层 | 当前组件 | 当前职责 | 未来重制约束 |
|---|---|---|---|
| DNS/TLS | `c2sml.cn` + Let’s Encrypt | HTTPS 终止、主站入口 | 优先迁移到 `agent.c2sml.cn`，专用证书与独立 origin。 |
| 边缘反代 | Nginx Proxy Manager 容器 `npm` | 按路径路由、TLS、Nginx location 管理 | 可替换为 Nginx、Caddy、Traefik、云 LB；必须具备路径/Host、TLS、请求大小、转发头和回滚能力。 |
| 应用 | `agent-history-web` | Django/Gunicorn Web 服务 | 应用必须无公网 `ports` 映射，仅接受反代私网访问。 |
| 私网 | `nginx-proxy-manager_default` | NPM 与应用容器通讯 | 应使用稳定服务名/网络别名，不依赖易变容器 IP。 |
| 数据 | `/var/lib/agent-history/db.sqlite3` | SQLite 数据库和静态文件目录 | 可迁移为 PostgreSQL + 对象存储；必须保留数据归属、备份、审计和恢复能力。 |
| 备份 | `/var/backups/agent-history` | root-only SQLite 在线备份 | 后续必须提供等价的一致性备份、校验与恢复演练。 |
| 进程守护 | systemd + podman-compose | 启动、重启、主机目录初始化 | 可迁移为 Kubernetes、Compose、Nomad 等，但要保留可重复部署与健康检查。 |

### 2.3 当前端口与暴露面

当前主机实测监听概览：

| 端口 | 当前用途 | 产品安全要求 |
|---|---|---|
| 22 | SSH 运维 | 仅公钥认证；应由云安全组限制管理来源。 |
| 80/443 | NPM 的 HTTP/HTTPS 入口 | 对外公开；80 应重定向到 HTTPS。 |
| 81 | NPM 管理接口 | 当前应只绑定回环，使用 SSH 隧道管理；不得公开。 |
| 8000 | 门户内部 Gunicorn | 不得绑定宿主机或公开。 |
| 8080/9000 | 现有主站/NPM 后端映射 | 与门户无直接关系；当前有既有依赖，重制门户时不得擅自删除。 |
| 3306/33060 | 主机既有 MySQL | 不属于门户数据库；不得让门户默认依赖或暴露它。 |

### 2.4 当前反代关键契约

`/agent/` 反代必须实现以下语义：

- 转发原始 `Host`；
- 转发可信的 `X-Forwarded-Proto=https`；
- 转发 `X-Forwarded-Host` 与 `X-Forwarded-Prefix=/agent`；
- 由边缘剥离 `/agent/` 后再转发给应用；
- 上传大小至少允许 `26 MiB`；
- 禁止公开 `/agent/healthz`；
- 不依赖客户端自送的伪造 `X-Forwarded-For`；
- 所有与门户无关的既有 NPM location 必须原样保留。

### 2.5 当前容器启动顺序

当前容器入口按以下顺序执行：

1. `migrate --noinput`；
2. `collectstatic --noinput`；
3. 启动 Gunicorn，监听容器内 `0.0.0.0:8000`；
4. 容器 healthcheck 仅请求容器内 `/healthz`；
5. NPM 通过私网服务名 `agent-history-web:8000` 转发请求。

**重制要求：** 数据库 schema 变更必须可演练、可回滚；静态资源发布必须和应用版本一致；健康检查必须不依赖公网。

---

## 3. 目标网络架构（To-Be）

### 3.1 推荐目标状态

推荐将门户从共享 origin 的 `/agent/` 迁移为独立子域：

```text
agent.c2sml.cn
  └─ TLS terminator / WAF / reverse proxy
       └─ private application service
            ├─ web/API service
            ├─ relational database
            ├─ object storage（如未来允许附件）
            ├─ backup/restore worker
            └─ observability（日志、指标、告警）
```

独立子域的主要价值：

- 降低与主站共享 origin 带来的同源 XSS/第三方脚本风险；
- 独立 Cookie scope、CSP、发布节奏和 CDN/缓存策略；
- 减少路径重写、前缀 URL、静态资源和 admin 路由复杂度；
- 可单独迁移、扩缩容和设置访问策略。

### 3.2 不迁移子域时的最低要求

若 DNS 暂不可控，仍可在 `/agent/` 继续运行，但必须：

- 保持独立 session/CSRF Cookie 名称和 `Path=/agent/`；
- 保持绝对 URL 与反向解析支持前缀；
- 不把 `/agent` 的 CSP 降级为与主站一致；
- 任何 NPM 保存必须使用完整、已版本管理的 Advanced 配置；
- 每次变更回归验证 `/`、`/agent/`、`/cv/`、`/xzqtest/`、`/xuzhiqin/` 和既有主站关键路径；
- 在产品和风险文档中明确：Cookie Path 不等于同源隔离。

### 3.3 数据库演进建议

当前 SQLite 适合低并发、单节点、手工导入门户。未来重制建议采用 PostgreSQL 或等价关系型数据库，原因：

- 更可靠的并发读写；
- 更清晰的备份、恢复、复制和迁移工具；
- 更好的全文检索、审计与可观测性；
- 可在数据库层实现更多完整性约束。

无论使用何种数据库，都必须保留：

- `owner + external_id` 的唯一性；
- `session + source_message_id` 的条件唯一性；
- Owner、Uploader、Parent 的关系与删除策略；
- 原始 metadata 与结构化字段并存；
- 可重放的导入批次审计；
- 历史导入数据与派生展示/统计数据的可区分性。

---

## 4. 角色、权限与数据隔离需求

### 4.1 角色定义

| 角色 | 能力 |
|---|---|
| 匿名访客 | 打开登录页；不得读取任何历史、导出、Memory、管理或预留功能数据。 |
| 普通用户 | 只可管理自己的 Owner 范围：查看、搜索、导入、导出、维护自己的 Memory pool。 |
| 超级管理员 | 管理账号；查看全站历史；导出全站可见历史；导入时可选择 Owner。 |
| 运维管理员 | 主机、反代、部署、备份与恢复；不应通过日志或命令泄露应用凭据和聊天内容。 |

### 4.2 强制授权规则

| ID | 需求 |
|---|---|
| AUTH-01 | 所有历史相关 endpoint 必须先验证已登录，并验证绝对 session 过期时间。 |
| AUTH-02 | 普通用户的每个查询、聚合、详情、搜索、导出、筛选和分页必须从 Owner-scoped queryset/数据访问层开始。 |
| AUTH-03 | 仅超级管理员可跨 Owner 查看、导出和选择导入 Owner。 |
| AUTH-04 | 不得信任普通用户在表单、JSON、URL 或前端请求中提交的 Owner ID。 |
| AUTH-05 | 直接用 session 主键访问其他 Owner 的详情必须返回 404/拒绝，不得泄露“是否存在”。 |
| AUTH-06 | subagent child 必须与 parent 属于相同 Owner 才能参与页面、统计、导出、搜索或聚合。 |
| AUTH-07 | 后台管理站点必须仅允许 active superuser；历史记录在后台默认只读。 |
| AUTH-08 | 不提供公共注册、邀请、密码重置或密码修改入口，除非设计了独立审批与交付流程。 |

---

## 5. 核心数据模型需求

### 5.1 必须保留的实体

#### A. HistorySession

| 字段/关系 | 需求 |
|---|---|
| `owner` | 必填，业务数据隔离主键。 |
| `uploader` | 必填，记录实际导入执行人。 |
| `parent_session` | 可空；仅允许一层 direct thread；删除 parent 时必须受保护。 |
| `external_id` | 必填，上游稳定 session ID；同一 Owner 内唯一。 |
| `title/source/model` | 可选展示元数据，限制长度并脱敏。 |
| `started_at/ended_at/end_reason` | 保存上游时间和结束原因；允许未知。 |
| `message_count/tool_call_count` | 导入时确定的会话计数。 |
| Token 计数 | `input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_write_tokens`、`reasoning_tokens`：非负大整数。 |
| `raw_metadata` | 保留允许的、已经脱敏的上游 metadata，支撑 round-trip 与未来演进。 |
| `imported_at` | 本门户导入时间，用于审计与展示。 |

#### B. HistoryMessage

| 字段/关系 | 需求 |
|---|---|
| `session` | 必填，级联删除仅适用于明确删除 session 的管理策略；当前产品不开放删除。 |
| `source_message_id` | 上游稳定消息 ID；同一 session 内非空值唯一。 |
| `role` | 必填；支持 user、assistant、tool、system、developer 及受控运行时类型。 |
| `content` | 已脱敏、长度受限的文本或 JSON 序列化文本。 |
| `timestamp` | 可空；排序必须在同一时间戳下退回数据库 ID。 |
| `tool_name/tool_call_id/tool_calls` | 保留工具调用的必要展示和审计信息，必须递归脱敏。 |
| `raw_metadata` | 已脱敏的受限 metadata，例如 reasoning payload 与 display metadata。 |

#### C. ImportBatch

必须记录：Owner、Uploader、原文件名（安全 basename）、文件 SHA-256、状态、导入/跳过 session 数、导入 message 数、有限长度错误摘要、创建与完成时间。

#### D. UserMemoryPool

每个 Owner 一个独立的 Memory pool，保存：

- `MEMORY.md` 文本；
- `USER.md` 文本；
- 最后更新时间。

它不是从历史自动抽取的共用 memory，也不应跨 Owner 读取。

### 5.2 父子关系约束

| ID | 需求 |
|---|---|
| DATA-01 | 一个 root session 可有 0..N 个 direct subagent threads。 |
| DATA-02 | 禁止 grandchildren；嵌套导入深度大于一必须 fail closed。 |
| DATA-03 | 禁止孤儿 parent、self-parent、parent cycle 和 conflicting parent。 |
| DATA-04 | child 与 parent 的 Owner 必须一致；数据访问层仍要二次验证该规则。 |
| DATA-05 | 线程显示、搜索、过滤、导出和 Token 聚合都必须只使用合法同 Owner direct threads。 |

### 5.3 Token 与上下文用量语义

| ID | 需求 |
|---|---|
| USAGE-01 | 上游存在的会话累计 Input/Output/Cache read/Cache write/Reasoning 必须作为精确的 session 数保存，不能仅留在 JSON。 |
| USAGE-02 | 所有精确计数必须是非负整数并受 signed bigint 上限约束。 |
| USAGE-03 | 不得从 session 总数倒推单条 message 的 Input/Output；上游没有逐调用数据时必须标为不可用。 |
| USAGE-04 | 缺失字段必须区分“未知”和“0”；历史数据无可靠来源的指标应隐藏或有显式 availability 语义。 |
| USAGE-05 | 详情页可显示 Context allocation，但必须声明其为当前保存内容的启发式估算，不是精确计费、tokenizer 结果或模型 context window。 |
| USAGE-06 | Context allocation 分类至少包括 reasoning、code、conversation、tools、system/control；必须避免 reasoning、代码和工具 payload 重复计数。 |
| USAGE-07 | 百分比必须严格合计 100%，空输入不得除零，所有 SVG/图表几何值必须通过服务器端验证。 |

---

## 6. 基本功能详细需求

优先级：P0 = 必须有；P1 = 本次重制强烈建议有；P2 = 可后续迭代。

### 6.1 登录、会话和客户端状态

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-AUTH-01 | P0 | 提供公开 HTTPS 登录页，使用用户名和密码认证。 | 登录成功进入概览；失败显示通用错误，不暴露账号存在性。 |
| FUNC-AUTH-02 | P0 | 实施登录失败限速/锁定。当前策略为 10 次失败后锁定、1 小时冷却、成功后清零；重制可调整参数但必须有同等防护。 | 连续失败达到阈值返回 429 或等价锁定响应；成功登录后计数重置。 |
| FUNC-AUTH-03 | P0 | 使用 HttpOnly、Secure、SameSite=Lax 的 session Cookie；临时路径部署时 Cookie Path 为 `/agent/`。 | 浏览器检查 Cookie 不可被 JS 读取，HTTPS 下 Secure 生效。 |
| FUNC-AUTH-04 | P0 | 建立服务器端绝对 session 到期时间；每个受保护 endpoint 同时检查 Django 登录和绝对过期。 | 过期 session 访问任意历史功能都跳转登录或返回 401。 |
| FUNC-AUTH-05 | P1 | 提供 `GET /api/session/`，仅返回 `authenticated`、username、server_time、session_expires_at 等最小客户端状态。 | 匿名或过期返回 `401` + `Cache-Control: no-store`；不返回权限清单、密钥、历史统计。 |
| FUNC-AUTH-06 | P0 | 退出必须清除 session 并跳转登录页。 | 退出后刷新受保护页面不再可读。 |

### 6.2 概览 Dashboard

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-DASH-01 | P0 | 显示当前用户作用域内 root session 数、合法 direct thread 数、消息数、工具调用数、memory 操作数。 | 普通用户看不到其他 Owner 的任一统计。 |
| FUNC-DASH-02 | P1 | 显示最近 7 天按 session 开始时间聚合的活动柱状图。 | 无数据时图表稳定显示；未知开始时间回退导入时间。 |
| FUNC-DASH-03 | P0 | 显示最近 root sessions，包含标题、来源、模型、聚合后的消息/工具数和时间。 | 子线程不单独成为最近历史卡片。 |
| FUNC-DASH-04 | P0 | 提供导航快捷入口：历史、Token 用量、Memory、导入；预留功能可展示但必须显式标记“预留”。 | 已实现入口有效；预留入口不得写入业务数据。 |

### 6.3 历史列表、搜索和筛选

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-LIST-01 | P0 | 只列出 root session；子线程在详情页内嵌展示。 | 列表不会把 child 当作独立历史。 |
| FUNC-LIST-02 | P0 | 支持按标题、external ID、source、model、主 session 消息内容、合法 child 的上述字段和消息内容搜索。 | 搜索 child 内容时返回对应 root，但不返回无关或跨 Owner root。 |
| FUNC-LIST-03 | P1 | 支持按 Uploader 多选 OR 筛选；root 或合法 child 的 uploader 匹配时返回 root。 | 提交无效/越权 uploader 参数时返回空结果，不扩大作用域。 |
| FUNC-LIST-04 | P0 | 支持稳定分页，保留搜索与 uploader 参数。当前页大小为 25，可配置。 | 翻页不丢筛选条件。 |
| FUNC-LIST-05 | P0 | 每行显示标题、来源、模型、Uploader、合法 child 数、聚合消息/工具数、开始时间及聚合 Input/Output。 | 数字使用一致的千位分隔；未知时间和模型有安全的占位文案。 |
| FUNC-LIST-06 | P1 | 所有长标题应保留完整 title attribute/可访问名称，同时可在视觉上省略。 | 长标题不破坏布局且能查看完整文本。 |

### 6.4 单条会话详情与可读展示

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-DETAIL-01 | P0 | 显示 root session 元信息：标题、来源、模型、开始时间、消息数、Uploader。 | 仅合法 Owner/管理员可访问。 |
| FUNC-DETAIL-02 | P0 | 将 root 的原始 messages 派生为 Turn：每个真实 user 请求开启一个 turn。 | 自动委托通知、上下文压缩等运行时包装不得误计为 user turn。 |
| FUNC-DETAIL-03 | P0 | 完整保存历史，默认折叠工具循环、tool 结果、空 assistant、system/developer 与运行时上下文。 | 用户请求和最终 assistant 回答可直接阅读；展开后仍能看到原始上下文。 |
| FUNC-DETAIL-04 | P0 | Markdown 服务端渲染；禁用原始 HTML、危险链接和远程图片加载。 | XSS payload 不执行；链接有 `nofollow noreferrer`；图片显示安全占位。 |
| FUNC-DETAIL-05 | P1 | memory 工具调用单独以 arguments-only 格式展示 action/content，隐藏 memory tool result。 | 页面不显示 call ID、target、usage 或不必要 payload；混合 memory/普通 tool 调用不丢普通调用。 |
| FUNC-DETAIL-06 | P0 | 在 root 下内嵌合法 direct subagent threads，各自保留其会话展示与元数据。 | child URL 可重定向到 parent 的 thread anchor；跨 Owner/多层 child 返回 404。 |
| FUNC-DETAIL-07 | P1 | 长代码、路径、表格在移动端必须可完整查看和复制。 | 不允许不可访问的水平裁切；代码块可横向滚动或安全换行，并有明显视觉提示。 |

### 6.5 Token 用量和上下文分配

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-USAGE-01 | P0 | 提供 `/history/usage/` 总览，展示当前 Owner 范围的 Input、Output、Cache read、Reasoning 精确累计值。 | 普通用户总数不包含其他 Owner 或畸形 child。 |
| FUNC-USAGE-02 | P0 | Token 总览按 root 显示每条历史及合法 direct threads 的聚合 Input/Output/Reasoning。 | root 聚合与详情聚合一致。 |
| FUNC-USAGE-03 | P0 | 详情展示 root + 合法 direct threads 的精确 Input、Output、Cache read/write、Reasoning。 | 数据来自结构化 session 字段，而非页面临时猜测。 |
| FUNC-USAGE-04 | P0 | 详情显示 Context allocation 堆叠条和图例。SVG 或等价可视化必须不依赖远程 JS。 | 段宽、比例和图例数字一致；总计 100%；无内容时显示明确空状态。 |
| FUNC-USAGE-05 | P0 | 页面必须保留精确值与估算值的口径说明。 | 文案明确“不等同于逐 API 计费 input，也不代表最大 context window”。 |
| FUNC-USAGE-06 | P1 | 技术计数应稳定使用千位分隔，不能被 locale 意外取消。 | 例如 49800000 渲染为 `49,800,000` 或产品明确选择的统一格式。 |

### 6.6 JSON/JSONL 导入

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-IMPORT-01 | P0 | 接受 UTF-8 `.jsonl` 和 `.json` 文件；普通用户导入到自己，管理员可选择 active Owner。 | 普通用户伪造 Owner 字段无效。 |
| FUNC-IMPORT-02 | P0 | 当前限制：文件 25 MiB、最多 2,000 sessions、100,000 messages、每 session 20,000 messages、每 message 2,000,000 字符；重制可配置但必须有上限。 | 超限、空文件、坏 JSON、错误扩展名均拒绝。 |
| FUNC-IMPORT-03 | P0 | 支持包含 `subagent_threads` 的嵌套 JSON；导入时扁平化为 parent relationship。 | 仅一层合法 child；深层、冲突 parent、孤儿、自环、cycle 均原子失败。 |
| FUNC-IMPORT-04 | P0 | 导入前完整验证；数据库写入使用事务。 | 任一验证失败时不创建部分 history；ImportBatch 记录失败。 |
| FUNC-IMPORT-05 | P0 | 同一 Owner 的重复 external session ID 必须幂等跳过，不覆盖既有历史。 | 重复导入报告 skipped sessions，消息数不重复。 |
| FUNC-IMPORT-06 | P0 | 上传内容和 metadata 必须在服务端递归二次脱敏，覆盖敏感键、Authorization/Cookie、PEM、常见中英文秘密标签和嵌套 JSON。 | 导入后数据库中不保留命中规则的明文秘密。 |
| FUNC-IMPORT-07 | P0 | 不在服务器永久保存原上传文件。仅保存安全文件名、SHA-256 和导入批次审计。 | 上传临时文件在请求完成后不可作为历史原件访问。 |
| FUNC-IMPORT-08 | P1 | 导入 response 显示新增 sessions、messages、跳过 sessions，并保存错误摘要。 | 用户能确定结果；管理员可在只读后台审计 ImportBatch。 |

### 6.7 历史导出

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-EXPORT-01 | P0 | 提供 JSONL 导出；每条 root session 一行，合法 child 嵌套在 `subagent_threads`。 | 普通用户只导出自己可见的 root/合法 child。 |
| FUNC-EXPORT-02 | P0 | 导出必须包含 session 精确 token 字段、允许的 metadata、messages、工具字段和 uploader 信息。 | 导出可被导入器再次接受，除非产品版本有明确兼容声明。 |
| FUNC-EXPORT-03 | P0 | 使用流式响应、`application/x-ndjson`、下载文件名和 `X-Content-Type-Options: nosniff`。 | 大导出不需一次性建立整份内存响应。 |
| FUNC-EXPORT-04 | P1 | 所有导出都应记录审计事件（谁、何时、多少 root/child、是否管理员全局导出）。 | 审计日志不写入历史正文或敏感文件。 |

### 6.8 个人 Memory pool

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-MEM-01 | P0 | 每个用户可分别编辑/上传 `MEMORY.md` 和 `USER.md`。 | 两份内容可独立更新，单边上传不得清空另一边。 |
| FUNC-MEM-02 | P0 | 仅接受 UTF-8 `.md/.markdown` 文件；每份最大 200,000 字符。 | 格式、编码、大小错误都显示表单错误。 |
| FUNC-MEM-03 | P0 | 仅当前 Owner 可读取/修改其 Memory pool。 | 用其他用户 ID/URL 参数不能读取或写入别人的内容。 |
| FUNC-MEM-04 | P1 | 支持安全 Markdown 预览与最后更新时间。 | 预览不执行 HTML/JS。 |

### 6.9 管理后台与账号维护

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-ADMIN-01 | P0 | 使用仅 superuser 可访问的管理站点管理用户。 | 普通用户访问 admin 不能进入管理功能。 |
| FUNC-ADMIN-02 | P0 | HistorySession、HistoryMessage、ImportBatch 在后台默认只读。 | 管理员不能通过后台意外改写历史正文/归属。 |
| FUNC-ADMIN-03 | P1 | 后台提供按 Owner、Uploader、source、model、import status 搜索和筛选。 | 管理员可排查导入问题而无需直接打开数据库。 |
| FUNC-ADMIN-04 | P0 | 管理员重置/创建密码必须通过受控带外交付，不写入 Git、命令参数、页面或日志。 | 凭据仅驻留于受控本机 mode-600 文件或指定密码管理工具。 |

### 6.10 预留功能必须保持不可用

| ID | 优先级 | 详细需求 | 验收标准 |
|---|---:|---|---|
| FUNC-RES-01 | P0 | “历史总结”可作为登录后预告页和只读状态 API 存在，但 `available=false`。 | 创建请求返回 503，`writes_performed=false`，不调用模型、不写数据库。 |
| FUNC-RES-02 | P0 | “API 充值”可作为登录后预告页和只读状态 API 存在，但 `available=false`。 | 下单请求返回 503，不接收 API Key/支付信息，不创建订单或余额。 |
| FUNC-RES-03 | P0 | 若未来启用任一预留功能，必须先独立完成需求、威胁建模、数据模型、账务/审计和验收。 | 不允许仅删除 503 就上线。 |

---

## 7. 安全、隐私与合规需求

### 7.1 必须的安全控制

| ID | 需求 |
|---|---|
| SEC-01 | 生产 `DEBUG=false`；缺少、弱或占位 `SECRET_KEY` 时 fail closed。 |
| SEC-02 | 通过 `SECURE_PROXY_SSL_HEADER` 信任唯一、已受控反代的 HTTPS 头；不得盲目信任任意客户端转发头。 |
| SEC-03 | CSRF 保护所有状态变更表单/API；session/CSRF Cookie 使用 Secure、HttpOnly（session）、SameSite。 |
| SEC-04 | 公共门户 CSP 至少为：`default-src 'self'`、禁止 object/frame、禁止任意第三方脚本、只允许 self 样式；Admin 若需内联资源必须限缩到 admin 路径。 |
| SEC-05 | 添加 `X-Content-Type-Options: nosniff`、`Referrer-Policy: same-origin`、`Permissions-Policy`、`X-Frame-Options: DENY` 或等价策略。 |
| SEC-06 | Markdown 必须禁用 raw HTML，链接添加 `nofollow noreferrer`，图片不得从远程 URL 自动加载。 |
| SEC-07 | 对导入字段、文件名、消息长度、JSON 结构、数值和时间严格校验。 |
| SEC-08 | 公网不得暴露应用端口、数据库端口、NPM 管理端口或内部健康细节。 |
| SEC-09 | 密码、Token、Cookie、私钥、环境文件、原始导出均不得写入 Git、聊天、命令参数、公开日志或文档。 |
| SEC-10 | 上传前脱敏不是唯一边界；服务端必须再次脱敏，并将原始导出视作禁止上传。 |

### 7.2 当前已知风险与重制改进

| 风险 | 当前状态 | 重制建议 |
|---|---|---|
| 与主站共享 browser origin | `/agent/` 与主站同源 | 优先迁移 `agent.c2sml.cn`，独立 TLS/CSP/Cookie/origin。 |
| HSTS 策略差异 | NPM/应用配置需整体协调 | 不在所有相关子域 HTTPS 完成前盲目 preload；统一由边缘治理。 |
| 单机 SQLite | 适合当前规模 | 中长期换 PostgreSQL，并建立 PITR/复制/演练策略。 |
| 既有 8080/9000 host 映射 | 主站依赖未知 | 单独依赖清点后才收口，不与门户重制混改。 |
| 自动同步 | 尚未实现 | 设计双层 idempotency、审计、冲突语义和重试后再启用。 |
| 大段代码的移动阅读 | 当前详情已有长内容 | 代码块必须可滚动/复制；提供折叠和锚点导航。 |

---

## 8. 非功能需求

### 8.1 性能与容量

| ID | 需求 |
|---|---|
| NFR-PERF-01 | 列表、详情、Token 总览必须分页或限制查询，避免一次加载全部 session/messages。 |
| NFR-PERF-02 | 详情仅预取 root 和合法 direct threads 的 messages；不得 N+1 查询。 |
| NFR-PERF-03 | 导出必须流式生成；大文件导入应受硬上限和事务保护。 |
| NFR-PERF-04 | Token 聚合应使用结构化列和数据库聚合，不能每次解析 metadata JSON。 |
| NFR-PERF-05 | 对 Owner + 时间、Owner + 标题、session + timestamp 建立适当索引。 |

### 8.2 可用性与恢复

| ID | 需求 |
|---|---|
| NFR-OPS-01 | 应用具有内部 health endpoint；边缘不公开该 endpoint。 |
| NFR-OPS-02 | 每次 schema/应用/NPM 变更前后都要备份并执行恢复验证。 |
| NFR-OPS-03 | 备份须一致性校验、加密或 root-only 权限、离线/异地副本策略和定期恢复演练。 |
| NFR-OPS-04 | 部署必须具备明确旧镜像/旧版本回滚步骤；迁移若不可逆必须单独制定数据回滚。 |
| NFR-OPS-05 | 记录服务健康、迁移版本、应用版本、导入失败、登录锁定和备份状态；日志不得记录聊天正文或凭据。 |

### 8.3 可访问性与移动端

| ID | 需求 |
|---|---|
| NFR-UX-01 | 支持至少 320px、375px、500px 和桌面宽度；document/body 不得有非预期横向溢出。 |
| NFR-UX-02 | Token 图例不得只依赖颜色；必须同时有文字、数字、百分比和可访问标签。 |
| NFR-UX-03 | 小字号辅助文本要满足可读对比度；重要数值与状态不应仅以灰色低对比显示。 |
| NFR-UX-04 | 长路径、命令和代码应完整可读、可复制；不得在移动端静默裁切。 |
| NFR-UX-05 | 所有导航、表单、筛选、详情、折叠区应可键盘操作并具备语义标签。 |

### 8.4 国际化与格式

| ID | 需求 |
|---|---|
| NFR-I18N-01 | UI 主语言可为简体中文；技术术语允许保留 Input/Output/Reasoning 等英文。 |
| NFR-I18N-02 | 时间必须指定时区（当前为 Asia/Shanghai），内部存储使用带时区时间。 |
| NFR-I18N-03 | 技术计数的格式必须在真实 locale 中测试，避免本地化取消千位分隔。 |

---

## 9. 重制推荐实现拆分

### 阶段 1：安全骨架与账号

- 建立 HTTPS、反向代理、私网应用、数据库、备份和健康检查；
- 实现用户、superuser、登录、绝对 session、CSRF、限速、安全头；
- 完成 Owner-scoped repository/service 层；
- 写匿名、普通用户、管理员和 IDOR 测试。

**完成标准：** 不导入数据时门户依旧安全，匿名无法读数据，普通用户无法越权，管理员可控创建账号。

### 阶段 2：导入、存储和导出

- 建立 Session/Message/ImportBatch/Uploader/Memory 数据模型；
- 实现 JSON/JSONL 解析、深度/循环校验、二次脱敏、事务和幂等；
- 实现 owner-safe 导出 round-trip；
- 用生产形状的脱敏副本演练迁移、备份与恢复。

**完成标准：** 导入失败不产生部分数据；重复 session 安全跳过；导出可以重新导入；跨 Owner parent 永不出现。

### 阶段 3：阅读体验

- 实现 Dashboard、列表、搜索、Uploader 筛选、分页；
- 实现 Turn 派生、工具折叠、memory 卡片、subagent thread 内嵌；
- 完成 Markdown/XSS、防远程图片和移动端代码阅读；
- 做真实账号浏览器验收。

**完成标准：** 用户能安全、完整、可读地浏览自己的全部历史；工具噪声不会掩盖用户请求和最终答案。

### 阶段 4：Token 与观测

- 实现 typed usage 字段、旧 metadata 回填、导入/导出；
- 实现 owner-safe root/thread 聚合；
- 实现精确 counter 与估算 allocation 的口径区分；
- 用 CSP-safe SVG/无 JS 可视化；
- 在真实 locale 和移动端验证数字、几何和 overflow。

**完成标准：** 每个数字都有明确来源，未知不伪装为 0，估算不伪装为精确。

### 阶段 5：迁移独立子域与长期演进

- 配置 `agent.c2sml.cn` DNS、TLS、独立 proxy host；
- 设置 Host、CSRF trusted origin、Cookie domain/path、CSP 和回归矩阵；
- 再考虑 PostgreSQL、对象存储、自动同步和经审批的派生摘要/记忆。

**完成标准：** 子域迁移不丢会话、不破坏登录、无跨 origin Cookie 问题，主站原有路径正常。

---

## 10. 验收与回归矩阵

### 10.1 安全与权限

- 匿名访问历史、Token、导出、导入、Memory、预留 API：不得返回数据；
- 匿名 session API：401 JSON、`no-store`；
- 普通用户访问他人详情/导出/Memory：404 或拒绝；
- 普通用户不能选择导入 Owner；
- 管理员能查看全站、选择导入 Owner、使用 admin；
- 连续失败登录触发锁定，成功后重置；
- CSRF 缺失的状态变更请求被拒绝；
- XSS/HTML/恶意 Markdown/恶意链接不执行。

### 10.2 数据完整性

- 既有 metadata 回填后五类 Token 合计与迁移前合计一致；
- SQLite/PostgreSQL 一致性检查通过；
- 重复 session 导入跳过；
- subagent 一层导入成功；孤儿、cycle、深层、冲突 parent 原子失败；
- 外 Owner child 指向可见 root 时，列表、详情、导出、Dashboard、Token totals、thread count 都排除；
- 导出后再导入保留 session/message/metadata/token 形状。

### 10.3 UI 与浏览器

- 桌面与 320/375/500px 宽度无 document/body 横向溢出；
- 代码块在移动端完整可见且可复制；
- 列表搜索、Uploader 多选、分页参数保持；
- Token 大数字有统一分隔；
- Context SVG 每段宽度非负、总和 100%、图例与几何一致；
- 无远程 script/chart 依赖；CSP 不阻断关键可视化；
- 长标题、长路径、工具 payload 不破坏布局。

### 10.4 部署与网络

- `nginx -t` 或等价边缘配置验证通过；
- 应用容器未发布宿主机端口；
- 内部 healthcheck 正常，公网 `/agent/healthz` 仍为 404；
- 登录页 200、匿名历史 302、匿名 session API 401、禁用密码重置 404；
- 主站 `/`、`/cv/`、`/xzqtest/`、`/xuzhiqin/` 等关键路径回归正常；
- 部署前后备份均可通过 restore verification；
- 回滚镜像/代码/数据库步骤已演练并记录。

---

## 11. 当前生产基线快照（供迁移比对，不是容量上限）

截至 2026-08-20 当前已验证：

```text
物理 sessions：62
root sessions：17
合法 direct threads：45
messages：4521

Input tokens：49,909,924
Output tokens：1,420,715
Cache read tokens：471,514,206
Cache write tokens：48,704
Reasoning tokens：618,914
```

这些值用于重制迁移验收的数量级与 aggregate 对照。未来数据增长后，验收应以导出/导入前后的记录数、哈希、Owner 分布和结构化 usage 合计为准，而不应把上述固定数字写进业务逻辑。

---

## 12. 最小交付清单

未来重制完成时，至少应交付：

1. 本文或其更新版本；
2. 网络架构图（`agent-history-network-architecture.html`）；
3. 数据库 ERD 与迁移策略；
4. OpenAPI/路由契约与权限矩阵；
5. 可复现部署配置（不含秘密）；
6. 环境变量样例文件；
7. 备份、恢复、回滚和灾难演练文档；
8. 单元、集成、安全、浏览器与生产验收报告；
9. 已脱敏的生产形状迁移样本或生成脚本；
10. 变更日志与明确的“已实现/预留/废弃”功能清单。

---

## 附录 A：当前关键端点

| 方法 | 应用内部路径 | 公网前缀路径 | 认证 | 当前状态 |
|---|---|---|---|---|
| GET | `/dashboard/` | `/agent/dashboard/` | 登录 + 绝对 session | 已实现 |
| GET | `/history/` | `/agent/history/` | 登录 + Owner scope | 已实现 |
| GET | `/history/session/<pk>/` | `/agent/history/session/<pk>/` | 登录 + Owner scope | 已实现 |
| GET | `/history/usage/` | `/agent/history/usage/` | 登录 + Owner scope | 已实现 |
| GET | `/history/export/` | `/agent/history/export/` | 登录 + Owner scope | 已实现 |
| GET/POST | `/history/import/` | `/agent/history/import/` | 登录；管理员可选择 Owner | 已实现 |
| GET/POST | `/history/memory/` | `/agent/history/memory/` | 登录 + 当前 Owner | 已实现 |
| GET | `/api/session/` | `/agent/api/session/` | 匿名 401 / 登录 200 | 已实现 |
| GET | `/healthz` | 内部；公网 `/agent/healthz` 404 | 内部 health check | 已实现 |
| GET | `/features/history-synthesis/` | `/agent/features/history-synthesis/` | 登录 | 预留页面 |
| GET/POST | `/api/v1/features/history-synthesis/...` | `/agent/api/v1/features/history-synthesis/...` | 登录 | GET 预留；POST 固定 503 |
| GET | `/features/api-credits/` | `/agent/features/api-credits/` | 登录 | 预留页面 |
| GET/POST | `/api/v1/features/api-credits/...` | `/agent/api/v1/features/api-credits/...` | 登录 | GET 预留；POST 固定 503 |

## 附录 B：当前实现参考文件

| 主题 | 当前参考文件 |
|---|---|
| 路由 | `config/urls.py`、`history/urls.py` |
| 设置、安全、Cookie、上传限制 | `config/settings.py`、`history/security_headers.py`、`history/auth_views.py` |
| 数据模型 | `history/models.py` |
| 导入与脱敏 | `history/importer.py`、`history/forms.py` |
| 查询、授权、展示、导出 | `history/views.py`、`history/presentation.py`、`history/usage.py` |
| 模板与 CSS | `templates/history/`、`history/static/history/app.css` |
| 容器与启动 | `Containerfile`、`compose.yaml`、`docker/entrypoint.sh`、`systemd/agent-history-portal.service` |
| 备份与恢复 | `scripts/backup.sh`、`scripts/restore-verify.sh` |
| NPM 路由 | `NPM_ADVANCED_MERGED.conf`、`NPM_SUBPATH_ROLLOUT.md` |
| 当前运行与恢复点 | `OPERATIONS.md`、`PROJECT_STATUS.md` |
