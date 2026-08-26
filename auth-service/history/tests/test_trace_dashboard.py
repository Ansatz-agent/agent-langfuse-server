from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from history.auth_views import ABSOLUTE_EXPIRY_KEY
from history.langfuse_client import LangfuseClient, LangfuseUnavailable
from history.trace_views import aggregate_dashboard


def observation(
    *,
    observation_id: str,
    trace_id: str,
    user_id: str,
    session_id: str = "session-a",
    start_time: str = "2026-08-24T06:00:00Z",
    name: str = "hermes.turn",
    observation_type: str = "SPAN",
    root: bool = True,
    model: str | None = None,
    tokens: int = 0,
    cost: float = 0,
    input_value=None,
    output_value=None,
):
    return {
        "id": observation_id,
        "traceId": trace_id,
        "startTime": start_time,
        "endTime": "2026-08-24T06:00:02Z",
        "projectId": "project",
        "parentObservationId": None if root else "root-observation",
        "type": observation_type,
        "isRootObservation": root,
        "name": name,
        "level": "DEFAULT",
        "statusMessage": None,
        "userId": user_id,
        "sessionId": session_id,
        "input": input_value,
        "output": output_value,
        "metadata": {"username": "alice"},
        "providedModelName": model,
        "usageDetails": {"total": tokens} if tokens else {},
        "costDetails": {"total": cost} if cost else {},
        "totalCost": cost,
        "traceName": "Ansatz conversation",
        "tags": ["desktop"],
        "release": "0.17.0",
        "modelId": None,
        "inputPrice": None,
        "outputPrice": None,
        "totalPrice": None,
    }


@override_settings(
    LANGFUSE_INTERNAL_BASE_URL="http://langfuse-web:3000/langfuse/api/public",
    LANGFUSE_PROJECT_PUBLIC_KEY="pk-test",
    LANGFUSE_PROJECT_SECRET_KEY="sk-test",
)
class LangfuseClientTests(TestCase):
    def test_v2_query_is_owner_scoped_bounded_and_cursor_paginated(self):
        calls = []
        pages = [
            {
                "data": [observation(observation_id="one", trace_id="trace-1", user_id="7")],
                "meta": {"cursor": "next-page"},
            },
            {
                "data": [observation(observation_id="two", trace_id="trace-2", user_id="7")],
                "meta": {},
            },
        ]

        def transport(request, timeout):
            calls.append((request, timeout))
            return 200, json.dumps(pages[len(calls) - 1]).encode()

        client = LangfuseClient(transport=transport)
        result = client.list_observations(
            user_id="7",
            from_time="2026-07-25T00:00:00Z",
            to_time="2026-08-24T00:00:00Z",
            session_id="session-a",
        )

        self.assertEqual([item["id"] for item in result], ["one", "two"])
        self.assertEqual(len(calls), 2)
        first_url = calls[0][0].full_url
        self.assertIn("/api/public/v2/observations?", first_url)
        self.assertIn("userId=7", first_url)
        self.assertIn("sessionId=session-a", first_url)
        self.assertIn("limit=1000", first_url)
        self.assertIn("fromStartTime=2026-07-25T00%3A00%3A00Z", first_url)
        self.assertIn("toStartTime=2026-08-24T00%3A00%3A00Z", first_url)
        self.assertIn("cursor=next-page", calls[1][0].full_url)
        self.assertEqual(calls[0][1], 5)
        self.assertTrue(calls[0][0].get_header("Authorization").startswith("Basic "))
        self.assertNotIn("pk-test", first_url)
        self.assertNotIn("sk-test", first_url)

    def test_bad_status_invalid_shape_and_page_overflow_are_unavailable(self):
        for label, transport in (
            ("status", lambda request, timeout: (503, b'{}')),
            ("shape", lambda request, timeout: (200, b'{"data":{},"meta":{}}')),
        ):
            with self.subTest(label=label):
                with self.assertRaises(LangfuseUnavailable):
                    LangfuseClient(transport=transport).list_observations(
                        user_id="7",
                        from_time="2026-07-25T00:00:00Z",
                        to_time="2026-08-24T00:00:00Z",
                    )


