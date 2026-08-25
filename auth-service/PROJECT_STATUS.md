# Agent History Portal 项目状态与后续交接

> 状态日期：2026-08-20 11:41 UTC
> 项目目录：`/home/linpengxiao/agent-history-portal`
> 目标域名：`https://agent.c2sml.cn`
> 当前结论：**临时门户登录入口已按用户要求公开于 `https://c2sml.cn/agent/`；数据仍由 Django 登录和 owner/admin 授权保护。最终仍应迁移到独立 `agent.c2sml.cn` origin。**

## 1. 现在是否还需要继续处理

**目前没有必须立即继续的服务器操作。**

管理组继续保留共享 `linpengxiao` 账号，同时按当前要求恢复 root 的 SSH 公钥登录；root 密码认证和非公钥认证仍关闭。生产账号、脱敏历史、备份和本机页面均已完成；`agent.c2sml.cn` 的 DNS 仍不能配置，因此继续使用临时 `/agent/` 路径：

- 不配置 `agent.c2sml.cn` 的 NPM Proxy Host；
- 不申请或启用该域名的 TLS 证书；
- 不通过裸 IP 或未受控临时域名绕过 TLS；
- `c2sml.cn/agent/` 已作为公开登录入口上线；
- 不把应用端口直接暴露到公网；
- 不在聊天、命令参数、Git 或文档中记录任何新密码或密钥。

### 当前 SSH 进度与剩余决策

- root 密码已轮换，但不再用于 SSH 管理；
- 8 把原 root 公钥与 1 把现有额外公钥已去重合并到 `linpengxiao`，共 9 把；
- `linpengxiao` 密码锁定，公钥登录和无交互 sudo 已验证；
- `PermitRootLogin prohibit-password`（当前 `sshd -T` 兼容显示为 `without-password`）；
- root 专用 `AuthenticationMethods publickey`；
- `PasswordAuthentication no`；
- `PubkeyAuthentication yes`；
- root 公钥登录、`linpengxiao` 公钥登录和 sudo 均已从新连接验证成功；root 密码/非公钥认证已验证为拒绝；
- 本次策略变更前配置和 authorized_keys 已备份到 `/var/backups/ssh-hardening/20260818T055108Z`；初始加固基线仍位于 `/var/backups/ssh-hardening/20260816T135055Z`。

共享账号降低了人员级审计粒度；OpenSSH 日志仍会记录 accepted key fingerprint。若公网 IP 稳定且不会锁住管理员，可进一步在华为云安全组中限制 22 端口来源。

## 2. 当前完成情况

### 2.0 Native Client Session 源码交接（2026-08-25）

认证连续性服务端源码已完成可执行交接，但本文件不把它表述为生产部署或迁移
证据。权威运维入口是 [`NATIVE_CLIENT_SESSION.md`](NATIVE_CLIENT_SESSION.md)：

- `history.0006_account_identity_client_session` 添加并回填不可变 UUID
  AccountIdentity 与保留的 native ClientSession；
  `history.0007_trace_token_client_session` 以可空、受保护关联绑定 native
  TraceUploadToken，并保留其撤销原因；`history.0008_client_session_auth_binding`
  添加并回填 Session 的密码状态绑定（keyed HMAC，非明文凭据）。
- 已实现 `/auth/api/client-session/`、`/auth/api/client-session/current/` 和
  `/auth/api/client-session/trace-token/`，以及对 native Trace 的结构化
  introspection。旧 Web-session/Trace 路由仍保留为 rollout 兼容接口。
- Admin 仅允许超级管理员以 revoke/disable 动作记录 Session 或账户终端状态；
  `revoke_sessions` 只变更选中且仍活跃的 Session，并保留已撤销行的首次
  reason；不允许新增、编辑或删除 AccountIdentity/ClientSession 证据行。
- 回滚必须保留 `0006`/`0007`/`0008` 的加性数据库状态和所有 identity/Session/Trace
  记录，只停止 native 路由流量；它不授权清除客户端的 SessionDB、附件、对话或
  Trace outbox。

提交部署或切换客户端前，先按 handoff 的 Gateway → auth service → Gateway
identity handling → client 顺序核对，并运行其中列出的 server 与 client fixture
行为测试。

### 2.1 架构与权限

已实现 Django + SQLite 多用户历史门户：

- 未登录用户不能访问历史、导入或导出；
- 普通用户只能查看、搜索、导入和导出自己名下的数据；
- 普通用户提交的 owner 字段不会覆盖当前登录账号；
- 只有超级管理员可以管理账号、查看全站历史和执行全站导出；
- 详情、搜索和导出均从 owner-scoped queryset 获取，避免 IDOR；
- 当前仅支持一次性导入，自动同步明确延期。

