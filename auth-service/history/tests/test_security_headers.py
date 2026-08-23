from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


class SecurityHeaderTests(TestCase):
    def test_health_endpoint_has_conservative_security_headers(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertIn("camera=()", response["Permissions-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")

    @override_settings(FORCE_SCRIPT_NAME="/agent")
    def test_prefixed_admin_uses_admin_compatible_csp(self):
        admin = get_user_model().objects.create_superuser(username="admin", password="strong-pass")
        self.client.force_login(admin)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.path, "/agent/admin/")
        self.assertEqual(response.wsgi_request.path_info, "/admin/")
        self.assertIn("script-src 'self' 'unsafe-inline'", response["Content-Security-Policy"])
