import json
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from history.models import HistoryMessage, HistorySession


class SearchAndExportTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="alice", password="safe-test-pass-1")
        self.other = user_model.objects.create_user(username="bob", password="safe-test-pass-2")
        self.admin = user_model.objects.create_superuser(
            username="owner", password="safe-test-pass-3", email="owner@example.test"
        )

    def login_as(self, username, password):
        response = self.client.post(
            reverse("login"),
            {"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 302)

    def make_session(self, owner, external_id, content="content", title="title"):
        session = HistorySession.objects.create(
            owner=owner,
            uploader=self.admin,
            external_id=external_id,
            title=title,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        HistoryMessage.objects.create(
            session=session,
            source_message_id="1",
            role="assistant",
            content=content,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            tool_name="terminal",
            tool_calls=[{"name": "echo", "arguments": {"x": 1}}],
        )
        return session

    def test_untrusted_message_content_is_escaped_in_html(self):
        session = self.make_session(self.user, "xss-session", content="<script>alert('x')</script>")
        self.login_as("alice", "safe-test-pass-1")

        response = self.client.get(reverse("history:session-detail", args=[session.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;", html=False
        )
        self.assertNotContains(response, "<script>alert('x')</script>", html=False)

    def test_list_is_paginated_and_preserves_query(self):
        for index in range(30):
            self.make_session(self.user, f"session-{index:02d}", title=f"Title {index:02d}")
        self.login_as("alice", "safe-test-pass-1")

        response = self.client.get(reverse("history:session-list"), {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)
        self.assertEqual(response.context["sessions"].count(), 5)

    def test_superuser_export_contains_all_owners(self):
        self.make_session(self.user, "alice-session", content="alice-content")
        self.make_session(self.other, "bob-session", content="bob-content")
        self.login_as("owner", "safe-test-pass-3")

        response = self.client.get(reverse("history:session-export"))
        body = b"".join(response.streaming_content).decode()
        rows = [json.loads(line) for line in body.splitlines() if line]

        self.assertEqual({row["id"] for row in rows}, {"alice-session", "bob-session"})
        self.assertEqual(response["Content-Type"], "application/x-ndjson")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_search_matches_message_content(self):
        self.make_session(self.user, "search-session", content="唯一的中文搜索词")
        self.login_as("alice", "safe-test-pass-1")

        response = self.client.get(reverse("history:session-list"), {"q": "中文搜索词"})

        self.assertEqual(response.context["sessions"].count(), 1)
        self.assertEqual(response.context["sessions"].first().external_id, "search-session")
