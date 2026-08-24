# Agent History Portal

多用户、按账号隔离的 Hermes 对话历史查看门户。

主站与 NPM 联合维护手册：`SITE_MAINTENANCE.md`

## 本地开发

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py test
uv run python manage.py runserver
```

## 权限模型

- 未登录用户不能读取历史。
- 普通用户只能读取、搜索、导入和导出自己的历史。
- 超级用户可在 `/admin/` 管理账号，并查看全部历史。
- 每个顶层 history 代表一个对话 session；Hermes 的直接 subagent thread 通过 `parent_session` 并入主 session，不在列表中独立显示。
- 当前部署支持一层 subagent thread；导入或迁移遇到孙线程、孤儿或 parent cycle 会 fail closed，不会静默变成顶层记录。
- 每个 session 和 thread 都有必填 uploader tag；列表侧栏按当前账号可见范围列出 uploader，支持多选 OR 筛选。
- 导出每个顶层 session 一行，线程嵌套在 `subagent_threads` 字段；当前生产快照为 17 个顶层、45 个子线程、4521 条消息。
- 当前运行代码提交为 `926bff0`，镜像为 `ee2c8319057bc836e92e0af3eeb5bb35348e04fdc9a450bb3172eac04741fd9d`；`/agent/history/` 已与概览页共用 dashboard shell/sidebar，并新增 `/agent/history/usage/`、列表逐记录 I/O 摘要和详情 Context allocation。Token 版本变更前备份为 `/var/backups/agent-history/db-20260820T113547Z.sqlite3`，变更后备份为 `/var/backups/agent-history/db-20260820T114136Z.sqlite3`，两者恢复验证均通过。旧镜像标签为 `localhost/agent-history-portal-rollback:pre-926bff0`。历史总结/API 充值仍仅为 `reserved` 占位，当前没有模型调用、支付、订单、余额、API key 存储或客户端密钥下发。
- 没有公开注册；历史在 MVP 中不可编辑或删除。

## 生产部署原则

应用容器只加入 `nginx-proxy-manager_default` 网络，不发布宿主机端口。NPM 通过容器别名 `agent-history-web:8000` 反向代理到应用；业务公网入口是 80/443，SSH 22 仅用于公钥运维，81 仅回环访问；8080/9000 即使暂时保留宿主机绑定，也必须由云安全组阻断公网。

`.env` 必须是服务器上的 mode-600 文件，不能提交 Git，也不能放入历史导出文件。不要复制 Hermes `.env`、`auth.json`、OAuth token 或 SSH 私钥。

## 首次部署的宿主机目录

应用容器固定使用 UID/GID `10001`。绑定挂载会覆盖镜像内 `/data` 的所有权，因此首次启动前必须在宿主机执行幂等初始化；systemd 单元也会在每次启动前自动执行同一脚本：

```bash
cd /opt/agent-history-portal
chmod 600 .env
sudo ./scripts/provision-host.sh
sudo cp systemd/agent-history-portal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-history-portal.service
```

数据目录 `/var/lib/agent-history` 归 UID/GID `10001` 所有，mode 700。初始化脚本拒绝顶层 symlink，并且只设置顶层目录，不以 root 递归遍历应用可写树。最终备份写入 root 所有、mode 700 的 `/var/backups/agent-history`；备份脚本由宿主机 root 启动无网络、只读根文件系统的一次性容器，使用固定应用镜像中的 Python/SQLite online backup API。源数据目录只读挂载，目标仅使用 root-only 父目录下的随机工作目录；校验后由 root 原子发布为 mode 600，应用 UID 无法遍历最终备份目录。

## SSH 安全门

已完成：root 密码已轮换，root 现允许 SSH 公钥登录但不允许密码登录；原有共享 `linpengxiao` 公钥登录和 sudo 通道保持可用。当前生效配置为 `PermitRootLogin prohibit-password`（`sshd -T` 显示为 `without-password`）、root 专用 `AuthenticationMethods publickey`、`PasswordAuthentication no`、`KbdInteractiveAuthentication no`、`PubkeyAuthentication yes`。root 公钥登录、`linpengxiao` 公钥登录和 sudo 均已从新连接验证成功；root 密码/非公钥认证已验证拒绝。本次策略变更前备份位于 `/var/backups/ssh-hardening/20260818T055108Z`，初始加固基线仍位于 `/var/backups/ssh-hardening/20260816T135055Z`。

华为云安全组应只公开 80/443；22 最好仅放行管理来源。NPM 的 81、8080、9000 不应对公网开放。使用下面的 SSH 隧道访问 NPM 管理界面，而不是开放 81：

```bash
ssh -i ~/.ssh/id_rsa -L 8181:127.0.0.1:81 linpengxiao@121.37.182.49
```

浏览器打开 `http://127.0.0.1:8181`。