### 2.2 导入与脱敏

已实现：

- JSON/JSONL 校验；
- 上传大小和记录数限制；
- 导入前完整校验；
- 数据库事务原子导入；
- 同一 owner 下重复 session 的幂等处理；
- 上传文件不永久落盘；
- 服务端二次递归脱敏。

二次脱敏覆盖：

- 嵌套字典、JSON 字符串和环境变量；
- password、token、api key、client secret 等敏感键；
- snake_case、camelCase 和大写缩写变体；
- Basic、Token、Bearer 和 Proxy Authorization；
- Cookie、Set-Cookie 和 session cookie；
- PEM/private key；
- name/value 请求头对象和二元素请求头数组；
- 英文和中文“密码、口令、密钥、令牌”等标签。

> 脱敏是纵深防御，不保证识别任意无标签秘密。因此原始 Hermes `--redact` 导出仍禁止上传；正式导入前必须使用当前代码重新清洗和扫描。

### 2.3 生产配置安全

生产环境已 fail closed：

- 缺少 `DJANGO_SECRET_KEY` 时拒绝启动；
- `DEBUG=1` 时拒绝启动；
- 短密钥、低字符多样性密钥拒绝启动；
- 常见占位符和周期性重复密钥拒绝启动；
- Cookie、CSRF、HTTPS 重定向和安全响应头已配置；
- django-axes 已启用登录失败限速；
- HSTS `includeSubDomains` 和 preload 暂不启用，等待所有相关子域 HTTPS 验证完成。

### 2.4 容器、NPM 与服务

服务器当前已部署服务端 turn 组织、Markdown 渲染、session/thread + uploader 筛选和 Token 用量分析版本：

- 应用目录：`/opt/agent-history-portal`；
- systemd：`agent-history-portal.service`；
- 容器：`agent-history-web`；
- 镜像：`ee2c8319057bc836e92e0af3eeb5bb35348e04fdc9a450bb3172eac04741fd9d`，对应运行代码提交 `926bff0`；旧镜像保留为 `localhost/agent-history-portal-rollback:pre-926bff0`；
- 容器以非 root 用户 `app` 运行；
- 应用不发布任何宿主机端口；
- NPM 通过私有 Podman 网络访问 `http://agent-history-web:8000`；
- `/healthz` 已从宿主机和 NPM 容器内验证；
- NPM `nginx -t` 已通过；
- systemd 已启用并在最后一次服务器验收时为 active。

NPM 管理端口已从公网收口：

- 81 仅绑定服务器回环地址；
- 应通过 SSH 隧道访问，不应重新开放公网；
- Podman 3.3 下旧 `cv-php8` 上游的 DNS alias 兼容问题已用持久化 `extra_hosts` 处理；
- 后续重建 NPM 前必须核对该旧上游映射。

临时路径前缀评估：