class DashboardAggregationTests(TestCase):
    def test_metrics_group_sessions_traces_days_models_tokens_and_cost(self):
        items = [
            observation(
                observation_id="root-a",
                trace_id="trace-a",
                user_id="7",
                input_value="完整问题",
                output_value="完整回答",
            ),
            observation(
                observation_id="generation-a",
                trace_id="trace-a",
                user_id="7",
                root=False,
                name="openai",
                observation_type="GENERATION",
                model="gpt-5.5",
                tokens=120,
                cost=0.0125,
            ),
            observation(
                observation_id="generation-b",
                trace_id="trace-b",
                user_id="7",
                session_id="session-b",
                start_time="2026-08-23T06:00:00Z",
                root=False,
                name="openai",
                observation_type="GENERATION",
                model="gpt-5.5",
                tokens=80,
                cost=0.0075,
            ),
        ]

        dashboard = aggregate_dashboard(items, days=30, query="")

        self.assertEqual(dashboard["metrics"]["sessions"], 2)
        self.assertEqual(dashboard["metrics"]["traces"], 2)
        self.assertEqual(dashboard["metrics"]["tokens"], 200)
        self.assertEqual(dashboard["metrics"]["cost"], 0.02)
        self.assertEqual(dashboard["metrics"]["active_days"], 2)
        self.assertEqual(dashboard["models"][0]["name"], "gpt-5.5")
        self.assertEqual(dashboard["models"][0]["tokens"], 200)
        self.assertEqual(dashboard["sessions"][0]["trace_count"], 1)


