from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from history.models import HistoryMessage, HistorySession


class DashboardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(
            username="dashboard-alice",
            password="safe-dashboard-pass-1",
        )
        self.bob = user_model.objects.create_user(
            username="dashboard-bob",
            password="safe-dashboard-pass-2",
        )
        self.alice_session = HistorySession.objects.create(
            owner=self.alice,
            uploader=self.alice,
            external_id="alice-session",
            title="Alice deployment review",
            source="hermes",
            model="test-model",
            started_at=timezone.now() - timedelta(hours=2),
            message_count=12,
            tool_call_count=4,
        )
        self.bob_session = HistorySession.objects.create(
            owner=self.bob,
            uploader=self.bob,
            external_id="bob-session",
            title="Bob private session",
            source="hermes",
            model="test-model",
            started_at=timezone.now() - timedelta(hours=1),
            message_count=99,
            tool_call_count=20,
        )
        HistoryMessage.objects.create(
            session=self.alice_session,
            source_message_id="alice-memory",
            role="assistant",
            tool_calls=[
                {
                    "function": {
                        "name": "memory",
                        "arguments": '{"action":"add","content":"Alice fact"}',
                    }
                }
            ],
        )

    def login_as_alice(self):
        response = self.client.post(
            reverse("login"),
            {"username": "dashboard-alice", "password": "safe-dashboard-pass-1"},
        )
        self.assertEqual(response.status_code, 302)

    def test_anonymous_dashboard_redirects_to_login(self):
        response = self.client.get(reverse("history:dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('history:dashboard')}",
        )

    def test_dashboard_is_the_authenticated_welcome_page(self):
        self.login_as_alice()

        response = self.client.get(reverse("history:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "概览")
        self.assertContains(response, "欢迎回来，dashboard-alice")
        self.assertContains(response, "最近会话")
        self.assertContains(response, "Alice deployment review")
        self.assertContains(response, "查看全部历史")
        self.assertNotContains(response, "Bob private session")

    def test_dashboard_exposes_owner_scoped_summary_metrics(self):
        self.login_as_alice()

        response = self.client.get(reverse("history:dashboard"))

        self.assertEqual(response.context["dashboard_stats"]["sessions"], 1)
        self.assertEqual(response.context["dashboard_stats"]["messages"], 12)
        self.assertEqual(response.context["dashboard_stats"]["tool_calls"], 4)
        self.assertEqual(response.context["dashboard_stats"]["memory_calls"], 1)

    def test_login_redirects_to_personal_traces(self):
        response = self.client.post(
            reverse("login"),
            {"username": "dashboard-alice", "password": "safe-dashboard-pass-1"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("trace-dashboard"))