- 应用已支持 `DJANGO_SCRIPT_NAME=/agent`，并为静态资源、反向解析、登录重定向、admin CSP 和 Cookie Path 增加测试；
- 服务器当前运行提交 `926bff0`，保留 Hermes client Session API、绝对 Session 过期、`/agent` 前缀、服务端 turn 组织、tool 上下文折叠、arguments-only memory、每用户 Memory pool、Markdown 渲染、session/thread 合并和 uploader 多选筛选；`/agent/history/` 已改为与概览页相同的 dashboard shell/sidebar、状态条和面板风格，500px 移动 viewport 整页横向 overflow 为 0；
- NPM 容器对剥离后 `/accounts/login/`、`/history/` 和 `/static/` 的模拟请求已验证 200/302、`/agent` 重定向、Cookie Path 和 hashed 静态资源；
- session/CSRF Cookie 使用独立名称，并在前缀模式下限定为 `Path=/agent/`；
- 2026-08-17 按用户要求移除 `/agent/` 的 `112.45.67.43/32` 和 `deny all`，允许所有外部来源到达登录页；
- NPM 合并配置已通过受认证 API 持久化，实际 NPM/OpenResty `nginx -t` 通过，API 返回 `nginx_online=true`；
- 独立 fail-closed 复审结论为 `PASS_WITH_MANDATORY_POST_APPLY_VERIFICATION`、0 blocking findings；已删除公开裸 `/agent` 的永久 308，并把直接边缘的 `X-Forwarded-For` 固定为 `$remote_addr`；
- 公开变更前 SQLite 在线备份与配置快照为 `/var/backups/nginx-proxy-manager/database-20260817T034118Z-before-public-agent.sqlite` 和 `config-20260817T034118Z-before-public-agent.tar.gz`，均为 root-only mode 600；
- 公开状态最终恢复点为 `/var/backups/nginx-proxy-manager/database-20260817T035230Z-public-agent.sqlite`、`config-20260817T035230Z-public-agent.tar.gz` 和 `/var/backups/agent-history/db-20260817T035231Z.sqlite3`，均已通过完整性/恢复验证；
- 真实只读、drop-capabilities、no-new-privileges 容器烟雾测试已验证 CSRF、登录、重定向、Cookie Path、静态和导航 URL；
- `c2sml.cn` 现有高级配置含多个静态/PHP/BBS/MinIO location，并将 `/api/` 代理到 NPM 81；公开 `/api/` 当前返回 NPM 版本元数据；
- 路径前缀与现有网站共享浏览器 origin。Cookie Path 只能避免 Cookie 冲突，不能阻止同源 XSS/第三方脚本读取门户页面；公开登录会扩大攻击面，应监控登录失败并尽快迁移到独立子域；
- 原 `/xzqtest`、`/xuzhiqin`、`/cv` 曾只存在于生成的 `1.conf`；现已从备份恢复并与 `/agent` 一起持久化到完整 `NPM_ADVANCED_MERGED.conf`，避免后续 Save 再次丢失；
- 公开来源的匿名登录跳转、真实用户/管理员登录、CSRF、Cookie Path、静态、列表、详情、搜索、导入、导出、退出和 Admin 隔离均需保持通过；
- `history.0002` 已在生产应用：20 个物理 session 迁移为 5 个顶层、15 个直接 subagent threads，20 个 uploader 均为 `portal-admin`，0 owner mismatch、0 raw parent mismatch、0 深层关系；
- 列表只显示 5 个顶层 session；一个详情内嵌 15 个 threads，其他四个没有 thread；搜索 child 返回父 session；导出为 5 个根行、15 个嵌套 threads、1510 条消息；
- uploader 侧栏按可见 owner 范围生成，当前只有 `portal-admin` 一个实际上传者；单元测试覆盖多 uploader 多选和伪造 uploader fail-closed；
- `pxlin`、`zhouzhangchen`、`yaojunjie` 在新版本均完成登录、Admin、5 行导出和安全退出验收；
- 2026-08-18 部署后外部验收：`/agent/` 和 `/agent/history/` 匿名请求均 302 到登录页，登录页和新静态 CSS 返回 200，`/cv/`、`/xzqtest/`、`/xuzhiqin/`、`/api/`、`/paper`、`/bbs` 保持原有响应；NPM reload 后上游恢复正常；
- Hermes `async_delegation_complete` 通知不再作为真实 user turn；已有历史通过已知内容前缀兼容，未来导入保留 `display_kind`/`display_metadata`。生产首个根 session 从 21 个展示 turn 校正为 7 个真实 user turn，14 个委托通知折叠并入相邻 turn，原始消息未删除；
- tool-call assistant、tool 结果和空 assistant 迭代消息均进入对应 turn 的“会话上下文”折叠；以 `CONTEXT COMPACTION` 开头的 Hermes 压缩交接消息不再作为 user turn。Huawei session 实测为 389 条全局上下文、6 个真实 turn、314 条 turn 内上下文、12 条可见 user/final-agent 消息；
- memory tool 的 assistant 调用和 tool 返回不进入上下文折叠；生产首个根 session 实测有 2 条 memory tool 位于全局 context 前置区之外，普通 tool 仍按迭代上下文折叠；
- 每次 memory 操作只展示 assistant 的第一条 `function.arguments`；对应 `tool_name=memory` 返回被隐藏；标题按 action 显示为 `记忆 add`、`记忆 delete` 或 `记忆 replace`，正文只渲染 `arguments.content`，不显示 call ID、target、usage 或其他 payload；生产 session 1/20 均由 2 条记录收敛为 1 条可见记忆；
- 混合 assistant tool call 会拆分为 memory 卡片和普通 tool 上下文片段，普通调用不会丢失；隐藏的 memory result 不改变既有 turn completeness；坏 JSON arguments 显示 `记忆 参数无效`；拆分片段使用唯一 `-memory`/`-tools` anchor；
- 新增 `/agent/history/memory/`，每个登录用户拥有独立 OneToOne Memory pool，可分别保存/上传 `MEMORY.md` 和 `USER.md`；更新只作用于当前登录账号，单边上传不会清空另一份内容；
- 新增 `/agent/features/history-synthesis/` 和 `/agent/features/api-credits/` 两个登录后可见的预留页面，以及对应 `GET` 状态和 `POST` 创建占位接口；全部继承绝对 Session 过期控制，状态固定为 `reserved/available=false`，创建接口固定返回 HTTP 503 且不写入；当前没有模型任务、支付、订单、余额或 API key 业务表；
- 2026-08-20 以 `linpengxiao` 普通账号完成当前 Hermes 脱敏历史的真实 HTTPS/CSRF 上传：新增 42 sessions/3011 messages，跳过 20 个重复 sessions；当前生产为 62 physical sessions、17 roots、45 children、4521 messages，uploader 为 `portal-admin` 20 与 `linpengxiao` 42；账号仍为 active/non-staff/non-superuser，0 跨 owner、0 深层关系、0 cycle、0 failed batch；
- 2026-08-20 部署 Token 用量分析：`history.0004` 将既有 `raw_metadata` 中的 Input、Output、Cache read/write、Reasoning 累计回填到非负 bigint 列；生产合计为 Input `49,909,924`、Output `1,420,715`、Cache read `471,514,206`、Cache write `48,704`、Reasoning `618,914`。新增 `/agent/history/usage/` owner-scoped 总览、列表逐记录 I/O 摘要和详情 Context allocation；上下文构成按当前保存消息以字符启发式估算并明确标注，不冒充逐 API 计费数据或模型最大 context window。旧数据无法可靠回填 API call count，因此当前不展示该指标；
- 该版本本地与候选镜像内均有 150 项 Django 测试通过；ruff、format、迁移检查、依赖审计、生产数据库副本演练、独立 fail-closed 复审、真实普通账号 HTTPS 桌面/500px 验收均通过。生产应用 healthy，NPM `nginx -t` 通过，原 `/cv`、`/xzqtest`、`/xuzhiqin` 路由保持可达；
- Token 版本变更前恢复点：`/var/backups/agent-history/db-20260820T113547Z.sqlite3`（SHA-256 `81777e854c1ff53bbcd8e67169758a8b5593b3605b92c02f250b7d3864e39e65`）；变更后恢复点：`/var/backups/agent-history/db-20260820T114136Z.sqlite3`（SHA-256 `36e207ad2f2b109177682d019ade5afc84a451508c1904e6e269fbbd935128c5`）；两者 restore verification 均为 `ok`；
- 完整应用、验收和回滚步骤见 `NPM_SUBPATH_ROLLOUT.md`。

