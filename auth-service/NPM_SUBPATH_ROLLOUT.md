# Temporary `/agent/` rollout on `c2sml.cn`

> Status: live and publicly reachable at `https://c2sml.cn/agent/`; authentication remains enforced by Django.
> Temporary URL: `https://c2sml.cn/agent/`
> Preferred final URL: `https://agent.c2sml.cn/`

## Security boundary

A path prefix does not create a separate browser origin. The existing static site, PHP routes, BBS routes, MinIO routes, and `/agent/` all share the `https://c2sml.cn` origin. Any same-origin XSS or compromised third-party script can potentially read an authenticated portal page. Cookie names and `Path=/agent/` prevent accidental cookie collisions, but they do not provide origin isolation.

The `/agent/` route is now publicly reachable so users outside the current management IP can reach the login page. This does not make the portal anonymous: Django login, CSRF, django-axes, owner-scoped authorization, and superuser-only Admin remain required. Do not expose database contents or internal health information through additional unauthenticated paths.

The existing `c2sml.cn` advanced configuration also exposes the NPM status API root at `/api/`, which currently returns NPM version metadata. Public access logs show active automated probing of `/api/` paths. This is pre-existing and must not be edited or removed without first checking whether another site component depends on it.

## Application environment

Set these values in `/opt/agent-history-portal/.env` only after the prefix-capable image has passed tests:

```dotenv
DJANGO_ALLOWED_HOSTS=c2sml.cn,agent.c2sml.cn,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://c2sml.cn,https://agent.c2sml.cn
DJANGO_SCRIPT_NAME=/agent
DJANGO_SESSION_COOKIE_NAME=agent_history_sessionid
DJANGO_CSRF_COOKIE_NAME=agent_history_csrftoken
```

`DJANGO_SCRIPT_NAME` must be `/agent` without a trailing slash. The application then generates `/agent/accounts/login/`, `/agent/dashboard/`, `/agent/history/`, `/agent/admin/`, and `/agent/static/` URLs. NPM must strip `/agent/` before proxying upstream.

## NPM Advanced configuration snippet

Apply through the NPM UI to the existing `c2sml.cn` Proxy Host. Do not edit the NPM SQLite database or generated `1.conf` directly.

```nginx
location ^~ /agent/healthz {
    return 404;
}

location ^~ /agent/ {
    client_max_body_size 26m;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /agent;
    proxy_set_header Connection "";

    rewrite ^/agent/$ /dashboard/ break;
    rewrite ^/agent/(.*)$ /$1 break;
    proxy_pass http://agent-history-web:8000;
    proxy_redirect off;
}
```

The `^~` modifier prevents the existing PHP regex locations from intercepting `/agent/` requests. The first rewrite maps the portal root to the authenticated overview dashboard while remaining inside the public location. The upstream has no host-published port and remains reachable only on the NPM Podman network.

The complete persistent Advanced configuration is stored at `NPM_ADVANCED_MERGED.conf`. It also contains the recovered `/xzqtest`, `/xuzhiqin`, and `/cv` blocks that previously existed only in generated `1.conf`; future NPM saves must preserve this merged source rather than editing generated files.

## Deployment checkpoint

Completed on the server:

- portal database backup and restore verification;
- root-only deployment-tree and pre-prefix `.env` backups;
- commit `3e8c20e` deployed as image `d75b690a18e51983197dc94d3475d76717c149095e2f2f65e34ed9798a500d99`;
- prefix environment values applied atomically;
- NPM-container upstream tests for login, redirect, Cookie Path, hashed static files, and health;
- consistent NPM SQLite and configuration backups:
  - pre-change: `/var/backups/nginx-proxy-manager/database-20260816T151725Z.sqlite`;
  - pre-change: `/var/backups/nginx-proxy-manager/config-20260816T151725Z.tar.gz`;
  - pre-public: `/var/backups/nginx-proxy-manager/database-20260817T034118Z-before-public-agent.sqlite`;
  - pre-public: `/var/backups/nginx-proxy-manager/config-20260817T034118Z-before-public-agent.tar.gz`;
  - public-state live database: `/var/backups/nginx-proxy-manager/database-20260817T035230Z-public-agent.sqlite`;
  - public-state generated config: `/var/backups/nginx-proxy-manager/config-20260817T035230Z-public-agent.tar.gz`;
  - public-state portal database: `/var/backups/agent-history/db-20260817T035231Z.sqlite3`.

