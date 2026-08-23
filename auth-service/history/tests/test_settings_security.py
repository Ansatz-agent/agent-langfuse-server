import json
import os
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionSettingsFailClosedTests(SimpleTestCase):
    def run_settings_import(self, code="import config.settings", **overrides):
        env = os.environ.copy()
        for name in (
            "DJANGO_ENV",
            "DJANGO_DEBUG",
            "DJANGO_SECRET_KEY",
            "DJANGO_SCRIPT_NAME",
            "DJANGO_SESSION_COOKIE_NAME",
            "DJANGO_CSRF_COOKIE_NAME",
        ):
            env.pop(name, None)
        env.update(overrides)
        return subprocess.run(  # noqa: S603 - test-only code defined in this module
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_missing_production_secret_refuses_to_start(self):
        result = self.run_settings_import(DJANGO_ENV="production", DJANGO_DEBUG="0")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_weak_or_placeholder_production_secrets_refuse_to_start(self):
        weak_values = (
            "x",
            "x" * 64,
            "django-insecure-placeholder-value-that-is-long-but-still-unsafe",
            "Django-insecure-placeholder-value-that-is-long-but-still-unsafe",
            "replace-with-a-unique-random-secret",
            "change-me-" + "a1b2c3" * 10,
            "your-secret-key-" + "a1b2c3" * 10,
            "default-secret-" + "a1b2c3" * 10,
            "secret-key-" + "a1b2c3" * 10,
            "password" * 8,
            "0123456789" * 6,
        )
        for value in weak_values:
            with self.subTest(value=value[:20]):
                result = self.run_settings_import(
                    DJANGO_ENV="production",
                    DJANGO_DEBUG="0",
                    DJANGO_SECRET_KEY=value,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_high_entropy_production_secret_allows_startup(self):
        result = self.run_settings_import(
            DJANGO_ENV="production",
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY=("A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8U9v0W1x2Y3z4a5B6c7D8e9F0g1H2"),
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_debug_mode_refuses_to_start(self):
        result = self.run_settings_import(
            DJANGO_ENV="production",
            DJANGO_DEBUG="1",
            DJANGO_SECRET_KEY="temporary-test-key-with-enough-entropy-1234567890",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_DEBUG", result.stderr)

    def test_subpath_settings_scope_urls_static_and_cookies(self):
        code = """
import json
import os
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from django.conf import settings
from django.urls import reverse
print(json.dumps({
    "force_script_name": settings.FORCE_SCRIPT_NAME,
    "login_url": reverse("login"),
    "static_url": settings.STATIC_URL,
    "session_cookie_name": settings.SESSION_COOKIE_NAME,
    "session_cookie_path": settings.SESSION_COOKIE_PATH,
    "csrf_cookie_name": settings.CSRF_COOKIE_NAME,
    "csrf_cookie_path": settings.CSRF_COOKIE_PATH,
}))
"""
        result = self.run_settings_import(
            code,
            DJANGO_ENV="production",
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY=(
                "A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8U9v0W1x2Y3z4a5B6c7D8e9F0g1H2"
            ),
            DJANGO_SCRIPT_NAME="/agent",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual(values["force_script_name"], "/agent")
        self.assertEqual(values["login_url"], "/agent/accounts/login/")
        self.assertEqual(values["static_url"], "/agent/static/")
        self.assertEqual(values["session_cookie_name"], "agent_history_sessionid")
        self.assertEqual(values["session_cookie_path"], "/agent/")
        self.assertEqual(values["csrf_cookie_name"], "agent_history_csrftoken")
        self.assertEqual(values["csrf_cookie_path"], "/agent/")

    def test_invalid_subpath_settings_refuse_to_start(self):
        for value in ("agent", "/agent/", "//agent", "/agent//history", "/agent?x"):
            with self.subTest(value=value):
                result = self.run_settings_import(
                    DJANGO_ENV="production",
                    DJANGO_DEBUG="0",
                    DJANGO_SECRET_KEY=(
                        "A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8U9v0W1x2Y3z4a5B6c7D8e9F0g1H2"
                    ),
                    DJANGO_SCRIPT_NAME=value,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DJANGO_SCRIPT_NAME", result.stderr)