### 2.5 宿主机目录与备份

已修复全新部署的 bind mount 所有权问题：

- `/var/lib/agent-history`：UID/GID `10001:10001`、mode 700；
- `/var/backups/agent-history`：`root:root`、mode 700；
- systemd 启动前执行幂等的 `scripts/provision-host.sh`；
- 初始化脚本拒绝顶层 symlink；
- root 不递归遍历或 chown 应用可写目录树。

备份方案已完成 TOCTOU 和旧系统 Python 兼容加固：

- 宿主机 root 启动无网络、只读根文件系统、capabilities 全部移除的一次性容器；
- 使用固定应用镜像中的 Python/SQLite online backup API，不依赖 CentOS 8 的系统 Python；
- 源数据目录仅以只读方式挂载；
- 随机工作目录位于 root-only 备份父目录下，容器只看到该工作目录而非整个备份目录；
- 执行 SQLite 完整性检查和 `fsync`；
- 同目录原子重命名；
- 最终备份为 `root:root`、mode 600；
- 应用 UID 无权修改最终备份。

最近一次管理员变更前后备份：

```text
变更前：/var/backups/agent-history/db-20260817T082654Z.sqlite3
SHA-256: a4dc135845e93758b70cda800c03a06ab8501c4bf09a2f00dc96cfbb488bbbf9

变更后：/var/backups/agent-history/db-20260817T083307Z.sqlite3
SHA-256: 0d5126f0f185dfd4541c2eab63043e18d87dc103eb3d72dd6713ea0bc282e3dc
restore verification: ok
```

Session/thread 功能部署检查点：

```text
变更前数据库：/var/backups/agent-history/db-20260817T140424Z.sqlite3
SHA-256: 42bd25623cd8e945f057b6a82361c06b692ecc73816fb4ad0decfc5141bc1c29
变更前部署树：/var/backups/agent-history-deploy/portal-20260817T140424Z-before-thread-feature.tar.gz
SHA-256: 4a29a938208bb2780c8b7b15b76fb64bcf9e9407e1648c0343784e9931732f9a
旧部署树：/opt/agent-history-portal.before-add6d40-20260817T140713Z

变更后数据库：/var/backups/agent-history/db-20260817T141212Z.sqlite3
SHA-256: 340654b67bf1f5e5b3e1d0290c259878beeee97b8ad274fe1163190a4b7c4a94
restore verification: ok
```

