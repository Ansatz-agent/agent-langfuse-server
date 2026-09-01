from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from history.auth_views import ABSOLUTE_EXPIRY_KEY
from history.langfuse_client import (
    LangfuseClient,
    LangfusePayloadTooLarge,
    LangfuseUnavailable,
)
from history.trace_views import aggregate_dashboard


def observation(
    *,
    observation_id: str,
    trace_id: str,
    user_id: str,
    session_id: str = "session-a",
    start_time: str = "2026-08-24T06:00:00Z",
    end_time: str = "2026-08-24T06:00:02Z",
    name: str = "hermes.turn",
    observation_type: str = "SPAN",
    root: bool = True,
    model: str | None = None,
    tokens: int | None = 0,
    cost: float | None = 0,
    input_value=None,
    output_value=None,
    parent_observation_id: str | None = None,
    metadata: dict | None = None,
    level: str = "DEFAULT",
):
    return {
        "id": observation_id,
        "traceId": trace_id,
        "startTime": start_time,
        "endTime": end_time,
        "projectId": "project",
        "parentObservationId": None if root else (parent_observation_id or "root-observation"),
        "type": observation_type,
        "isRootObservation": root,
        "name": name,
        "level": level,
        "statusMessage": None,
        "userId": user_id,
        "sessionId": session_id,
        "input": input_value,
        "output": output_value,
        "metadata": metadata if metadata is not None else {"username": "alice"},
        "providedModelName": model,
        "usageDetails": {"total": tokens} if tokens is not None else {},
        "inputUsage": 0,
        "outputUsage": 0,
        "totalUsage": 0,
        "costDetails": {"total": cost} if cost is not None else {},
        "totalCost": cost if cost is not None else 0,
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
        self.assertIn("limit=100", first_url)
        fields = parse_qs(urlparse(first_url).query)["fields"][0].split(",")
        self.assertNotIn("io", fields)
        self.assertNotIn("metadata", fields)
        self.assertIn("fromStartTime=2026-07-25T00%3A00%3A00Z", first_url)
        self.assertIn("toStartTime=2026-08-24T00%3A00%3A00Z", first_url)
        self.assertIn("cursor=next-page", calls[1][0].full_url)
        self.assertEqual(calls[0][1], 5)
        self.assertTrue(calls[0][0].get_header("Authorization").startswith("Basic "))
        self.assertNotIn("pk-test", first_url)
        self.assertNotIn("sk-test", first_url)

    def test_single_observation_query_scopes_user_trace_and_observation_together(self):
        calls = []

        def transport(request, timeout):
            calls.append(request)
            return 200, json.dumps(
                {
                    "data": [
                        observation(
                            observation_id="step-1",
                            trace_id="trace-1",
                            user_id="7",
                            input_value="question",
                            output_value="answer",
                        )
                    ],
                    "meta": {},
                }
            ).encode()

        item = LangfuseClient(transport=transport).get_observation(
            user_id="7", trace_id="trace-1", observation_id="step-1"
        )

        self.assertEqual(item["id"], "step-1")
        query = parse_qs(urlparse(calls[0].full_url).query)
        self.assertEqual(query["limit"], ["1"])
        self.assertIn("io", query["fields"][0].split(","))
        filters = json.loads(query["filter"][0])
        self.assertEqual(
            {(entry["column"], entry["value"]) for entry in filters},
            {("userId", "7"), ("traceId", "trace-1"), ("id", "step-1")},
        )

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
        self.detail_calls = []

    def list_observations(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return list(self.items)

    def get_observation(self, **kwargs):
        self.detail_calls.append(kwargs)
        if self.error:
            raise self.error
        for item in self.items:
            if (
                str(item.get("userId")) == kwargs["user_id"]
                and str(item.get("traceId")) == kwargs["trace_id"]
                and str(item.get("id")) == kwargs["observation_id"]
            ):
                return dict(item)
        return None


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
                    user_id=self.user.username,
                    input_value="owned input",
                ),
                observation(
                    observation_id="foreign",
                    trace_id="trace-foreign",
                    user_id=self.other.username,
                    input_value="foreign secret",
                ),
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(
                reverse("trace-dashboard"),
                {"days": "30", "userId": self.other.username},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls[0]["user_id"], self.user.username)
        self.assertContains(response, "trace-owned")
        self.assertNotContains(response, "foreign secret")
        self.assertNotContains(response, "trace-foreign")

    def test_dashboard_renders_complete_personal_analytics_inventory(self):
        fake = FakeLangfuseClient(
            [
                observation(
                    observation_id="root",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    input_value="private prompt must not appear",
                    output_value="private response must not appear",
                ),
                observation(
                    observation_id="generation",
                    trace_id="trace-owned",
                    user_id=self.user.username,
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

    def test_missing_usage_renders_unavailable_across_trace_surfaces(self):
        fake = FakeLangfuseClient(
            [
                observation(
                    observation_id="root",
                    trace_id="trace-missing",
                    user_id=self.user.username,
                    session_id="session-missing",
                ),
                observation(
                    observation_id="generation-missing",
                    trace_id="trace-missing",
                    user_id=self.user.username,
                    session_id="session-missing",
                    root=False,
                    observation_type="GENERATION",
                    model="deepseek-v4-flash",
                    tokens=None,
                    cost=None,
                ),
            ]
        )

        for route, args in (
            ("trace-dashboard", []),
            ("trace-model-analytics", []),
            ("trace-index", []),
            ("trace-session-detail", ["session-missing"]),
            ("trace-detail", ["trace-missing"]),
        ):
            with self.subTest(route=route):
                with patch("history.trace_views.get_langfuse_client", return_value=fake):
                    response = self.client.get(reverse(route, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "—")

        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            dashboard = self.client.get(reverse("trace-dashboard"))
            models = self.client.get(reverse("trace-model-analytics"))
            trace = self.client.get(reverse("trace-detail", args=["trace-missing"]))
            selected = self.client.get(
                reverse("trace-detail", args=["trace-missing"]),
                {"step": "generation-missing"},
            )

        self.assertFalse(dashboard.context["metrics"]["has_tokens"])
        self.assertEqual(dashboard.context["metrics"]["tokens_display"], "—")
        self.assertEqual(dashboard.context["metrics"]["cost_display"], "—")
        self.assertEqual(models.context["recent_calls"][0]["tokens_display"], "—")
        self.assertEqual(models.context["recent_calls"][0]["cost_display"], "—")
        self.assertEqual(trace.context["summary"]["tokens_display"], "—")
        self.assertEqual(trace.context["summary"]["cost_display"], "—")
        generation = next(
            item for item in trace.context["observations"] if item["id"] == "generation-missing"
        )
        self.assertEqual(generation["tokens_display"], "—")
        self.assertEqual(generation["cost_display"], "—")
        self.assertContains(selected, "— tokens")

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
            user_id=self.user.username,
            observation_type="GENERATION",
            model="openai/gpt-5.5",
            tokens=120,
            cost=0.012,
        )
        foreign = observation(
            observation_id="foreign-generation",
            trace_id="trace-foreign",
            user_id=self.other.username,
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
                    "userId": self.other.username,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls[0]["user_id"], self.user.username)
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
                    user_id=self.user.username,
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

    def test_trace_index_is_owner_scoped_and_groups_rows_by_session(self):
        fake = FakeLangfuseClient(
            [
                observation(
                    observation_id="owned-root",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    session_id="session-owned",
                    input_value="Summarize the quarterly report",
                    output_value="Revenue increased year over year",
                ),
                observation(
                    observation_id="owned-tool",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    session_id="session-owned",
                    root=False,
                    name="document_search",
                    metadata={"nemo_relay.scope_type": "tool"},
                ),
                observation(
                    observation_id="owned-second-root",
                    trace_id="trace-owned-second",
                    user_id=self.user.username,
                    session_id="session-owned",
                    start_time="2026-08-24T07:00:00Z",
                    end_time="2026-08-24T07:00:03Z",
                    input_value="Follow-up question",
                    output_value="Latest session response",
                ),
                observation(
                    observation_id="unassigned-root",
                    trace_id="trace-unassigned",
                    user_id=self.user.username,
                    session_id=None,
                    start_time="2026-08-23T07:00:00Z",
                    input_value="Unsessioned request",
                    output_value="Unsessioned response",
                ),
                observation(
                    observation_id="foreign-root",
                    trace_id="trace-foreign",
                    user_id=self.other.username,
                    input_value="foreign secret prompt",
                ),
            ]
        )

        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(reverse("trace-index"), {"days": "30"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls[0]["user_id"], self.user.username)
        self.assertFalse(fake.calls[0]["include_io"])
        self.assertEqual(len(response.context["sessions"]), 2)
        owned_session = response.context["sessions"][0]
        self.assertEqual(owned_session["id"], "session-owned")
        self.assertEqual(owned_session["trace_count"], 2)
        self.assertNotIn("latest_response_preview", owned_session)
        self.assertContains(response, "Trace")
        self.assertNotContains(response, "Summarize the quarterly report")
        self.assertNotContains(response, "Latest session response")
        self.assertContains(response, "document_search")
        self.assertContains(response, "Unsessioned traces")
        self.assertContains(
            response, reverse("trace-session-detail", args=["session-owned"])
        )
        self.assertNotContains(response, "foreign secret prompt")
        self.assertNotContains(response, "trace-foreign")

    def test_legacy_trace_runs_route_redirects_to_sessions_with_bounded_query(self):
        response = self.client.get(
            reverse("trace-runs-legacy"),
            {"days": "90", "q": "tool", "userId": self.other.username, "extra": "drop"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/traces/sessions/?days=90&q=tool")

    def test_trace_index_unavailable_is_generic(self):
        fake = FakeLangfuseClient(error=LangfuseUnavailable("private upstream detail"))
        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(reverse("trace-index"))

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Trace 服务暂时不可用", status_code=503)
        self.assertNotContains(response, "private upstream detail", status_code=503)

    def test_trace_detail_uses_content_first_semantic_blocks(self):
        owned = FakeLangfuseClient(
            [
                observation(
                    observation_id="root-observation",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    start_time="2026-08-24T06:00:00Z",
                    end_time="2026-08-24T06:00:12Z",
                    input_value="Explain the result",
                    output_value="The task is complete",
                ),
                observation(
                    observation_id="generation-one",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    root=False,
                    start_time="2026-08-24T06:00:01Z",
                    end_time="2026-08-24T06:00:04Z",
                    name="nvidia",
                    observation_type="GENERATION",
                    model="openai/gpt-5.5",
                    input_value={"messages": [{"role": "user", "content": "Explain the result"}]},
                    output_value={"content": "I will inspect it"},
                ),
                observation(
                    observation_id="tool-one",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    root=False,
                    start_time="2026-08-24T06:00:04Z",
                    end_time="2026-08-24T06:00:07Z",
                    name="terminal",
                    input_value={"command": "echo complete"},
                    output_value={"stdout": "complete", "exit_code": 0},
                    metadata={"nemo_relay.scope_type": "tool"},
                ),
            ]
        )

        with patch("history.trace_views.get_langfuse_client", return_value=owned):
            response = self.client.get(
                reverse("trace-detail", args=["trace-owned"]), {"days": "7"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["days"], 7)
        self.assertFalse(owned.calls[0]["include_io"])
        self.assertEqual(
            owned.detail_calls,
            [
                {
                    "user_id": self.user.username,
                    "trace_id": "trace-owned",
                    "observation_id": "root-observation",
                }
            ],
        )
        self.assertEqual(
            [block["kind"] for block in response.context["content_blocks"]],
            ["user_request", "model_exchange", "tool_exchange", "final_response"],
        )
        self.assertEqual(response.context["selected_step"]["id"], "overview")
        self.assertEqual(
            [step["id"] for step in response.context["steps"]],
            ["overview", "generation-one", "tool-one"],
        )
        self.assertContains(response, "Content")
        self.assertContains(response, "Performance")
        self.assertContains(response, "User request")
        self.assertContains(response, "MODEL RESPONSE")
        self.assertContains(response, "TOOL CALL")
        self.assertContains(response, "Final response")
        self.assertContains(response, 'class="trace-selected-step ', count=1, html=False)
        self.assertNotContains(response, "echo complete")

        with patch("history.trace_views.get_langfuse_client", return_value=owned):
            tool_response = self.client.get(
                reverse("trace-detail", args=["trace-owned"]), {"step": "tool-one"}
            )
        self.assertEqual(tool_response.status_code, 200)
        self.assertEqual(tool_response.context["selected_step"]["id"], "tool-one")
        self.assertEqual(owned.detail_calls[-1]["observation_id"], "tool-one")
        self.assertContains(tool_response, "Tool arguments")
        self.assertContains(tool_response, "Tool result")
        self.assertContains(tool_response, "echo complete")

    def test_trace_step_fragment_is_owner_scoped_selected_and_escaped(self):
        owned = FakeLangfuseClient(
            [
                observation(
                    observation_id="root-observation",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    input_value="request",
                    output_value="response",
                ),
                observation(
                    observation_id="tool-one",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    root=False,
                    name="terminal",
                    input_value={"command": "<script>alert(1)</script>"},
                    output_value={"stdout": "safe"},
                    metadata={"nemo_relay.scope_type": "tool"},
                ),
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=owned):
            response = self.client.get(
                reverse("trace-step-fragment", args=["trace-owned", "tool-one"])
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tool arguments")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertNotContains(response, "analytics-topbar")
        self.assertEqual(owned.calls[0]["user_id"], self.user.username)
        self.assertEqual(owned.calls[0]["trace_id"], "trace-owned")
        self.assertEqual(len(owned.detail_calls), 1)
        self.assertEqual(owned.detail_calls[0]["observation_id"], "tool-one")

        foreign = FakeLangfuseClient(
            [
                observation(
                    observation_id="foreign-tool",
                    trace_id="trace-foreign",
                    user_id=self.other.username,
                )
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=foreign):
            denied = self.client.get(
                reverse("trace-step-fragment", args=["trace-foreign", "foreign-tool"])
            )
        self.assertEqual(denied.status_code, 404)

    def test_long_trace_renders_only_the_selected_payload(self):
        items = [
            observation(
                observation_id="root-observation",
                trace_id="trace-long",
                user_id=self.user.username,
                input_value="root request",
                output_value="root response",
                end_time="2026-08-24T06:02:00Z",
            )
        ]
        for index in range(1, 51):
            items.append(
                observation(
                    observation_id=f"step-{index}",
                    trace_id="trace-long",
                    user_id=self.user.username,
                    root=False,
                    name=f"model-step-{index}",
                    observation_type="GENERATION",
                    model="openai/gpt-5.5",
                    input_value={"content": f"private-input-{index}"},
                    output_value={"content": f"private-output-{index}"},
                    parent_observation_id="root-observation",
                )
            )
        fake = FakeLangfuseClient(items)

        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            overview = self.client.get(reverse("trace-detail", args=["trace-long"]))
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(len(overview.context["steps"]), 51)
        self.assertFalse(fake.calls[0]["include_io"])
        self.assertEqual(
            [call["observation_id"] for call in fake.detail_calls],
            ["root-observation"],
        )
        self.assertContains(overview, "root request")
        self.assertNotContains(overview, "private-input-40")
        self.assertNotContains(overview, "private-output-40")

        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            selected = self.client.get(
                reverse("trace-detail", args=["trace-long"]), {"step": "step-40"}
            )
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.context["selected_step"]["id"], "step-40")
        self.assertEqual(fake.detail_calls[-1]["observation_id"], "step-40")
        self.assertContains(selected, "private-output-40")
        self.assertNotContains(selected, "private-output-39")
        self.assertNotContains(selected, "private-output-41")

        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            invalid = self.client.get(
                reverse("trace-detail", args=["trace-long"]), {"step": "not-owned"}
            )
        self.assertEqual(invalid.context["selected_step"]["id"], "overview")
        self.assertNotIn(
            "not-owned", [call["observation_id"] for call in fake.detail_calls]
        )

    def test_trace_detail_is_owner_only_and_escapes_full_payload(self):
        owned = FakeLangfuseClient(
            [
                observation(
                    observation_id="root",
                    trace_id="trace-owned",
                    user_id=self.user.username,
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
        self.assertEqual(owned.calls[0]["user_id"], self.user.username)
        self.assertEqual(owned.calls[0]["trace_id"], "trace-owned")

        foreign = FakeLangfuseClient(
            [
                observation(
                    observation_id="foreign",
                    trace_id="trace-foreign",
                    user_id=self.other.username,
                )
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=foreign):
            denied = self.client.get(reverse("trace-detail", args=["trace-foreign"]))
        self.assertEqual(denied.status_code, 404)

    def test_oversized_selected_payload_keeps_trace_shell_available(self):
        class OversizedPayloadClient(FakeLangfuseClient):
            def get_observation(self, **kwargs):
                self.detail_calls.append(kwargs)
                raise LangfusePayloadTooLarge("private size detail")

        fake = OversizedPayloadClient(
            [
                observation(
                    observation_id="root",
                    trace_id="trace-large-payload",
                    user_id=self.user.username,
                )
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=fake):
            response = self.client.get(
                reverse("trace-detail", args=["trace-large-payload"])
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selected content is too large to display.")
        self.assertContains(response, "Steps")
        self.assertNotContains(response, "private size detail")
        self.assertEqual(fake.detail_calls[0]["observation_id"], "root")

    def test_trace_detail_uses_light_shell_and_content_first_execution_views(self):
        owned = FakeLangfuseClient(
            [
                observation(
                    observation_id="root-observation",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    start_time="2026-08-24T06:00:00Z",
                    end_time="2026-08-24T06:00:12Z",
                    input_value="Explain the result",
                    output_value="The task is complete",
                ),
                observation(
                    observation_id="generation-one",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    root=False,
                    start_time="2026-08-24T06:00:01Z",
                    end_time="2026-08-24T06:00:04Z",
                    name="nvidia",
                    observation_type="GENERATION",
                    model="openai/gpt-5.5",
                    tokens=120,
                    cost=0.012,
                    input_value={"messages": [{"role": "user", "content": "Explain the result"}]},
                    output_value={"content": "I will inspect it"},
                    metadata={"nemo_relay.scope_type": "llm", "provider": "nvidia"},
                ),
                observation(
                    observation_id="tool-one",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    root=False,
                    start_time="2026-08-24T06:00:04Z",
                    end_time="2026-08-24T06:00:07Z",
                    name="terminal",
                    input_value={"command": "echo complete"},
                    output_value={"stdout": "complete", "exit_code": 0},
                    metadata={"nemo_relay.scope_type": "tool", "tool_call_id": "call-1"},
                ),
                observation(
                    observation_id="generation-two",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    root=False,
                    start_time="2026-08-24T06:00:07Z",
                    end_time="2026-08-24T06:00:12Z",
                    name="nvidia",
                    observation_type="GENERATION",
                    model="openai/gpt-5.5",
                    tokens=80,
                    cost=0.008,
                    output_value={"content": "The task is complete"},
                    metadata={"nemo_relay.scope_type": "llm", "provider": "nvidia"},
                    level="ERROR",
                ),
            ]
        )

        with patch("history.trace_views.get_langfuse_client", return_value=owned):
            response = self.client.get(reverse("trace-detail", args=["trace-owned"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/auth/static/history/trace_analytics.css")
        self.assertNotContains(response, "/auth/static/history/app.css")
        self.assertContains(response, 'class="analytics-topbar"', html=False)
        for label in (
            "Duration · 12.00s",
            "Steps",
            "LLM calls",
            "Tool calls",
            "Tokens",
            "Cost",
            "Content",
            "Performance",
            "Raw",
            "Execution Timeline",
            "terminal",
            "3.00s",
        ):
            self.assertContains(response, label)
        self.assertEqual(response.context["summary"]["llm_calls"], 2)
        self.assertEqual(response.context["summary"]["tool_calls"], 1)
        self.assertEqual(response.context["summary"]["errors"], 1)
        self.assertEqual(response.context["summary"]["duration_display"], "12.00s")
        self.assertEqual(response.context["observations"][2]["offset_percent"], 33.333)
        self.assertEqual(response.context["observations"][2]["width_percent"], 25.0)

    def test_session_detail_is_a_compact_trace_list_without_full_payloads(self):
        owned = FakeLangfuseClient(
            [
                observation(
                    observation_id="root",
                    trace_id="trace-owned",
                    user_id=self.user.username,
                    session_id="session-owned",
                    start_time="2026-08-24T06:00:00Z",
                    end_time="2026-08-24T06:00:09Z",
                    input_value="question",
                    output_value="answer",
                ),
                observation(
                    observation_id="root-second",
                    trace_id="trace-owned-second",
                    user_id=self.user.username,
                    session_id="session-owned",
                    start_time="2026-08-24T07:00:00Z",
                    end_time="2026-08-24T07:00:03Z",
                    input_value="follow-up",
                    output_value="second answer",
                ),
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=owned):
            response = self.client.get(
                reverse("trace-session-detail", args=["session-owned"])
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/auth/static/history/trace_analytics.css")
        self.assertNotContains(response, "/auth/static/history/app.css")
        self.assertContains(response, "Session traces")
        self.assertContains(response, "All sessions")
        self.assertContains(response, "9.00s")
        self.assertNotContains(response, "question")
        self.assertNotContains(response, "second answer")
        self.assertContains(response, "Inspect trace", count=2)
        self.assertNotContains(response, "<pre", html=False)
        self.assertFalse(owned.calls[0]["include_io"])
        self.assertEqual(owned.detail_calls, [])
        self.assertEqual(
            [trace["id"] for trace in response.context["traces"]],
            ["trace-owned", "trace-owned-second"],
        )

    def test_unsessioned_bucket_opens_only_observations_without_session_id(self):
        owned = FakeLangfuseClient(
            [
                observation(
                    observation_id="unassigned",
                    trace_id="trace-unassigned",
                    user_id=self.user.username,
                    session_id=None,
                    input_value="unassigned content",
                ),
                observation(
                    observation_id="assigned",
                    trace_id="trace-assigned",
                    user_id=self.user.username,
                    session_id="session-owned",
                    input_value="assigned secret",
                ),
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=owned):
            response = self.client.get(
                reverse("trace-session-detail", args=["__unsessioned__"])
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "unassigned content")
        self.assertNotContains(response, "assigned secret")

    def test_large_session_uses_only_one_lightweight_collection_query(self):
        owned = FakeLangfuseClient(
            [
                observation(
                    observation_id=f"step-{index}",
                    trace_id=f"trace-{index // 10}",
                    user_id=self.user.username,
                    session_id="large-session",
                    root=index % 10 == 0,
                    input_value=f"large-input-{index}",
                    output_value=f"large-output-{index}",
                )
                for index in range(843)
            ]
        )
        with patch("history.trace_views.get_langfuse_client", return_value=owned):
            response = self.client.get(
                reverse("trace-session-detail", args=["large-session"])
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(owned.calls), 1)
        self.assertFalse(owned.calls[0]["include_io"])
        self.assertEqual(owned.detail_calls, [])
        self.assertNotContains(response, "large-input-0")

    def test_session_detail_is_owner_only(self):
        foreign = FakeLangfuseClient(
            [
                observation(
                    observation_id="foreign",
                    trace_id="trace-foreign",
                    user_id=self.other.username,
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
