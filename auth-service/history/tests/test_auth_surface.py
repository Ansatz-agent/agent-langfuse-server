from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    ALLOWED_HOSTS=["c2sml.cn", "testserver"],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
)
class AuthSurfaceTests(TestCase):
    request_options = {
        "HTTP_HOST": "c2sml.cn",
        "HTTP_X_FORWARDED_PROTO": "https",
    }

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="member", password="temporary-strong-pass"
        )

    def test_exact_auth_and_private_route_paths(self):
        self.assertEqual(reverse("login"), "/auth/login/")
        self.assertEqual(reverse("logout"), "/auth/logout/")
        self.assertEqual(reverse("client-session"), "/auth/api/session/")
        self.assertEqual(
            reverse("native-client-session"),
            "/auth/api/client-session/",
        )
        self.assertEqual(
            reverse("native-client-session-current"),
            "/auth/api/client-session/current/",
        )
        self.assertEqual(reverse("trace-token"), "/auth/api/trace-token/")
        self.assertEqual(
            reverse("trace-token-revoke-device"),
            "/auth/api/trace-token/revoke-device/",
        )
        self.assertEqual(
            reverse("trace-token-introspect"),
            "/internal/trace-token/introspect/",
        )

    def test_cookie_contract_is_host_only_secure_and_root_scoped(self):
        self.assertEqual(settings.SESSION_COOKIE_NAME, "__Host-ansatz_sessionid")
        self.assertEqual(settings.CSRF_COOKIE_NAME, "__Host-ansatz_csrftoken")
        self.assertEqual(settings.SESSION_COOKIE_PATH, "/")
        self.assertEqual(settings.CSRF_COOKIE_PATH, "/")
        self.assertIs(settings.SESSION_COOKIE_SECURE, True)
        self.assertIs(settings.CSRF_COOKIE_SECURE, True)
        self.assertIs(settings.SESSION_COOKIE_HTTPONLY, True)
        self.assertIsNone(settings.SESSION_COOKIE_DOMAIN)
        self.assertIsNone(settings.CSRF_COOKIE_DOMAIN)

    def test_login_uses_ansatz_brand_static_path_and_redirects_to_traces(self):
        login_page = self.client.get(reverse("login"), **self.request_options)
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, "/auth/static/history/app.css")
        csrf_cookie = login_page.cookies["__Host-ansatz_csrftoken"]
        self.assertEqual(csrf_cookie["path"], "/")
        self.assertTrue(csrf_cookie["secure"])
        self.assertEqual(csrf_cookie["domain"], "")

        login = self.client.post(
            reverse("login"),
            {"username": "member", "password": "temporary-strong-pass"},
            **self.request_options,
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login["Location"], "/traces/")
        session_cookie = login.cookies["__Host-ansatz_sessionid"]
        self.assertEqual(session_cookie["path"], "/")
        self.assertTrue(session_cookie["secure"])
        self.assertTrue(session_cookie["httponly"])

    def test_agent_routes_do_not_exist(self):
        self.assertEqual(self.client.get("/agent/", **self.request_options).status_code, 404)
        self.assertEqual(
            self.client.get("/agent/accounts/login/", **self.request_options).status_code,
            404,
        )