可读性版本部署检查点：

```text
提交：58b14fd
候选镜像：65bffc27872289a10cacf54c745bf2fbe7a226885672ee9331b080da2524a0fa
变更前部署树：/opt/agent-history-portal.before-58b14fd-20260818T011831Z
变更前数据库：/var/backups/agent-history/db-20260817T160640Z.sqlite3
变更前数据库 SHA-256: b7e46dda12cc3c398ba15ee56d07945624c04df5376f4d9ddb7cf905c19c351c
部署后数据库：/var/backups/agent-history/db-20260818T012212Z.sqlite3
部署后数据库 SHA-256: b7e46dda12cc3c398ba15ee56d07945624c04df5376f4d9ddb7cf905c19c351c
restore verification: ok
```

Hermes 控制事件展示修复检查点：

```text
提交：98bf4ac
加固提交：8d9263e
上下文提交：ab5d89b
最终提交：d3c5876
最终镜像：2cfbcf2aec6ae39177cee7bd0ec3cf993952af0babd7f62ca444e1c05b8d1d03
变更前数据库：/var/backups/agent-history/db-20260818T015322Z.sqlite3
变更前数据库 SHA-256: b7e46dda12cc3c398ba15ee56d07945624c04df5376f4d9ddb7cf905c19c351c
变更前部署树快照：/var/backups/agent-history-deploy/portal-20260818T015323Z-before-control-events.tar.gz
变更前部署树快照 SHA-256: 9453ff17d8b854c84454a14d1ccafec6a03b0c2107e6668aa6b45d8e39717826
旧部署树：/opt/agent-history-portal.before-98bf4ac-20260818T015422Z
加固前部署树：/opt/agent-history-portal.before-8d9263e-20260818T020247Z
上下文折叠前部署树：/opt/agent-history-portal.before-ab5d89b-20260818T041251Z
Memory pool v1 部署树：/opt/agent-history-portal.before-c5c0932-20260818T044553Z
Memory pool v2 部署树：/opt/agent-history-portal.before-916fe04-20260818T045627Z
Memory pool v3 部署树：/opt/agent-history-portal.before-ea07a1e-20260818T050135Z
Memory 去重前部署树：/opt/agent-history-portal.before-508391a-20260818T053408Z
Memory 去重 v3 前部署树：/opt/agent-history-portal.before-d3c5876-20260818T055555Z
部署后数据库：/var/backups/agent-history/db-20260818T015457Z.sqlite3
部署后数据库 SHA-256: b7e46dda12cc3c398ba15ee56d07945624c04df5376f4d9ddb7cf905c19c351c
restore verification: ok
```

上下文折叠版本部署后数据库：`/var/backups/agent-history/db-20260818T041325Z.sqlite3`；SHA-256 为 `b7e46dda12cc3c398ba15ee56d07945624c04df5376f4d9ddb7cf905c19c351c`，restore verification: ok。
Memory pool v3 部署后数据库：`/var/backups/agent-history/db-20260818T050213Z.sqlite3`；SHA-256 为 `f8e04585ac90beaad05be9ac34cee4b2c49353820f209c33ee4b01d26066147c`，restore verification: ok。
Memory 去重部署后数据库：`/var/backups/agent-history/db-20260818T053441Z.sqlite3`；SHA-256 为 `13515feca4ea50ca8f648d0452c7cf475f1ebb8eb006d4f570f5e8eb3bbea58d`，restore verification: ok。
Memory 去重 v3 部署后数据库：`/var/backups/agent-history/db-20260818T055629Z.sqlite3`；SHA-256 为 `13515feca4ea50ca8f648d0452c7cf475f1ebb8eb006d4f570f5e8eb3bbea58d`，restore verification: ok。

预留功能部署检查点：