Completed through NPM's authenticated API using a partial update of `advanced_config` only. The API response matched the local 161-line file byte-for-byte, all other Proxy Host fields remained unchanged, and NPM reported `nginx_online=true`.

Post-apply checks passed:

- public `/agent/` redirects to the prefixed login flow from both local and server-originated requests;
- an unauthenticated request cannot read history and is redirected to login;
- `/agent/healthz` receives 404;
- real user/admin login, CSRF, Cookie Path, static files, list, detail, search, import, export, logout, and admin isolation passed;
- `/`, `/cv/`, `/xzqtest/`, `/xuzhiqin/`, `/api/`, `/paper`, and `/bbs` passed regression checks;
- NPM `nginx -t` passed and the application still publishes no host port;
- public-state portal and NPM backups passed SQLite integrity/restore checks.

Application behavior after the session/thread feature:

- the list exposes only root sessions; the current production snapshot is 5 roots and 15 embedded direct subagent threads;
- the detail page renders the root messages plus embedded thread messages and uploader tags;
- child search hits return the root session;
- the uploader sidebar supports repeated `uploader=<id>` parameters and owner-scoped multi-select filtering;
- export emits one NDJSON row per root with nested `subagent_threads`; the current export contains 5 rows, 15 nested threads, and 1510 messages;
- invalid/foreign uploader filters return no sessions rather than broadening the result;
- migrations/imports reject ambiguous uploader attribution, orphan/cyclic parents and depths greater than one.

Session/thread release checkpoint:

```text
commit: add6d40
image: e06bdfc124a872911b448603c3ed59ea0dc1ac3de2697a7263cf234dafb1d834
pre-change DB: /var/backups/agent-history/db-20260817T140424Z.sqlite3
pre-change tree: /var/backups/agent-history-deploy/portal-20260817T140424Z-before-thread-feature.tar.gz
old tree: /opt/agent-history-portal.before-add6d40-20260817T140713Z
post-change DB: /var/backups/agent-history/db-20260817T141212Z.sqlite3
```

## Required change procedure

1. Create a fresh application backup and verify restoration.
2. Back up the NPM SQLite database, generated proxy-host configuration, and compose file.
3. Record the status of `/`, `/cv/`, `/api/`, `/paper`, and `/bbs` before the change.
4. Deploy the prefix-capable application image while the public route is still absent.
5. Set the application environment above and restart the service.
6. Verify the application internally through the NPM container using stripped upstream paths.
7. Open an SSH tunnel to NPM:

   ```bash
   ssh -i ~/.ssh/id_rsa -L 8181:127.0.0.1:81 linpengxiao@121.37.182.49
   ```

8. Open `http://127.0.0.1:8181`, edit the existing `c2sml.cn` Proxy Host, and add the snippet in Advanced configuration. An authorized NPM administrator must perform this step; do not send the NPM password in chat.
9. Save, then run `sudo podman exec npm nginx -t`.
10. From any external network, verify the public login redirect, static files, list, detail, search, export, logout, CSRF, Cookie Path, and admin authorization.
11. From an unauthenticated client, verify `/agent/history/` redirects to `/agent/accounts/login/` and no history data is returned.
12. Recheck `/`, `/cv/`, `/api/`, `/paper`, and `/bbs` for regressions.

## Rollback

1. Remove only the two `/agent` location blocks through the NPM UI and save.
2. Run `sudo podman exec npm nginx -t` and recheck the existing site.
3. Remove `DJANGO_SCRIPT_NAME=/agent` and the `c2sml.cn` additions from the application environment if the application should return to dedicated-host mode.
4. Restart `agent-history-portal.service` and verify the private `/healthz` upstream.

When `agent.c2sml.cn` DNS becomes available, remove the path location, clear `DJANGO_SCRIPT_NAME`, restore cookie paths to `/`, create a dedicated Proxy Host and certificate, and repeat the public acceptance tests.