class FakeLangfuseClient:
    def __init__(self, items=None, error=None):
        self.items = list(items or [])
        self.error = error
        self.calls = []

    def list_observations(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return list(self.items)


@override_settings(HERMES_SESSION_ABSOLUTE_AGE_SECONDS=3600)
class TraceDashboardViewTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"
        self.user = get_user_model().objects.create_user(
            username="alice", password="safe-test-pass-1"
        )
        self.other = get_user_model().objects.create_user(
            username="bob", password="safe-test-pass-2"
        )
        self.client.force_login(self.user)
        session = self.client.session
        session[ABSOLUTE_EXPIRY_KEY] = (timezone.now() + timedelta(hours=1)).isoformat()
        session.save()

    def test_dashboard_ignores_browser_user_id_and_filters_foreign_rows(self):
        fake = FakeLangfuseClient(
            [
                observation(
                    observation_id="owned",
                    trace_id="trace-owned",
                    user_id=str(self.user.pk),
                    input_value="owned input",
                ),
                observation(
                    observation_id="foreign",
                    trace_id="trace-foreign",
                    user_id=str(self.other.pk),
                    input_value="foreign secret",
                ),
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(
                reverse("trace-dashboard"),
                {"days": "30", "userId": str(self.other.pk)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls[0]["user_id"], str(self.user.pk))
        self.assertContains(response, "trace-owned")
        self.assertNotContains(response, "foreign secret")
        self.assertNotContains(response, "trace-foreign")

    def test_dashboard_renders_complete_personal_analytics_inventory(self):
        fake = FakeLangfuseClient(
            [
                observation(
                    observation_id="root",
                    trace_id="trace-owned",
                    user_id=str(self.user.pk),
                    input_value="private prompt must not appear",
                    output_value="private response must not appear",
                ),
                observation(
                    observation_id="generation",
                    trace_id="trace-owned",
                    user_id=str(self.user.pk),
                    root=False,
                    observation_type="GENERATION",
                    model="openai/gpt-5.5",
                    tokens=120,
                    cost=0.012,
                ),
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(reverse("trace-dashboard"))

        self.assertEqual(response.status_code, 200)
        for label in (
            "Ansatz Analytics",
            "Dashboard",
            "Model Analytics",
            "Total Cost",
            "Daily Avg",
            "Tokens",
            "Active Days",
            "Usage Trend",
            "Cost Mix",
            "Token Mix",
            "Daily Activity",
            "Top Models",
            "Recent Sessions",
        ):
            self.assertContains(response, label)
        self.assertContains(response, "openai/gpt-5.5")
        self.assertContains(response, "trace-owned")
        self.assertContains(response, "<svg", html=False)
        self.assertContains(response, "<progress", html=False)
        self.assertNotContains(response, " style=", html=False)
        self.assertNotContains(response, "private prompt must not appear")
        self.assertNotContains(response, "private response must not appear")

    def test_empty_dashboard_retains_shell_and_upload_guidance(self):
        with patch(
            "history.trace_views.get_langfuse_client", return_value=FakeLangfuseClient([])
        ):
            response = self.client.get(reverse("trace-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ansatz Analytics")
        self.assertContains(response, "No uploaded usage yet")

    def test_days_are_bounded_and_unavailable_is_generic(self):
        fake = FakeLangfuseClient(error=LangfuseUnavailable("secret backend detail"))
        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(reverse("trace-dashboard"), {"days": "3650"})

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Trace 服务暂时不可用", status_code=503)
        self.assertNotContains(response, "secret backend detail", status_code=503)
        self.assertEqual(fake.calls[0]["days"], 30)

    def test_model_analytics_is_owner_scoped_and_bounds_view_state(self):
        owned = observation(
            observation_id="owned-generation",
            trace_id="trace-owned",
            user_id=str(self.user.pk),
            observation_type="GENERATION",
            model="openai/gpt-5.5",
            tokens=120,
            cost=0.012,
        )
        foreign = observation(
            observation_id="foreign-generation",
            trace_id="trace-foreign",
            user_id=str(self.other.pk),
            observation_type="GENERATION",
            model="foreign-secret-model",
            tokens=999,
            cost=99,
        )
        fake = FakeLangfuseClient([owned, foreign])

        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(
                reverse("trace-model-analytics"),
                {
                    "days": "3650",
                    "metric": "secret",
                    "granularity": "quarter",
                    "chart": "pie",
                    "model": "foreign-secret-model",
                    "userId": str(self.other.pk),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls[0]["user_id"], str(self.user.pk))
        self.assertEqual(fake.calls[0]["days"], 30)
        self.assertFalse(fake.calls[0]["include_io"])
        self.assertContains(response, "Model Analytics")
        self.assertContains(response, "openai/gpt-5.5")
        self.assertNotContains(response, "foreign-secret-model")
        self.assertNotContains(response, " style=", html=False)
        for label in (
            "Model distribution",
            "Token composition",
            "Cache split by model",
            "Effective-cost scatterplot",
            "What changed",
            "Model breakdown",
            "Recent Model Calls",
        ):
            self.assertContains(response, label)
        self.assertEqual(response.context["metric"], "cost")
        self.assertEqual(response.context["granularity"], "week")
        self.assertEqual(response.context["chart"], "distribution")

    def test_model_analytics_chart_mode_changes_to_time_series(self):
        fake = FakeLangfuseClient(
            [
                observation(
                    observation_id="generation",
                    trace_id="trace-owned",
                    user_id=str(self.user.pk),
                    observation_type="GENERATION",
                    model="openai/gpt-5.5",
                    tokens=120,
                    cost=0.012,
                )
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(
                reverse("trace-model-analytics"),
                {"chart": "bar", "granularity": "day"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Model usage over time")
        self.assertContains(response, 'aria-label="Model usage trend"', html=False)
        self.assertEqual(response.context["chart"], "bar")

    def test_trace_detail_is_owner_only_and_escapes_full_payload(self):
        owned = FakeLangfuseClient(
            [
                observation(
                    observation_id="root",
                    trace_id="trace-owned",
                    user_id=str(self.user.pk),
                    input_value="<script>alert(1)</script>",
                    output_value={"answer": "完整回答"},
                )
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=owned):
            response = self.client.get(reverse("trace-detail", args=["trace-owned"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "完整回答")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertEqual(owned.calls[0]["user_id"], str(self.user.pk))
        self.assertEqual(owned.calls[0]["trace_id"], "trace-owned")

        foreign = FakeLangfuseClient(
            [
                observation(
                    observation_id="foreign",
                    trace_id="trace-foreign",
                    user_id=str(self.other.pk),
                )
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=foreign):
            denied = self.client.get(reverse("trace-detail", args=["trace-foreign"]))
        self.assertEqual(denied.status_code, 404)

    def test_session_detail_is_owner_only(self):
        foreign = FakeLangfuseClient(
            [
                observation(
                    observation_id="foreign",
                    trace_id="trace-foreign",
                    user_id=str(self.other.pk),
                    session_id="foreign-session",
                )
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=foreign):
            response = self.client.get(
                reverse("trace-session-detail", args=["foreign-session"])
            )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_and_expired_sessions_redirect_to_auth(self):
        self.client.logout()
        response = self.client.get(reverse("trace-dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/auth/login/?next="))

        self.client.force_login(self.user)
        response = self.client.get(reverse("trace-dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/auth/login/?next="))