## DNS 与 Nginx Proxy Manager

在域名控制台添加 A 记录：

- 主机记录：`agent`
- 记录值：`121.37.182.49`
- TTL：600（验收后可调大）

等待 `agent.c2sml.cn` 能解析后，在 NPM 新建 Proxy Host：

- Domain Names：`agent.c2sml.cn`
- Scheme：`http`
- Forward Hostname/IP：`agent-history-web`
- Forward Port：`8000`
- 开启 Block Common Exploits；不需要 Websocket Support。
- SSL 中申请新的 Let's Encrypt 证书，开启 Force SSL 和 HTTP/2。
- 仅在 HTTPS 和所有相关子域都确认正常后再考虑 HSTS；当前不要启用 includeSubDomains 或 preload。

NPM 容器应能直接访问 `http://agent-history-web:8000/healthz`；应用不发布宿主机端口。

当前 CentOS/Podman 3.3 环境中，旧 `cv-php8` 容器的 DNS 别名在 NPM 重建后不会自动注册。服务器的 NPM `compose.yaml` 已加入 `extra_hosts`，并把管理端口绑定为 `127.0.0.1:81:81`。如果未来删除并重建 `cv-php8`（不是普通 restart），必须先读取它的新容器 IP、更新该映射，再重建 NPM；完成后运行 `nginx -t` 并验证 `https://c2sml.cn/cv/`。

### 临时 `/agent/` 路径

无 DNS 权限期间，门户已临时上线于 `https://c2sml.cn/agent/`。它与现有网站共享浏览器 origin，不能提供独立子域的 XSS/第三方脚本隔离。按用户要求，`/agent/` 登录入口现已向所有外部来源开放；历史数据仍由 Django 登录、CSRF、django-axes、owner-scoped 授权和 superuser-only Admin 保护。应用使用独立 Cookie 名和 `Path=/agent/`，NPM 正确剥离路径。完整运行配置见 `NPM_ADVANCED_MERGED.conf`，验收与回滚步骤见 `NPM_SUBPATH_ROLLOUT.md`。

原 `/xzqtest`、`/xuzhiqin` 和 `/cv` 曾只手工存在于 NPM 生成的 `1.conf`，不在数据库 Advanced 字段中；一次 UI Save 会删除它们。现在三段已经合并并持久化到 Advanced 配置。后续不得直接编辑生成的 `1.conf`，所有 NPM 保存必须保留 `NPM_ADVANCED_MERGED.conf` 中的完整配置。

现有 `c2sml.cn` 高级配置还将 `/api/` 代理至 NPM 81，公网根响应会公开 NPM 版本元数据。这是既有配置；未经依赖确认不得直接删除，但应在后续 NPM 安全复核中处理。

## 门户账号

已创建：

- `portal-admin`：超级管理员，只用于 `/admin/` 和全站管理；
- `pxlin`：超级管理员；
- `zhouzhangchen`：超级管理员；
- `yaojunjie`：超级管理员；
- `linpengxiao`：普通历史 owner，不具有 admin 权限。

