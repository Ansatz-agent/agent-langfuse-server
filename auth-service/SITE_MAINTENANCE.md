# c2sml.cn 主站与 Agent 门户维护手册

> 状态日期：2026-08-17 14:12 UTC
> 服务器：`121.37.182.49`
> 主站：`https://c2sml.cn/`
> 临时 Agent 门户：`https://c2sml.cn/agent/`
> 最终 Agent 目标：`https://agent.c2sml.cn/`
> 本文不包含密码、Token、Cookie、私钥或 `.env` 内容。

## 1. 文档用途

本文是修改 `c2sml.cn`、新增路径功能、调整公开访问或管理白名单、更新容器或恢复 NPM 时的统一入口。任何变更必须先读本文及以下文件：

```text
/home/linpengxiao/agent-history-portal/NPM_ADVANCED_MERGED.conf
/home/linpengxiao/agent-history-portal/NPM_SUBPATH_ROLLOUT.md
/home/linpengxiao/agent-history-portal/OPERATIONS.md
/home/linpengxiao/agent-history-portal/PROJECT_STATUS.md
```

最高优先级规则：

1. 不直接编辑 NPM 生成的 `/data/nginx/proxy_host/1.conf`；
2. 不直接修改 NPM SQLite 数据库；
3. 不删除 `NPM_ADVANCED_MERGED.conf` 中不属于当前需求的旧路由；
4. 不把 NPM 81、应用 8000、8080 或 9000 重新开放公网；
5. 不把密码、Token、Cookie、私钥、`.env` 或历史导出提交 Git；
6. 每次修改必须先备份、记录基线、验证并准备回滚。

## 2. 当前公开访问与白名单结论

### 2.1 主站没有公用 NPM 白名单

实时盘点结果：

```text
NPM Proxy Host access_list_id：0
NPM Access List 数量：0
NPM Access List Client 数量：0
NPM Access List Auth 数量：0
```

因此：

- `c2sml.cn` 主站整体是公开网站；
- `/agent/` 登录入口也已按用户要求公开；
- 没有主站公用 NPM 白名单；
- 不能在 NPM UI 中误把 Access List 挂到整个 `c2sml.cn` Host，否则会限制所有主站和 Agent 路径；
- Agent 的数据访问仍由 Django 登录、CSRF、django-axes、owner-scoped 授权和 superuser-only Admin 保护。

### 2.2 Agent 当前为公开登录入口

当前规则没有 `allow`/`deny`：

```nginx
location ^~ /agent/ {
    # 其余代理配置见 NPM_ADVANCED_MERGED.conf
}
```

含义：

- 所有外部来源均可打开 `/agent/` 登录入口；
- 未登录访问历史会跳转到 `/agent/accounts/login/`；
- 普通用户登录后只能访问自己的历史；
- 超级管理员才可访问 Admin 和全站历史；
- `/agent/healthz` 对所有公网来源返回 404；
- 该规则不改变 `/`、`/cv/`、`/bbs` 等主站路径。

如果未来需要重新限制为管理 IP 白名单，可在完整配置源中恢复：

```nginx
allow 112.45.67.43/32;
allow <新的固定公网 IP/CIDR>;
deny all;
```

不要使用：

```nginx
allow 0.0.0.0/0;
```

恢复白名单会使普通外部用户无法到达登录页，必须作为显式产品决策执行并重新验收。不要使用 `allow 0.0.0.0/0;` 伪装成白名单；公开状态应直接省略 `allow`/`deny`。

### 2.3 SSH 与主机防火墙

SSH 当前有效配置：

```text
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
```

共享运维账号：

```text
linpengxiao
```

主机现场状态：

```text
firewalld：未运行或不可用
主机 iptables：没有针对管理来源的通用 INPUT 白名单
```

因此 SSH 来源限制如果存在，只可能位于华为云安全组；现场工具无法读取云安全组配置，必须在云控制台另行核对。不要把“当前端口公网不可达”误认为宿主机没有监听。

## 3. 当前端口与暴露面

### 3.1 宿主机监听

```text
22：0.0.0.0 / IPv6，sshd
80：0.0.0.0，NPM
81：127.0.0.1，NPM 管理界面
443：0.0.0.0，NPM
8080：0.0.0.0，NPM Podman 映射
9000：0.0.0.0，NPM Podman 映射
8000：未绑定宿主机，Agent 仅容器网络
```

### 3.2 当前公网探测

```text
22：open
80：open
443：open
81：closed-or-filtered
8000：closed-or-filtered
8080：closed-or-filtered
9000：closed-or-filtered
```

