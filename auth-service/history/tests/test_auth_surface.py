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
        self.assertEqual(reverse("trace-index"), "/traces/sessions/")
        self.assertEqual(reverse("trace-runs-legacy"), "/traces/runs/")
        self.assertEqual(
            reverse("trace-step-fragment", args=["trace-a", "observation-a"]),
            "/traces/trace/trace-a/step/observation-a/",
        )
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
        self.assertContains(login_page, "/auth/static/history/trace_analytics.css")
        self.assertContains(login_page, 'class="analytics-topbar"', html=False)
        self.assertContains(login_page, 'class="analytics-auth-card"', html=False)
        self.assertContains(login_page, "Personal Trace Analytics")
        self.assertNotContains(login_page, "/auth/static/history/app.css")
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

    def test_analytics_secondary_text_remains_readable(self):
        stylesheet = (
            settings.BASE_DIR / "history/static/history/trace_analytics.css"
        ).read_text()
        dashboard = (settings.BASE_DIR / "templates/traces/dashboard.html").read_text()
        script = (
            settings.BASE_DIR / "history/static/history/trace_analytics.js"
        ).read_text()

        self.assertIn(".analytics-hero-insight span", stylesheet)
        self.assertIn("font-size: 13px", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.analytics-hero p \{[^}]*font-weight: 650",
        )
        self.assertIn(".sidebar-kpi small { font-size: 12px", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.ranked-models a > span:first-child strong \{[^}]*font-size: 14px",
        )
        self.assertIn(".mix-list li {", stylesheet)
        self.assertIn("font-size: 12px", stylesheet)
        self.assertRegex(
            stylesheet,
            r"\.trend-column small \{[^}]*grid-row: 2[^}]*font-size: 12px[^}]*white-space: nowrap",
        )
        self.assertRegex(
            stylesheet,
            r"\.trend-column svg \{[^}]*max-width: 64px",
        )
        self.assertIn('class="trend-plot"', dashboard)
        self.assertIn('data-bar-height="{{ row.svg_height }}"', dashboard)
        self.assertIn("value.dataset.barHeight", script)
        self.assertRegex(
            stylesheet,
            r"\.analytics-auth-heading small \{[^}]*font-size: 13px",
        )

    def test_dashboard_dark_theme_uses_cohesive_surface_tokens(self):
        stylesheet = (
            settings.BASE_DIR / "history/static/history/trace_analytics.css"
        ).read_text()

        for selector in (
            ':root[data-theme="dark"] .analytics-hero',
            ':root[data-theme="dark"] .sidebar-kpi',
            ':root[data-theme="dark"] .analytics-hero-insight span',
            ':root[data-theme="dark"] .activity-grid span',
        ):
            self.assertIn(selector, stylesheet)

    def test_recent_sessions_table_uses_readable_type_scale(self):
        stylesheet = (
            settings.BASE_DIR / "history/static/history/trace_analytics.css"
        ).read_text()

        self.assertRegex(
            stylesheet,
            r"\.analytics-table-panel table \{[^}]*font-size: 14px",
        )
        self.assertRegex(
            stylesheet,
            r"\.analytics-table-panel th \{[^}]*font-size: 12px",
        )
        self.assertRegex(
            stylesheet,
            r"\.analytics-table-panel td small \{[^}]*font-size: 12px",
        )

    def test_token_composition_legend_uses_readable_type_scale(self):
        stylesheet = (
            settings.BASE_DIR / "history/static/history/trace_analytics.css"
        ).read_text()

        self.assertRegex(
            stylesheet,
            r"\.token-legend span \{[^}]*font-size: 13px",
        )

    def test_trace_content_uses_readable_type_scale(self):
        stylesheet = (
            settings.BASE_DIR / "history/static/history/trace_analytics.css"
        ).read_text()
        script = (
            settings.BASE_DIR / "history/static/history/trace_analytics.js"
        ).read_text()
        detail = (settings.BASE_DIR / "templates/traces/trace_detail.html").read_text()
        shell = (settings.BASE_DIR / "templates/traces/app_shell.html").read_text()

        for selector, size in (
            (r"\.trace-index-row", "14px"),
            (r"\.trace-step-link strong", "14px"),
            (r"\.trace-selected-step h2", "16px"),
            (r"\.trace-payload-scroll pre", "14px"),
            (r"\.trace-timeline-header", "12px"),
        ):
            self.assertRegex(stylesheet, rf"{selector} \{{[^}}]*font-size: {size}")
        self.assertRegex(
            stylesheet,
            r"\.trace-inspector-layout \{[^}]*height: clamp\([^}]*100vh",
        )
        self.assertRegex(
            stylesheet,
            r"\.trace-step-scroll \{[^}]*overflow-y: auto",
        )
        self.assertRegex(
            stylesheet,
            r"\.trace-payload-scroll \{[^}]*overflow: auto",
        )
        self.assertRegex(
            stylesheet,
            r"\.trace-step-detail-view\[hidden\] \{[^}]*display: none",
        )
        self.assertIn("data-trace-step-link", detail)
        self.assertIn("stepFragmentCache", script)
        self.assertIn("history.pushState", script)
        self.assertIn('activateStepLink(initialStepLink.dataset.stepId)', script)
        self.assertIn("stepScroller?.scrollTo", script)
        self.assertNotIn("scrollIntoView", script)
        self.assertIn('window.addEventListener("resize", centerActiveStep)', script)
        self.assertIn("trace_analytics.css' %}?v=20260827-session-inspector-3", shell)
        self.assertIn("trace_analytics.js' %}?v=20260827-session-inspector-3", shell)
        self.assertIn("background: linear-gradient(105deg, #20291a", stylesheet)

    def test_trace_page_background_follows_the_active_theme(self):
        stylesheet = (
            settings.BASE_DIR / "history/static/history/trace_analytics.css"
        ).read_text()

        self.assertRegex(
            stylesheet,
            r"\.analytics-auth-page, \.trace-explorer-page \{[^}]*background: var\(--aa-bg\)",
        )

    def test_agent_routes_do_not_exist(self):
        self.assertEqual(self.client.get("/agent/", **self.request_options).status_code, 404)
        self.assertEqual(
            self.client.get("/agent/accounts/login/", **self.request_options).status_code,
            404,
        )