密码没有写入服务器明文文件、命令参数、聊天或 Git。原始两个账号的本机凭据位于 `/home/linpengxiao/.local/state/agent-history-portal-production/credentials.txt`；新增三个管理员的凭据位于 `/home/linpengxiao/.local/state/agent-history-portal-production/additional-admin-credentials.txt`。两个文件均为 mode 600。当前登录入口为 `https://c2sml.cn/agent/accounts/login/`。

### Hermes 客户端账户分发

- 账户只由超级管理员在 `/agent/admin/` 创建、停用或重置密码。
- 不启用公开注册、邀请、密码重置或密码修改 URL。
- 管理员创建账户或重置密码后，通过已核验的带外渠道交付初始密码；不得写入 Git、命令参数、服务器日志或无访问控制的聊天/工单。
- 用户首次成功登录后仍由管理员负责后续重置；Hermes 客户端不提供账户生命周期操作。

### Native Client Session 操作

认证连续性协议的唯一运维契约见 [`NATIVE_CLIENT_SESSION.md`](NATIVE_CLIENT_SESSION.md)。
它定义了不可变 `account_id`、原生 Session/Trace 路由与 no-store 响应、
迁移 `history.0006`/`0007`、终端撤销分类、管理动作、兼容性和回滚顺序。

- Native Session、AccountIdentity 与已撤销 TraceUploadToken 是保留的审计证据；
  不得通过 Admin、ORM 或 SQL 删除这些行来登出或撤销设备。
- 只能由超级管理员使用 `revoke_sessions`、`disable_accounts` 或
  `revoke_accounts`；前者仅影响选中 Session，后两者会撤销各自仍活跃的
  Session 及其绑定的 Trace token。
- `invalid_session_credential`、服务不可达和普通 Django Web Session 过期均为
  非终端状态。只有 account/session ID 与本地缓存匹配的结构化
  `account_disabled`、`account_revoked` 或 `session_revoked` 才是终端状态。
- 不在日志、命令行、Git、工单或聊天记录 native bearer、Trace bearer、Django
  session cookie、CSRF 值或 Gateway internal secret；示例只能使用 handoff 中的
  sentinel 值。
- 回滚只停止使用 native 路由并保留迁移后的数据库和记录；绝不要求删除客户端
  SessionDB、附件、对话、projects/profiles 或 Trace outbox。

### 预留功能接口

- 页面：`/agent/features/history-synthesis/`、`/agent/features/api-credits/`；
- 状态：`GET /agent/api/v1/features/history-synthesis/`、`GET /agent/api/v1/features/api-credits/`；
- 未来创建入口：`POST /agent/api/v1/features/history-synthesis/runs/`、`POST /agent/api/v1/features/api-credits/orders/`；
- 所有入口继承 `hermes_session_required`，绝对 Session 过期后不可访问；
- 两个 POST 当前固定返回 503 `feature_not_available`，并声明及测试 `writes_performed=false`；
- 当前不接受 API key、支付信息或订单，不调用 critic/总结模型；完整契约见 `RESERVED_FEATURES_HANDOFF.md`。

## 一次性导入

可信本机的标准导出命令：

```bash
hermes sessions export ./hermes-history-redacted.jsonl \
  --format jsonl --redact --yes
chmod 600 ./hermes-history-redacted.jsonl
```

2026-08-16 初始正式导入为 20 个会话、1510 条消息，owner 为 `linpengxiao`、uploader 为 `portal-admin`。2026-08-20 又以 `linpengxiao` 普通账号经真实 HTTPS/CSRF 表单上传当前 62-session 脱敏快照：20 个重复 session 按现有幂等语义整 session 跳过，新增 42 个 session 和 3011 条消息；生产现为 17 roots、45 children、4521 条消息，uploader 分布为 `portal-admin` 20、`linpengxiao` 42，0 failed batches。最终本机文件为 `/home/linpengxiao/.local/state/agent-history-portal-upload/hermes-current-sanitized-final-20260820T044534Z.jsonl`，mode 600，SHA-256 `d33eb461297dfad80117672644d6e287142a59d6c7c41a8e3e0d2da1221fe366`。