```text
运行代码提交：5449d06bd58b3e4157dbeffe11512a385c1c149f
运行镜像：027205acdad5d446570bbab7097e131d6151462400505e08bcd0551185f013de
旧提交：c84b75f5966a4de6a9108b9a5071bca995c366ab
旧镜像/回滚标签：b1155df43eb736847b2501e092f4e92cf2d1bf5b99d300521c0e9302310e92a5
变更前数据库：/var/backups/agent-history/db-20260820T035550Z.sqlite3
变更前部署树：/var/backups/agent-history-deploy/portal-20260820T035550Z-before-reserved-features.tar.gz
变更前部署树 SHA-256：0710702228c425251b022c700be9001bec2536dd5c89220d69a4e998e03c34bd
变更后数据库：/var/backups/agent-history/db-20260820T040743Z.sqlite3
变更后数据库 SHA-256：f59964bb6e74ad49813614831a00b642363be2cda49a808c23b140e343b3aada
restore verification: ok
```

历史列表 UI 与普通账号上传检查点：

```text
运行代码提交：17d1c53b9217bc086fe0d84d2df8f2a005dc52a1
运行镜像：0537d3dbff6df5460e482a95a6a1e1c6bc54e0e0bf5cdd20dfe751fe989d7285
旧提交：bd9721e4f68e55e8df903192c1e8234ade46b362
旧镜像/回滚标签：027205acdad5d446570bbab7097e131d6151462400505e08bcd0551185f013de
变更前数据库：/var/backups/agent-history/db-20260820T051438Z.sqlite3
变更前数据库 SHA-256：2ee4454546e8ad70eed0452027d0909f49c5d2e6c592938b0b9fe078438c188d
变更前部署树：/var/backups/agent-history-deploy/portal-20260820T051438Z-before-history-ui-upload.tar.gz
变更前部署树 SHA-256：a1273dd5c11d3b0d1d1280702e41a4afc93fe604633653da68fced2619eaf3e0
变更后数据库：/var/backups/agent-history/db-20260820T053005Z.sqlite3
变更后数据库 SHA-256：4f002c7487bbab1471c782e9599b05e90fdd40a41fa382d517255c4b0c471fa6
restore verification: ok
```

已通过 SSH 加密传输复制一份包含脱敏历史的 mode-600 off-host 副本到本机：

```text
/home/linpengxiao/.local/state/agent-history-portal-offhost-backups/db-20260816T140915Z.sqlite3
mode: 600
SHA-256: c009022453028fee12e6f66d5784dba177de02fb38b9ae870b34fcf7f276b9a8
integrity_check: ok
sessions: 20
messages: 1510
```

旧备份已迁移到：

```text
/var/backups/agent-history/legacy/
```

### 2.6 测试与独立复审

当前代码实际复核结果：

- Django：143 项测试通过（本机生产基线与候选镜像内均通过）；
- Ruff 与本次 Python 文件 formatter：通过；
- pip-audit：`No known vulnerabilities found`；
- Git diff 检查：通过；
- migration check：`No changes detected`；
- 预留功能 immutable snapshot `7c546974de43ec6ad3a0919abae47a782e529384af7ef19bccf596d9a0bf59d8` 经独立 fail-closed 复审批准；
- 历史列表 UI immutable snapshot `ca771a2e4a6810166a15b480238e3da559f733667d640053918b4d757cf3d564` 经独立 fail-closed 复审批准，0 security/logic findings；
- Selenium/Firefox 合成数据验证主页/历史共享 shell/sidebar、active nav、搜索/筛选和桌面/500px 移动横向 overflow=0；线上普通账号登录、Secure Cookie、CSRF 上传、导出 ID 集合、owner/uploader、退出和旧路由均通过；
- 真实登录、Secure Cookie、`/agent/api/session/`、两个页面、两个状态 API、两个 503/无写入 POST、退出和 5 roots/15 threads/1510 messages 均完成公网验收；
- 生产形状数据库副本 migration rehearsal：20 sessions、5 roots、15 children、20 uploader tags、1510 messages、integrity ok；
- 最终独立 fail-closed 复审在修复跨 owner/deep child redirect 后通过，无 security/logic blocker。
- Markdown 预处理使用 `markdown-it-py==4.2.0`，服务端逐条保留消息，按 user 边界组织 turn，不压缩、不摘要、不抽取 preference/common memory；原始 HTML 禁用，危险链接由渲染器阻断，远程图片显示为纯文本占位符；

已提交：

```text
add6d40 [verified] merge subagent threads and filter uploaders
58b14fd [verified] render readable server-side markdown history
98bf4ac [verified] fold Hermes delegation events into turns
8d9263e [verified] harden Hermes control event detection
ab5d89b [verified] collapse tool iterations into context
c5c0932 [verified] show memory tools and add user memory pools
916fe04 [verified] preserve partial memory pool updates
ea07a1e [verified] fix memory-only context empty state
508391a [verified] deduplicate memory tool presentation
d3c5876 [verified] preserve mixed tool and memory context
5449d06 [verified] reserve synthesis and API credit features
17d1c53 [verified] align history list with dashboard
561cb77 [verified] record three additional portal admins
8390c9d [verified] make agent login publicly reachable
```

