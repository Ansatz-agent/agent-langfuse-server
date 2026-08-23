import json
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from history.models import HistoryMessage, HistorySession


class HistoryAccessControlTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(username="alice", password="safe-test-pass-1")
        self.bob = user_model.objects.create_user(username="bob", password="safe-test-pass-2")
        self.admin = user_model.objects.create_superuser(
            username="owner", password="safe-test-pass-3", email="owner@example.test"
        )
        self.alice_session = HistorySession.objects.create(
            owner=self.alice,
            uploader=self.admin,
            external_id="alice-session",
            title="Alice private project",
            source="cli",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.bob_session = HistorySession.objects.create(
            owner=self.bob,
            uploader=self.admin,
            external_id="bob-session",
            title="Bob private project",
            source="cli",
            started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        HistoryMessage.objects.create(
            session=self.alice_session,
            source_message_id="1",
            role="user",
            content="alice-unique-secret-topic",
            timestamp=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
        HistoryMessage.objects.create(
            session=self.bob_session,
            source_message_id="1",
            role="user",
            content="bob-unique-secret-topic",
            timestamp=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
        )

    def login_as(self, username: str, password: str) -> None:
        response = self.client.post(
            reverse("login"),
            {"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 302)

    def test_anonymous_history_endpoints_require_login(self):
        endpoints = [
            reverse("history:session-list"),
            reverse("history:session-detail", args=[self.alice_session.pk]),
            reverse("history:usage-dashboard"),
            reverse("history:session-import"),
            reverse("history:session-export"),
        ]

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response["Location"])

    def test_normal_user_sees_only_owned_sessions(self):
        self.login_as("alice", "safe-test-pass-1")

        response = self.client.get(reverse("history:session-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice private project")
        self.assertNotContains(response, "Bob private project")
        self.assertEqual(list(response.context["sessions"]), [self.alice_session])

    def test_history_list_uses_dashboard_workspace_layout_and_keeps_controls(self):
        self.login_as("alice", "safe-test-pass-1")

        response = self.client.get(reverse("history:session-list"), {"q": "Alice"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="dashboard-shell history-dashboard-shell"')
        self.assertContains(response, 'aria-label="仪表盘导航"')
        self.assertContains(response, 'class="is-active" href="/history/"')
        self.assertContains(response, "Owner 隔离已启用")
        self.assertContains(response, "会话历史")
        self.assertContains(response, "1 个主会话")
        self.assertContains(response, 'class="history-dashboard-search"')
        self.assertContains(response, 'type="checkbox" name="uploader"')
        self.assertContains(response, "Alice private project")

    def test_normal_user_cannot_open_another_users_session(self):
        self.login_as("alice", "safe-test-pass-1")

        response = self.client.get(reverse("history:session-detail", args=[self.bob_session.pk]))

        self.assertEqual(response.status_code, 404)

    def test_search_is_owner_scoped(self):
        self.login_as("alice", "safe-test-pass-1")

        own_response = self.client.get(
            reverse("history:session-list"), {"q": "alice-unique-secret-topic"}
        )
        foreign_response = self.client.get(
            reverse("history:session-list"), {"q": "bob-unique-secret-topic"}
        )

        self.assertContains(own_response, "Alice private project")
        self.assertNotContains(foreign_response, "Bob private project")
        self.assertEqual(foreign_response.context["sessions"].count(), 0)

    def test_normal_user_export_contains_only_owned_sessions(self):
        self.login_as("alice", "safe-test-pass-1")

        response = self.client.get(reverse("history:session-export"))

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode()
        rows = [json.loads(line) for line in body.splitlines() if line]
        self.assertEqual([row["id"] for row in rows], ["alice-session"])
        self.assertNotIn("bob-unique-secret-topic", body)

    def test_superuser_can_see_all_sessions(self):
        self.login_as("owner", "safe-test-pass-3")

        response = self.client.get(reverse("history:session-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice private project")
        self.assertContains(response, "Bob private project")
        self.assertEqual(response.context["sessions"].count(), 2)
