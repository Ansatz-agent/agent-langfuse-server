from pathlib import Path

from django.test import SimpleTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DeploymentFileTests(SimpleTestCase):
    def test_dashboard_mobile_grid_can_shrink_without_page_overflow(self):
        stylesheet = (PROJECT_ROOT / "history/static/history/app.css").read_text()

        shrink_rule = (
            ".dashboard-shell, .dashboard-sidebar, .dashboard-main "
            "{ min-width: 0; max-width: 100%; }"
        )
        self.assertIn(shrink_rule, stylesheet)
        self.assertIn(
            ".dashboard-shell { grid-template-columns: minmax(0, 1fr); }",
            stylesheet,
        )
        self.assertIn(
            ".dashboard-side-nav { min-width: 0; max-width: 100%; overflow-x: auto; }",
            stylesheet,
        )

    def test_runtime_requirements_include_direct_markdown_dependency(self):
        requirements = (PROJECT_ROOT / "requirements.txt").read_text().splitlines()

        self.assertIn("markdown-it-py==4.2.0", requirements)

    def test_systemd_provisions_host_paths_before_starting_compose(self):
        unit = (PROJECT_ROOT / "systemd/agent-history-portal.service").read_text()

        self.assertIn("ExecStartPre=/opt/agent-history-portal/scripts/provision-host.sh", unit)

    def test_provision_script_separates_data_and_backup_ownership(self):
        script = (PROJECT_ROOT / "scripts/provision-host.sh").read_text()

        self.assertIn("DATA_DIR=${DATA_DIR:-/var/lib/agent-history}", script)
        self.assertIn("BACKUP_DIR=${BACKUP_DIR:-/var/backups/agent-history}", script)
        self.assertIn('install -d -m 0700 -o "$APP_UID" -g "$APP_GID" "$DATA_DIR"', script)
        self.assertIn('test ! -L "$DATA_DIR"', script)
        self.assertNotIn("chown -R", script)
        self.assertIn('test ! -L "$BACKUP_DIR"', script)
        self.assertIn('install -d -m 0700 -o root -g root "$BACKUP_DIR"', script)

    def test_backup_is_created_entirely_in_root_controlled_directory(self):
        script = (PROJECT_ROOT / "scripts/backup.sh").read_text()

        self.assertIn("BACKUP_DIR=${BACKUP_DIR:-/var/backups/agent-history}", script)
        self.assertIn('WORK_DIR=$(mktemp -d "$BACKUP_DIR/.work-$STAMP.XXXXXX")', script)
        self.assertIn('TEMP="$WORK_DIR/db.sqlite3"', script)
        self.assertIn("podman run --rm -i", script)
        self.assertIn("--network none", script)
        self.assertIn("--read-only", script)
        self.assertIn("--cap-drop all", script)
        self.assertIn("--security-opt no-new-privileges", script)
        self.assertIn("--user 10001:10001", script)
        self.assertIn('-v "$DATA_DIR:/source:ro"', script)
        self.assertIn('-v "$WORK_DIR:/output"', script)
        self.assertNotIn('-v "$BACKUP_DIR:/backup"', script)
        self.assertIn("--entrypoint python", script)
        self.assertIn('mv "$TEMP" "$TARGET"', script)
        self.assertNotIn("podman exec", script)
        self.assertNotIn('python3 - "$SOURCE" "$TEMP"', script)
        self.assertNotIn("/data/.backup-", script)

    def test_temporary_subpath_rollout_is_public_and_strips_prefix(self):
        guide = (PROJECT_ROOT / "NPM_SUBPATH_ROLLOUT.md").read_text()

        self.assertIn("publicly reachable", guide)
        self.assertIn("Django login", guide)
        self.assertIn("location ^~ /agent/", guide)
        self.assertNotIn("location = /agent {", guide)
        self.assertIn("rewrite ^/agent/$ /dashboard/ break;", guide)
        self.assertIn("rewrite ^/agent/(.*)$ /$1 break;", guide)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", guide)
        self.assertNotIn("$proxy_add_x_forwarded_for", guide)
        self.assertIn("proxy_pass http://agent-history-web:8000;", guide)
        self.assertIn("location ^~ /agent/healthz", guide)
        self.assertIn("DJANGO_SCRIPT_NAME=/agent", guide)
        self.assertIn("DJANGO_CSRF_TRUSTED_ORIGINS=https://c2sml.cn", guide)
        self.assertIn("Do not edit the NPM SQLite database", guide)

        merged = (PROJECT_ROOT / "NPM_ADVANCED_MERGED.conf").read_text()
        self.assertEqual(merged.count("location ^~ /xzqtest {"), 1)
        self.assertEqual(merged.count("location ^~ /xuzhiqin {"), 1)
        self.assertEqual(merged.count("location ^~ /cv {"), 1)
        self.assertEqual(merged.count("location ^~ /agent/"), 2)
        self.assertIn("rewrite ^/agent/$ /dashboard/ break;", merged)
        self.assertNotIn("allow 112.45.67.43/32;", merged)
        self.assertNotIn("deny all;", merged)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", merged)

    def test_site_maintenance_documents_current_boundaries(self):
        guide = (PROJECT_ROOT / "SITE_MAINTENANCE.md").read_text()

        self.assertIn("NPM Access List 数量：0", guide)
        self.assertIn("`/agent/` 登录入口也已按用户要求公开", guide)
        self.assertIn("不要使用：\n\n```nginx\nallow 0.0.0.0/0;", guide)
        self.assertIn("不直接编辑 NPM 生成的", guide)
        self.assertIn("不直接修改 NPM SQLite 数据库", guide)
        self.assertIn("NPM_ADVANCED_MERGED.conf", guide)
        self.assertIn("/var/backups/nginx-proxy-manager", guide)
        self.assertIn("additional-admin-credentials.txt", guide)
        self.assertIn("超级管理员：portal-admin、pxlin、zhouzhangchen、yaojunjie", guide)
        self.assertIn("匿名访问 `/agent/history/` 必须跳转登录", guide)
        self.assertIn("8080 和 9000 仍绑定", guide)
        self.assertNotIn("PRIVATE KEY", guide)
