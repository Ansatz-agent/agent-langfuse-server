from __future__ import annotations

from datetime import datetime, timezone

from django.test import SimpleTestCase

from history.trace_analytics import build_dashboard, build_model_analytics


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)


def observation(
    observation_id: str,
    *,
    observation_type: str = "GENERATION",
    name: str = "provider",
    model: str | None = "openai/gpt-5.5",
    started: str = "2026-08-25T10:00:00Z",
    ended: str = "2026-08-25T10:00:02Z",
    usage: dict | None = None,
    total_cost=...,
    level: str = "DEFAULT",
    session_id: str = "session-a",
    trace_id: str = "trace-a",
):
    item = {
        "id": observation_id,
        "type": observation_type,
        "name": name,
        "providedModelName": model,
        "startTime": started,
        "endTime": ended,
        "usageDetails": usage or {},
        "costDetails": {},
        "level": level,
        "sessionId": session_id,
        "traceId": trace_id,
        "traceName": "Ansatz conversation",
        "userId": "7",
    }
    if total_cost is not ...:
        item["totalCost"] = total_cost
    return item


class TraceAnalyticsTests(SimpleTestCase):
    def setUp(self):
        self.items = [
            observation(
                "root",
                observation_type="SPAN",
                name="hermes.turn",
                model=None,
                usage={"total": 1000},
                total_cost=5,
            ),
            observation(
                "generation-a",
                model="openai/openai/gpt-5.5",
                usage={
                    "input": 100,
                    "input_cached_tokens": 60,
                    "output": 20,
                    "output_reasoning_tokens": 5,
                    "total": 120,
                },
                total_cost=0.12,
            ),
            observation(
                "generation-b",
                model="openai/gpt-5.5",
                started="2026-08-24T10:00:00Z",
                ended="2026-08-24T10:00:04Z",
                usage={"input": 50, "output": 10, "total": 60},
                total_cost=...,
                session_id="session-b",
                trace_id="trace-b",
            ),
            observation(
                "generation-c",
                name="custom",
                model="deepseek-v4-flash",
                started="2026-08-24T11:00:00Z",
                ended="2026-08-24T11:00:03Z",
                usage={"input": 30, "output": 10, "total": 40},
                total_cost=0,
                level="ERROR",
                session_id="session-b",
                trace_id="trace-c",
            ),
        ]

    def test_dashboard_uses_generation_usage_without_double_counting(self):
        result = build_dashboard(
            self.items,
            days=30,
            now=NOW,
            query="",
            metric="cost",
            granularity="day",
            chart="bar",
        )

        self.assertEqual(result["metrics"]["sessions"], 2)
        self.assertEqual(result["metrics"]["traces"], 3)
        self.assertEqual(result["metrics"]["tokens"], 220)
        self.assertEqual(result["metrics"]["cost"], 0.12)
        self.assertTrue(result["metrics"]["has_cost"])
        self.assertEqual(result["metrics"]["active_days"], 2)
        self.assertEqual(result["metrics"]["errors"], 1)
        self.assertEqual(result["token_mix"]["input"], 180)
        self.assertEqual(result["token_mix"]["cached_input"], 60)
        self.assertEqual(result["token_mix"]["output"], 40)
        self.assertEqual(result["token_mix"]["reasoning_output"], 5)
        self.assertEqual(result["token_mix"]["cache_hit_rate"], 60 / 180)
        self.assertEqual(result["models"][0]["name"], "openai/gpt-5.5")
        self.assertEqual(result["models"][0]["tokens"], 180)
        self.assertEqual(result["models"][0]["cost"], 0.12)
        self.assertEqual(result["models"][0]["priced_calls"], 1)
        self.assertEqual(result["models"][1]["cost"], 0.0)
        self.assertEqual(result["models"][1]["priced_calls"], 1)

    def test_model_analytics_preserves_missing_price_and_latency_evidence(self):
        result = build_model_analytics(
            self.items,
            days=30,
            now=NOW,
            model="all",
            metric="cost",
            granularity="day",
            chart="distribution",
        )

        gpt = result["models"][0]
        deepseek = result["models"][1]
        self.assertEqual(gpt["name"], "openai/gpt-5.5")
        self.assertEqual(gpt["calls"], 2)
        self.assertEqual(gpt["avg_latency"], 3.0)
        self.assertEqual(gpt["p95_latency"], 4.0)
        self.assertIsNone(gpt["unit_cost"])
        self.assertEqual(deepseek["unit_cost"], 0.0)
        self.assertEqual(deepseek["errors"], 1)
        self.assertEqual(len(result["recent_calls"]), 3)

    def test_dashboard_cost_ring_includes_long_tail_models(self):
        items = [
            observation(
                f"generation-{index}",
                model=f"model-{index}",
                total_cost=index,
            )
            for index in range(1, 7)
        ]

        result = build_dashboard(
            items,
            days=30,
            now=NOW,
            query="",
            metric="cost",
            granularity="day",
            chart="bar",
        )

        self.assertEqual(len(result["cost_mix_segments"]), 6)

    def test_empty_projection_never_invents_pricing_or_trends(self):
        dashboard = build_dashboard(
            [],
            days=7,
            now=NOW,
            query="",
            metric="tokens",
            granularity="week",
            chart="line",
        )
        models = build_model_analytics(
            [],
            days=7,
            now=NOW,
            model="all",
            metric="tokens",
            granularity="week",
            chart="line",
        )

        self.assertEqual(dashboard["metrics"]["tokens"], 0)
        self.assertFalse(dashboard["metrics"]["has_cost"])
        self.assertIsNone(dashboard["metrics"]["unit_cost"])
        self.assertEqual(dashboard["insights"], [])
        self.assertIsNone(models["summary"]["cache_hit_rate"])
        self.assertEqual(models["insights"], [])
