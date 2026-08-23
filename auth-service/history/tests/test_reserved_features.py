from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from history.models import HistorySession, ImportBatch, UserMemoryPool


class ReservedFeatureTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reserved-feature-user",
            password="safe-reserved-feature-pass",
        )

    def login(self, client):
        page = client.get(reverse("login"))
        csrf_token = page.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(
            reverse("login"),
            {
                "username": "reserved-feature-user",
                "password": "safe-reserved-feature-pass",
            },
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 302)

    def test_history_synthesis_page_requires_login_and_explains_reserved_pipeline(self):
        url = reverse("history:history-synthesis")

        anonymous = self.client.get(url)
        self.assertRedirects(anonymous, f"{reverse('login')}?next={url}")

        self.login(self.client)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "历史方法总结")
        self.assertContains(response, "Critic model")
        self.assertContains(response, "共性流程与方法")
        self.assertContains(response, "尚未开放")

    def test_history_synthesis_status_api_is_authenticated_and_machine_readable(self):
        url = reverse("history:history-synthesis-status")

        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("login"), anonymous["Location"])

        self.login(self.client)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "schema_version": "v1",
                "feature": "history_synthesis",
                "status": "reserved",
                "available": False,
                "accepting_requests": False,
                "planned_stages": [
                    "candidate_selection",
                    "critic_eligibility_review",
                    "common_process_synthesis",
                    "evidence_linking",
                    "human_review",
                ],
                "create_endpoint": reverse("history:history-synthesis-runs"),
            },
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_history_synthesis_create_endpoint_is_csrf_protected_and_never_writes(self):
        url = reverse("history:history-synthesis-runs")
        csrf_client = Client(enforce_csrf_checks=True)
        self.login(csrf_client)

        rejected = csrf_client.post(url, data={})
        self.assertEqual(rejected.status_code, 403)

        page = csrf_client.get(reverse("history:history-synthesis"))
        csrf_token = page.cookies[settings.CSRF_COOKIE_NAME].value
        response = csrf_client.post(url, data={}, HTTP_X_CSRFTOKEN=csrf_token)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(HistorySession.objects.count(), 0)
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(UserMemoryPool.objects.count(), 0)
        self.assertEqual(
            response.json(),
            {
                "schema_version": "v1",
                "feature": "history_synthesis",
                "error": "feature_not_available",
                "status": "reserved",
                "writes_performed": False,
            },
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_api_credits_page_requires_login_and_explains_planned_providers(self):
        url = reverse("history:api-credits")

        anonymous = self.client.get(url)
        self.assertRedirects(anonymous, f"{reverse('login')}?next={url}")

        self.login(self.client)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API 额度充值")
        self.assertContains(response, "DeepSeek")
        self.assertContains(response, "Qwen")
        self.assertContains(response, "客户端安全激活")
        self.assertContains(response, "尚未开放")
        self.assertNotContains(response, 'name="api_key"')
        self.assertNotContains(response, 'name="payment_token"')

    def test_api_credits_status_api_is_authenticated_and_machine_readable(self):
        url = reverse("history:api-credits-status")

        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("login"), anonymous["Location"])

        self.login(self.client)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "schema_version": "v1",
                "feature": "api_credits",
                "status": "reserved",
                "available": False,
                "accepting_orders": False,
                "planned_providers": ["deepseek", "qwen"],
                "planned_delivery": "desktop_secure_activation",
                "create_endpoint": reverse("history:api-credit-orders"),
            },
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_api_credit_order_endpoint_is_csrf_protected_and_never_writes(self):
        url = reverse("history:api-credit-orders")
        csrf_client = Client(enforce_csrf_checks=True)
        self.login(csrf_client)

        rejected = csrf_client.post(url, data={"provider": "deepseek"})
        self.assertEqual(rejected.status_code, 403)

        page = csrf_client.get(reverse("history:api-credits"))
        csrf_token = page.cookies[settings.CSRF_COOKIE_NAME].value
        response = csrf_client.post(
            url,
            data={"provider": "deepseek"},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(HistorySession.objects.count(), 0)
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(UserMemoryPool.objects.count(), 0)
        self.assertEqual(
            response.json(),
            {
                "schema_version": "v1",
                "feature": "api_credits",
                "error": "feature_not_available",
                "status": "reserved",
                "writes_performed": False,
            },
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_authenticated_navigation_exposes_both_reserved_feature_buttons(self):
        self.login(self.client)

        response = self.client.get(reverse("history:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("history:history-synthesis"))
        self.assertContains(response, reverse("history:api-credits"))
        self.assertContains(response, "历史总结")
        self.assertContains(response, "API 充值")

    def test_reserved_api_method_matrix_is_fail_closed(self):
        self.login(self.client)

        read_only_endpoints = (
            reverse("history:history-synthesis-status"),
            reverse("history:api-credits-status"),
        )
        create_endpoints = (
            reverse("history:history-synthesis-runs"),
            reverse("history:api-credit-orders"),
        )

        for endpoint in read_only_endpoints:
            with self.subTest(endpoint=endpoint, method="POST"):
                self.assertEqual(self.client.post(endpoint).status_code, 405)
        for endpoint in create_endpoints:
            with self.subTest(endpoint=endpoint, method="GET"):
                self.assertEqual(self.client.get(endpoint).status_code, 405)

    def test_expired_absolute_session_cannot_access_reserved_features(self):
        self.login(self.client)
        session = self.client.session
        session["hermes_absolute_session_expires_at"] = (
            timezone.now() - timedelta(seconds=1)
        ).isoformat()
        session.save()

        endpoints = (
            reverse("history:history-synthesis"),
            reverse("history:history-synthesis-status"),
            reverse("history:history-synthesis-runs"),
            reverse("history:api-credits"),
            reverse("history:api-credits-status"),
            reverse("history:api-credit-orders"),
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 302)