注意：8080 和 9000 虽然当前被云侧或网络侧挡住，但 Podman 仍绑定 `0.0.0.0`。未来应先确认旧 `/static-backend`、`/bbs-backend`、`/minio` 和 `/bbs-img/` 的依赖，再评估移除宿主机映射。未经依赖验证不要直接删除。

## 4. NPM 与容器拓扑

### 4.1 容器

```text
npm
  image: dockerproxy.net/jc21/nginx-proxy-manager:latest
  network: nginx-proxy-manager_default
  host ports: 80, 443, 8080, 9000；81 仅 loopback

cv-php8
  image: localhost/cv-php8:latest
  network: nginx-proxy-manager_default
  无宿主机端口

agent-history-web
  image: localhost/agent-history-portal_web:latest
  user: app
  network alias: agent-history-web
  无宿主机端口
```

### 4.2 关键路径

```text
/root/nginx-proxy-manager/compose.yaml
/root/nginx-proxy-manager/data/database.sqlite
/root/nginx-proxy-manager/data/nginx/proxy_host/1.conf  # 生成文件，不可直接维护
/root/nginx-proxy-manager/letsencrypt
/var/www
/run/php-fpm

/opt/agent-history-portal
/opt/agent-history-portal/.env                         # root:root 600，禁止输出
/var/lib/agent-history
/var/backups/agent-history
/var/backups/nginx-proxy-manager
```

NPM compose 使用静态兼容映射：

```text
cv-php8 -> 10.89.0.45
```

如果 `cv-php8` 被删除后重建，其容器 IP 可能改变。此时必须：

1. 读取新容器 IP；
2. 更新 `/root/nginx-proxy-manager/compose.yaml` 的 `extra_hosts`；
3. 重建或重启 NPM；
4. 运行 `nginx -t`；
5. 验证 `/cv/`。

普通 restart 通常不会改变该映射；删除并重新创建容器才是重点风险。

## 5. c2sml.cn Proxy Host 元数据

当前 NPM Proxy Host：

```text
ID：1
域名：c2sml.cn
默认 forward：http://localhost:2345
Access List：无
证书 ID：5
Force SSL：启用
HTTP/2：启用
Block Common Exploits：启用
WebSocket：启用
HSTS：启用
HSTS includeSubDomains：启用
Trust Forwarded Proto：关闭
Advanced 配置：161 行
```

证书：

```text
Provider：Let's Encrypt
CN/SAN：c2sml.cn
notBefore：2026-06-29 02:35:07 UTC
notAfter：2026-09-27 02:35:06 UTC
```

NPM 应自动续期，但到期前 30 天必须检查续期日志和实际公网证书。当前证书不覆盖 `agent.c2sml.cn`。

既有 HSTS `includeSubDomains` 会影响未来所有子域；新增 HTTP-only 子域可能被浏览器强制升级并失败。新增子域前必须提供 HTTPS。

## 6. 持久化 Advanced 配置

唯一可维护源：

```text
/home/linpengxiao/agent-history-portal/NPM_ADVANCED_MERGED.conf
```

当前属性：

```text
行数：161
SHA-256：879ca078b86ae15afa7f96bebcb2d2b8c784846463bb6c7a633682edda1b2588
```

NPM 数据库中的 Advanced 字段已与该文件逐字节一致。

历史教训：`/xzqtest`、`/xuzhiqin`、`/cv` 曾只手工存在于生成的 `1.conf`，不在数据库中。一次 NPM UI Save 会重生成文件并删除这些手工内容。现在它们已经并入持久化 Advanced 配置；以后不能重新分离。

## 7. 当前路由表