当前源文件共含 4767 条 active/non-compacted 消息；其中 20 个重复 session 在源端有 1756 条、门户旧快照有 1510 条，现有 importer 不会为重复 session 补写相差的 246 条消息。因此该操作验证了普通账号手动上传，但不是增量同步。若需要持续同步，必须单独设计 message-level 幂等与冲突语义，不能删除旧数据或复制活动 SQLite 文件来绕过。

`--redact` 和模式匹配都不能保证识别没有标签的自定义密码。曾出现在对话里的服务器密码、临时 token 等必须先轮换，并在上传前对其旧值执行精确替换；验证旧值不存在后才允许上传。

## Token 用量与 Context allocation

- `history.0004` 为 session 保存 `input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_write_tokens` 和 `reasoning_tokens`，并从已有 `raw_metadata` 回填；导入层只接受 SQLite signed bigint 范围内的非负整数。
- `/agent/history/usage/`、历史列表和详情只从 owner-scoped queryset 读取；root session 只聚合同 owner 的直接 subagent thread，畸形跨 owner parent 关系不会进入总览、thread count 或页面。
- Input、Output、Cache 和 Reasoning 是 Hermes 导出的 session 累计值。Context allocation 仅根据当前保存消息估算推理、代码、对话、工具、系统/控制内容，页面必须持续保留“字符启发式估算”披露，不得把它表述为逐 API 精确 input 或模型最大 context window。
- 旧生产记录没有可靠、完整的 API call count，当前不显示该指标。未来只有在定义“未知”与“0”的独立语义并完成受控补写后才能启用。
- 2026-08-20 生产迁移后共有 62 sessions、17 roots、45 threads、4521 messages；合计 Input `49,909,924`、Output `1,420,715`、Cache read `471,514,206`、Cache write `48,704`、Reasoning `618,914`，SQLite integrity 为 `ok`。
- 变更前恢复点 `/var/backups/agent-history/db-20260820T113547Z.sqlite3`，SHA-256 `81777e854c1ff53bbcd8e67169758a8b5593b3605b92c02f250b7d3864e39e65`；变更后恢复点 `/var/backups/agent-history/db-20260820T114136Z.sqlite3`，SHA-256 `36e207ad2f2b109177682d019ade5afc84a451508c1904e6e269fbbd935128c5`；两者均已运行 `restore-verify.sh` 并通过。

完整回滚时先停止应用，将 `localhost/agent-history-portal-rollback:pre-926bff0` 重新标记为 `localhost/agent-history-portal_web:latest`，把代码切回 `cb97271`，再以 `podman-compose ... up -d --no-build --force-recreate web` 重建。`history.0004` 只增加列，短时仅回滚应用通常无需立即恢复数据库；若要求完整恢复变更前状态，则在停机并另行保留当前数据库后，按现有恢复流程使用 `db-20260820T113547Z.sqlite3`。

## 运维

```bash
sudo systemctl status agent-history-portal.service
sudo systemctl restart agent-history-portal.service
sudo journalctl -u agent-history-portal.service -f
sudo /opt/agent-history-portal/scripts/backup.sh
sudo /opt/agent-history-portal/scripts/restore-verify.sh /var/backups/agent-history/db-<timestamp>.sqlite3
```

Podman 3.3 重建 `agent-history-web` 后，NPM 的 Nginx worker 可能仍缓存旧容器 IP。应用容器变为 `healthy` 后执行：

```bash
sudo podman exec npm nginx -t
sudo podman exec npm nginx -s reload
curl -sS -o /dev/null -w '%{http_code}\n' https://c2sml.cn/agent/accounts/login/
```

最后一条必须返回 `200`；随后再验证匿名 `/agent/api/session/` 为 `401`、公开密码重置路径为 `404`。

未来自动同步不应复制活动 SQLite 文件；应另行实现带 token、session/message 双层幂等、冲突策略、审计和重试的单向导入 API。现有 importer 对重复 session 只跳过，不更新其中新增消息。
