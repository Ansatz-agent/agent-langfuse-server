from typing import Any

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import get_script_prefix, set_script_prefix

from history.models import HistorySession


@override_settings(
    FORCE_SCRIPT_NAME="/agent",
    STATIC_URL="/agent/static/",
    SESSION_COOKIE_NAME="agent_history_sessionid",
    SESSION_COOKIE_PATH="/agent/",
    CSRF_COOKIE_NAME="agent_history_csrftoken",
    CSRF_COOKIE_PATH="/agent/",
    ALLOWED_HOSTS=["c2sml.cn", "testserver"],
)
class SubpathRuntimeTests(TestCase):
    request_options: dict[str, Any] = {
        "HTTP_HOST": "c2sml.cn",
        "HTTP_X_FORWARDED_PROTO": "https",
    }

    def setUp(self):
        self.original_script_prefix = get_script_prefix()
        set_script_prefix("/agent/")
        self.user = get_user_model().objects.create_user(
            username="member", password="temporary-strong-pass"
        )
        self.session = HistorySession.objects.create(
            owner=self.user,
            uploader=self.user,
            external_id="subpath-session",
            title="Subpath session",
        )

    def tearDown(self):
        set_script_prefix(self.original_script_prefix)
        super().tearDown()

    def test_anonymous_redirect_includes_script_prefix(self):
        response = self.client.get("/history/", **self.request_options)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "/agent/accounts/login/?next=/agent/history/",
        )

    def test_login_static_detail_links_and_cookie_paths_include_prefix(self):
        login_page = self.client.get("/accounts/login/", **self.request_options)
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, "/agent/static/history/app.css")
        self.assertEqual(login_page.cookies["agent_history_csrftoken"]["path"], "/agent/")

        login = self.client.post(
            "/accounts/login/",
            {"username": "member", "password": "temporary-strong-pass"},
            **self.request_options,
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login["Location"], "/agent/dashboard/")
        self.assertEqual(login.cookies["agent_history_sessionid"]["path"], "/agent/")

        history = self.client.get("/history/", **self.request_options)
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, f"/agent/history/session/{self.session.pk}/")
        self.assertContains(history, "/agent/features/history-synthesis/")
        self.assertContains(history, "/agent/features/api-credits/")