| 路径 | 类型 | 上游/目录 | 白名单 | 基线 |
|---|---|---|---|---|
| `/` | 静态 alias | `/var/www/lr_static_web/` | 无 | 200 |
| `/xzqtest` | 静态 + PHP | `/var/www/xzqtest`、PHP-FPM socket | 无 | `/xzqtest/` 200 |
| `/xuzhiqin` | 静态 | `/var/www/xuzhiqin` | 无 | `/xuzhiqin/` 200 |
| `/cv` | PHP8 | `/var/www/cv`、`cv-php8:9000` | 无 | `/cv/` 302 到登录 |
| `/paper` | 静态/PHP | `/var/www/lr_static_web/paper/` | 无 | `/paper` 301 |
| `/subscribe-admin` | 静态 | `/var/www/lr_static_web/subscribe_admin/` | 无 | 变更时单独验证 |
| `/static-backend` | HTTP proxy | `localhost:8080` | 无 | 变更时单独验证 |
| `/bbs` | 静态 | `/var/www/lr_bbs_front/` | 无 | `/bbs` 301 |
| `/bbs-admin` | 静态 | `/var/www/lr_bbs_front/admin/` | 无 | 变更时单独验证 |
| `/bbs-backend` | HTTP proxy | `localhost:8080` | 无；存在 `Access-Control-Allow-Origin: *` | 变更时单独验证 |
| `/minio` | HTTP proxy | `localhost:9000` | 无 | 变更时单独验证 |
| `/bbs-img/` | HTTP proxy | `localhost:9000` | 无 | 变更时单独验证 |
| `/chevereto` | 静态 + PHP | `/var/www/chevereto`、PHP-FPM socket | 无 | 变更时单独验证 |
| `/api/` | HTTP proxy | `localhost:81`（NPM API） | 无 | 200，公开版本元数据 |
| `~ \.php$` | PHP regex | `/var/www/lr_static_web/`、PHP-FPM socket | 无 | 谨慎修改 |
| `/agent/healthz` | 固定响应 | 404 | 全部拒绝查看健康内容 | 404 |
| `/agent/` | Django proxy | `agent-history-web:8000` | 无；登录后授权 | 302 到前缀登录 |

`^~` 前缀用于防止 PHP regex 抢占 `/cv`、`/chevereto` 和 `/agent` 请求。增加功能时要先理解 Nginx location 优先级。

## 8. 凭据位置

不要把以下文件内容写入本文、Git、工单或聊天：

```text
/home/linpengxiao/.local/state/nginx-proxy-manager/credentials.txt
/home/linpengxiao/.local/state/agent-history-portal-production/credentials.txt
/home/linpengxiao/.local/state/agent-history-portal-production/additional-admin-credentials.txt
/opt/agent-history-portal/.env
```

当前门户身份：

```text
超级管理员：portal-admin、pxlin、zhouzhangchen、yaojunjie
普通 owner：linpengxiao
```

当前历史展示契约：

```text
顶层 history：一个对话 session
subagent thread：只允许一层，嵌入所属 session 详情
上传者：每个 session/thread 必填 uploader tag
筛选：侧栏按当前用户可见 uploader 多选，父或任一 thread 匹配即返回父 session
导出：每个顶层 session 一行；thread 嵌套于 subagent_threads
当前快照：5 个顶层 session、15 个 subagent threads、1510 条消息
```

迁移和导入遇到 uploader 归因歧义、孤儿 parent、自环/cycle、跨 owner parent 或深度超过一层时必须 fail closed。`parent_session` 使用 `PROTECT`，不得级联删除主 session 及线程。

当前功能版本为提交 `add6d40`、镜像 `e06bdfc124a872911b448603c3ed59ea0dc1ac3de2697a7263cf234dafb1d834`；生产已验证 5 roots、15 threads、20 uploader tags、1510 messages、0 owner mismatch、0 deep relationship。

NPM 管理方式：

```bash
ssh -i ~/.ssh/id_rsa -L 8181:127.0.0.1:81 linpengxiao@121.37.182.49
```

浏览器：

```text
http://127.0.0.1:8181/
```

不要重新公开 81。

## 9. 标准变更流程

### 9.1 变更前

1. 明确影响范围：主站静态、PHP、BBS、MinIO、NPM 或 Agent；
2. 明确目标访问策略：公开登录、IP 白名单或专用管理入口；
3. 若使用 IP 白名单，从 SSH accepted-key 日志和 NPM access log 双重核对来源 IP；
4. 读取当前 `NPM_ADVANCED_MERGED.conf`；
5. 获取 NPM API 当前 Proxy Host 并确认 Advanced 行数和 SHA；
6. 运行门户 SQLite 在线备份与恢复验证；
7. 创建 NPM SQLite 在线备份和生成配置快照；
8. 记录基线状态；
9. 只修改版本控制中的源文件，不修改生成文件；
10. 准备明确回滚步骤。

建议基线：

```bash
for path in / /agent/ /cv/ /xzqtest/ /xuzhiqin/ /api/ /paper /bbs; do
  curl -sS -o /dev/null -w "$path=%{http_code} %{redirect_url}\n" \
    "https://c2sml.cn$path"
done
```

### 9.2 修改 NPM Advanced

1. 编辑 `NPM_ADVANCED_MERGED.conf`；
2. 保留所有无关 location；
3. 清理行尾空白；
4. 计算 SHA-256；
5. 用实际 NPM/OpenResty 的临时 server wrapper 执行 `nginx -t`；
6. 通过 NPM UI 保存，或使用受认证 NPM API 仅更新 `advanced_config`；
7. 不把 API token 或密码写入命令参数/文件；
8. 保存后确认 API 返回 `nginx_online=true`；
9. 再次比较 Advanced SHA 和行数；
10. 运行正式 `sudo podman exec npm nginx -t`；
11. 完整执行回归矩阵；
12. 创建变更后备份。