Git 作者：

```text
MiracleLin001 <linpengxiao@sjtu.edu.cn>
```

## 3. 当前公网与 DNS 状态

2026-08-16 外部复核：

```text
agent.c2sml.cn A 记录：无
c2sml.cn A 记录：121.37.182.49
22：open
80：open
81：closed-or-filtered
443：open
8080：closed-or-filtered
9000：closed-or-filtered
https://c2sml.cn/：HTTP 200
https://c2sml.cn/cv/：HTTP 302
```

因此门户当前不能通过 `agent.c2sml.cn` 公网访问，这是有意的安全暂停状态，不是部署故障。

## 4. 当前未完成和阻塞项

| 项目 | 状态 | 阻塞原因 |
|---|---|---|
| root 密码轮换 | 已完成 | 2026-08-16 已确认密码状态更新；新值未写入文档或 Git |
| 共享非 root SSH 公钥账号 | 已完成 | 9 把授权公钥已迁移到 `linpengxiao`，登录和 sudo 已验证 |
| root SSH 公钥登录与密码认证策略 | 已完成 | root 公钥登录和 `linpengxiao` sudo 已验证；root 密码/非公钥认证保持拒绝 |
| SSH 22 安全组来源限制 | 可选降险 | 需要可信且稳定的公网来源 IP |
| `agent.c2sml.cn` DNS | 阻塞 | 暂时无法添加 A 记录 |
| 临时 `/agent/` 路径 | 已公开 | 所有来源可到达登录页；历史数据仍需 Django 登录和权限授权 |
| 独立 NPM Proxy Host 与 TLS | 阻塞 | 依赖 `agent.c2sml.cn` DNS；仍是最终方案 |
| 正式超级管理员和普通账号 | 已完成 | 超级管理员 `portal-admin`、`pxlin`、`zhouzhangchen`、`yaojunjie` 和普通 owner `linpengxiao` 均已完成权限验收 |
| 真实 Hermes 历史导入 | 已完成 | 62 physical sessions、17 roots、45 children、4521 messages、1 owner、2 succeeded/0 failed batches |
| 临时路径公网验收 | 已完成 | 公开来源匿名跳转、真实登录、权限、静态、导出和旧路由均通过 |
| 独立子域公网验收 | 阻塞 | 依赖 `agent.c2sml.cn` DNS、独立 Proxy Host 和证书 |
| 自动同步 | 第二阶段 | 现有 importer 对重复 session 整体跳过，不补新增消息；需设计 session/message 双层幂等和冲突策略 |
| 历史方法总结业务 | 第二阶段 | 页面/接口已预留；critic、共性提取、证据和人工审核尚未实现 |
| DeepSeek/Qwen 充值业务 | 第二阶段 | 页面/接口已预留；支付、订单、额度、客户端激活和密钥生命周期尚未实现 |
| CentOS 8 迁移 | 技术债 | CentOS 8 已停止维护，当前风险被接受但未消除 |

## 5. 真实历史文件状态

禁止上传的原始导出：

```text
/home/linpengxiao/hermes-history-redacted-20260815T130210Z.jsonl
```

原因：Hermes 自带 `--redact` 曾遗漏已泄露的旧 root 密码。

2026-08-16 的初始正式导入源仍保留为历史恢复证据。2026-08-20 当前正式上传源：

```text
/home/linpengxiao/.local/state/agent-history-portal-upload/hermes-current-sanitized-final-20260820T044534Z.jsonl
```

```text
sessions: 62
roots: 17
children: 45
embedded active/non-compacted messages: 4767
mode: 600
SHA-256: d33eb461297dfad80117672644d6e287142a59d6c7c41a8e3e0d2da1221fe366
```

最终文件依次经过 Hermes `--redact`、门户递归脱敏器和保守 residual scrub；与当前 Hermes `.env`、auth pool、门户账号凭据精确比对为 0，私钥头、常见 token/JWT、URL 内嵌凭据、未脱敏 Authorization/Cookie 和敏感赋值模式均为 0。生产普通账号导入批次 succeeded，owner/uploader 都是 `linpengxiao`，新增 42 sessions/3011 messages、跳过 20 个重复 sessions，0 failed；原始 `--redact` 和非最终中间文件已删除。

