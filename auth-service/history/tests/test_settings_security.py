import json
import os
import subprocess
import sys

from django.test import SimpleTestCase


VALID_DJANGO_SECRET = (
    "A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8U9v0W1x2Y3z4a5B6c7D8e9F0g1H2"
)
VALID_TRACE_GATEWAY_SECRET = "V7tQ2xL9pR4mK8nD6sF3wH5jC1zB0aY-uE_gI-oP"
VALID_LANGFUSE_PUBLIC_KEY = "pk-lf-test-settings"
VALID_LANGFUSE_SECRET_KEY = "sk-lf-test-settings"


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
            "TRACE_GATEWAY_INTERNAL_SECRET",
            "LANGFUSE_PROJECT_PUBLIC_KEY",
            "LANGFUSE_PROJECT_SECRET_KEY",
        ):
            env.pop(name, None)
        if overrides.get("DJANGO_ENV") == "production" and (
            "TRACE_GATEWAY_INTERNAL_SECRET" not in overrides
        ):
            env["TRACE_GATEWAY_INTERNAL_SECRET"] = VALID_TRACE_GATEWAY_SECRET
        if overrides.get("DJANGO_ENV") == "production":
            env.setdefault("LANGFUSE_PROJECT_PUBLIC_KEY", VALID_LANGFUSE_PUBLIC_KEY)
            env.setdefault("LANGFUSE_PROJECT_SECRET_KEY", VALID_LANGFUSE_SECRET_KEY)
        for name, value in overrides.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
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
            DJANGO_SECRET_KEY=VALID_DJANGO_SECRET,
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

    def test_auth_settings_use_fixed_routes_static_and_host_cookies(self):
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
                VALID_DJANGO_SECRET
            ),
            DJANGO_SCRIPT_NAME="/agent",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertIsNone(values["force_script_name"])
        self.assertEqual(values["login_url"], "/auth/login/")
        self.assertEqual(values["static_url"], "/auth/static/")
        self.assertEqual(values["session_cookie_name"], "__Host-ansatz_sessionid")
        self.assertEqual(values["session_cookie_path"], "/")
        self.assertEqual(values["csrf_cookie_name"], "__Host-ansatz_csrftoken")
        self.assertEqual(values["csrf_cookie_path"], "/")

    def test_missing_langfuse_project_keys_refuses_production_startup(self):
        result = self.run_settings_import(
            DJANGO_ENV="production",
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY=VALID_DJANGO_SECRET,
            LANGFUSE_PROJECT_PUBLIC_KEY=None,
            LANGFUSE_PROJECT_SECRET_KEY=None,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LANGFUSE_PROJECT_PUBLIC_KEY", result.stderr)

    def test_missing_trace_gateway_secret_refuses_production_startup(self):
        result = self.run_settings_import(
            DJANGO_ENV="production",
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY=VALID_DJANGO_SECRET,
            TRACE_GATEWAY_INTERNAL_SECRET=None,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRACE_GATEWAY_INTERNAL_SECRET", result.stderr)

    def test_weak_trace_gateway_secrets_refuse_production_startup(self):
        for value in ("x", "x" * 64, "change-me-" + "a1b2c3" * 10):
            with self.subTest(value=value[:20]):
                result = self.run_settings_import(
                    DJANGO_ENV="production",
                    DJANGO_DEBUG="0",
                    DJANGO_SECRET_KEY=VALID_DJANGO_SECRET,
                    TRACE_GATEWAY_INTERNAL_SECRET=value,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("TRACE_GATEWAY_INTERNAL_SECRET", result.stderr)