禁止：

```text
直接编辑 /root/nginx-proxy-manager/data/nginx/proxy_host/1.conf
直接 UPDATE NPM SQLite（正常维护场景）
只复制新增 location 而丢失旧路由
无备份直接重建 NPM
```

### 9.3 增加新的主站路径功能

优先选择：

```nginx
location ^~ /new-feature/ {
    # 明确 root/alias 或私网 upstream
}
```

要求：

- 先确认是否需要登录认证和 IP 白名单；
- 不要因为 `/agent/` 公开就自动公开新的管理路径；管理功能应单独评估认证、权限和来源限制；
- 后端容器应加入 `nginx-proxy-manager_default`，使用稳定 alias；
- 后端不要发布宿主机端口；
- 需要大上传时单独设置 `client_max_body_size`；
- 不对认证 API 使用通配 `Access-Control-Allow-Origin: *`；
- 不在同一 location 中混用不清楚的 `alias`、`root` 和 rewrite；
- 使用 `^~` 时检查是否会遮蔽已有路径；
- 保存后至少验证新路径、`/`、`/cv/`、`/agent/` 和 `/api/`。

### 9.4 增加 Agent 功能

Django 侧：

1. 先写访问控制测试；
2. 所有普通用户 queryset 从 owner 范围开始；
3. 新 URL 必须在 `DJANGO_SCRIPT_NAME=/agent` 下正确反向解析；
4. 不硬编码 `/history`、`/admin`、`/static` 根路径；
5. 检查 CSRF、Cookie Path、CSP 和导出授权；
6. 运行完整 Django 测试、Ruff、pip-audit 和生产 check；
7. 构建真实容器并用前缀模式烟雾测试；
8. 备份后部署；
9. 公网验证普通用户、管理员和匿名用户。

NPM `/agent/` location 通常不需要为每个 Django 子路由单独修改；只有上传大小、超时、公开/白名单策略或 upstream 发生变化时才修改。

### 9.5 增加独立容器后端

1. 容器以非 root 用户运行；
2. 加入 `nginx-proxy-manager_default`；
3. 设置稳定 network alias；
4. 不配置 `ports:`，除非有经过审查的明确需求；
5. NPM 使用容器 alias 和内部端口；
6. 健康检查只在私网；
7. 备份数据目录；
8. 验证重建后的 alias/IP；
9. 不使用易失容器 IP，除非像 `cv-php8` 一样记录兼容映射和恢复步骤。

## 10. 调整公开访问与管理白名单

### 10.1 当前公开状态

`/agent/` location 当前没有 `allow`/`deny` 指令。所有来源均可打开登录页，但历史数据仍需 Django 身份认证和 owner/admin 授权。变更后必须验证：

1. 外部网络可访问 `/agent/accounts/login/`；
2. 匿名访问 `/agent/history/` 只跳转登录，不返回历史内容；
3. 普通用户不能进入其他 owner 或 Admin；
4. 登录失败锁定、CSRF、Cookie Secure/Path 和安全响应头正常；
5. `/agent/healthz` 仍为 404。

### 10.2 恢复 IP 白名单

如果未来决定只允许管理来源：

1. 从管理员终端确认固定公网出口 IP；
2. 在服务器 SSH 日志确认 accepted-key 来源；
3. 在 NPM access log 确认 `[Client ...]`；
4. 在 `NPM_ADVANCED_MERGED.conf` 的 `/agent/` location 内新增一个或多个 `allow`；
5. 以 `deny all;` 结束规则；
6. 执行 Nginx 临时配置测试并备份 NPM；
7. 通过 UI/API 保存完整 Advanced；
8. 从允许来源验证 302/200；
9. 从未授权来源验证 403；
10. 更新文档、SHA、备份和 Git。

### 10.3 再次公开

从白名单模式恢复公开时，直接删除 `/agent/` location 内所有 `allow` 和 `deny all`，不要写 `allow 0.0.0.0/0`。保存后从至少两个不同外部来源验证登录页可达，并重复匿名/普通用户/管理员权限验收。

### 10.4 未来需要真正的公用白名单

当前没有全站公用白名单。如果两个以上管理路径需要共享同一组 CIDR，应单独设计并审查一种集中机制，例如：

- NPM Access List，只挂到专用管理子域；或
- Nginx 自定义 include，由多个受保护 location 引用。