消息统计口径：Hermes `sessions stats` 在导出时为 62 sessions/10146 物理消息，包含 active 与已被压缩替代的 compacted 行；JSONL 只包含 4767 条 active/non-compacted 消息，不是截断。门户对已有 session ID 做整 session 跳过：重叠 20 sessions 在当前源端有 1756 条消息，但门户旧快照只有 1510 条，因此相差的 246 条不会补写；生产最终为 4521 条消息。该操作是普通账号手动上传测试，不是增量同步；自动/增量同步仍属于第二阶段。

## 6. 以后恢复工作时的安全顺序

### 阶段 A：SSH 与凭据

已完成：

1. root 密码轮换；
2. 创建并启用共享 `linpengxiao` 公钥账号；
3. 保留并核对 root 与共享账号的现有公钥；
4. 共享账号密码锁定和 sudo 验证；
5. 配置 root 仅允许公钥认证，并保持 SSH 密码认证关闭；
6. 通过新连接验证允许/拒绝矩阵；
7. 通过共享账号执行门户健康检查和备份恢复。

可选后续：按需要限制云安全组 22 端口来源。

### 阶段 B：DNS、NPM 和 TLS

1. 添加 A 记录：`agent.c2sml.cn → 121.37.182.49`；
2. 等待并验证公网 DNS；
3. 通过 SSH 隧道访问 NPM 管理界面；
4. 新建独立 Proxy Host，不修改已有 `c2sml.cn` Host；
5. 上游使用 `http://agent-history-web:8000`；
6. 申请 Let’s Encrypt 证书并启用 Force SSL；
7. 验证 HTTP→HTTPS、证书链、登录 Cookie、CSRF 和安全响应头；
8. 再次确认 81、8000、8080 和 9000 不对公网开放；
9. 再次验证原 `c2sml.cn` 和 `/cv/` 未受影响。

### 阶段 C：账号、清洗和导入

已完成：

1. 创建正式超级管理员 `portal-admin`、`pxlin`、`zhouzhangchen`、`yaojunjie`；
2. 创建普通 owner 账号 `linpengxiao`；
3. 重新导出、精确清洗和严格扫描 Hermes 历史；
4. 在隔离 SQLite 中预演导入；
5. 导入正式脱敏历史；
6. 验证登录、列表、搜索、详情、导出和 admin 权限；
7. 普通账号导出 17 个 root 行、45 个嵌套 threads，并与 62 个源 session ID 完全匹配；
8. 导入后执行在线备份和恢复验证；初始 2026-08-16 快照另有 off-host 副本。

## 7. 后续验收标准

只有以下条件全部满足，才能声明公网交付完成：

- DNS 指向正确服务器；
- HTTP 自动跳转 HTTPS；
- TLS 主机名和证书链有效；
- 未登录请求不泄露会话元数据；
- 普通用户跨 owner 的列表、搜索、详情、导入和导出均被拒绝；
- 超级管理员账号管理和全站导出正常；
- 登录失败限速不会误锁所有来源；
- 应用和 NPM 管理端口未直接暴露公网；
- 原有网站行为与变更前一致；
- 正式导入的会话数和消息数匹配；
- 备份能在临时位置恢复并通过完整性检查；
- systemd 重启后服务、权限、账号和数据保持正常。
- 使用公开 `/agent/` 时，任意来源可到达登录页，但匿名历史请求必须跳转登录；现有 `/`、`/cv/`、`/api/`、`/paper` 和 `/bbs` 行为不得改变。

## 8. 关键文件

```text
/home/linpengxiao/agent-history-portal/OPERATIONS.md
/home/linpengxiao/agent-history-portal/PROJECT_STATUS.md
/home/linpengxiao/agent-history-portal/SITE_MAINTENANCE.md
/home/linpengxiao/agent-history-portal/NPM_SUBPATH_ROLLOUT.md
/home/linpengxiao/agent-history-portal/NPM_ADVANCED_MERGED.conf
/home/linpengxiao/agent-history-portal/RESERVED_FEATURES_HANDOFF.md
/home/linpengxiao/agent-history-portal/scripts/provision-host.sh
/home/linpengxiao/agent-history-portal/scripts/backup.sh
/home/linpengxiao/agent-history-portal/scripts/restore-verify.sh
/home/linpengxiao/agent-history-portal/systemd/agent-history-portal.service
/home/linpengxiao/.hermes/plans/2026-08-15_182603-multi-user-agent-history-portal.md
```

## 9. 一句话结论

**临时 `https://c2sml.cn/agent/` 登录入口已公开；它仍是共享 origin 的短期方案，数据访问依赖 Django 登录与权限隔离，最终应迁移到独立 `https://agent.c2sml.cn/`。**