不要把公用白名单直接挂到当前 `c2sml.cn` Proxy Host，否则主站和公开 Agent 登录入口都会被限制。集中白名单实施前必须备份和完成全站回归。

## 11. 验收矩阵

### 11.1 NPM 与端口

```text
sudo podman exec npm nginx -t                      -> successful
agent-history-portal.service                       -> active
agent-history-web Host Ports                       -> {}
81/8000/8080/9000 外网                              -> closed-or-filtered
80/443 外网                                         -> open
```

### 11.2 主站

```text
/               -> 200
/cv/            -> 302 /cv/login.php
/xzqtest/       -> 200
/xuzhiqin/      -> 200
/api/           -> 200（现有风险，不代表推荐）
/paper          -> 301
/bbs            -> 301
```

### 11.3 Agent

任意公网来源：

```text
/agent/                         -> 302 到 /agent/accounts/login/
/agent/accounts/login/          -> 200
/agent/healthz                  -> 404
登录后 /agent/history/          -> 200
普通用户 /agent/admin/          -> admin login，不得进入后台
超级管理员 /agent/admin/        -> 200
搜索、详情、导入、导出、退出      -> 200/正常重定向
导出记录数                       -> 当前 20
Session/CSRF Cookie Path         -> /agent/
Cookie Secure                    -> true
```

匿名访问 `/agent/history/` 必须跳转登录；不得返回会话标题、消息或导出数据。

## 12. 回滚

### 12.1 NPM Advanced 回滚

优先恢复公开状态工作检查点：

```text
/var/backups/nginx-proxy-manager/database-20260817T035230Z-public-agent.sqlite
/var/backups/nginx-proxy-manager/config-20260817T035230Z-public-agent.tar.gz
/var/backups/agent-history/db-20260817T035231Z.sqlite3
```

如只撤销 Agent：

1. 从 `NPM_ADVANCED_MERGED.conf` 删除两个 `/agent` location；
2. 保留 `/xzqtest`、`/xuzhiqin`、`/cv` 及其他主站路由；
3. 测试并通过 NPM UI/API 保存完整 Advanced；
4. 运行 `nginx -t`；
5. 验证主站路由；
6. 从 Agent `.env` 删除 `DJANGO_SCRIPT_NAME=/agent` 和 `c2sml.cn` origin 配置；
7. 重启 Agent 服务并验证私网上游。

不要仅恢复旧生成 `1.conf`；下一次 NPM Save 仍会覆盖它。

### 12.2 Agent 数据回滚

使用：

```bash
sudo /opt/agent-history-portal/scripts/restore-verify.sh \
  /var/backups/agent-history/db-<timestamp>.sqlite3
```

先在临时位置验证，不要覆盖活动库。正式恢复需单独停服务、备份当前库、原子替换并检查权限。

## 13. 已知风险与维护优先级

### 高优先级评估项

1. `/api/` 将公网请求代理到 NPM 81，公开版本元数据并持续受到扫描；确认无依赖后应移除或改为管理白名单；
2. 8080 和 9000 仍绑定 `0.0.0.0`，当前仅依赖云侧阻断；确认依赖后应移除不必要映射；
3. 主机没有启用 firewalld，需确认华为云安全组是实际边界；
4. NPM 镜像使用 `latest`，未来应在备份和测试后固定版本或 digest；
5. CentOS 8 已 EOL，应规划迁移。

### 中优先级

1. `/agent/` 与主站共享 origin；最终迁移到 `agent.c2sml.cn`；
2. 共享 `linpengxiao` 运维账号降低人员审计粒度；
3. `cv-php8` 使用静态容器 IP 兼容映射；
4. 证书续期必须监控；
5. 现有 `/bbs-backend` 使用通配 CORS，应确认是否处理认证数据。

## 14. 维护记录模板

每次变更在状态文档中记录：

```markdown
## YYYY-MM-DD 变更标题

- 变更人：
- 影响路径：
- 变更前 Git SHA：
- NPM Advanced SHA：
- 访问策略/白名单：
- 数据库备份：
- NPM 配置备份：
- 变更内容：
- nginx -t：
- 主站回归：
- Agent 普通用户验收：
- Agent 管理员验收：
- 匿名访问与登录跳转：
- 端口探测：
- 已知问题：
- 回滚步骤：
```

## 15. 一句话结论

主站当前没有公用白名单；`/agent/` 登录入口也已公开，数据访问依赖 Django 登录和 owner/admin 授权。未来修改网站时，必须以 `NPM_ADVANCED_MERGED.conf` 为唯一 Advanced 配置源，先备份、再测试、通过 UI/API 保存完整配置，最后执行主站、Agent 匿名/登录权限和端口三组验收。
