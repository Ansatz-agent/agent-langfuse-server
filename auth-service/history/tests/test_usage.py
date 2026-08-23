from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from history.models import HistoryMessage, HistorySession
from history.usage import _segments_from_counts, build_context_allocation, estimate_tokens


class ContextAllocationTests(SimpleTestCase):
    def test_context_allocation_separates_reasoning_code_chat_tools_and_system_text(self):
        messages = [
            HistoryMessage(role="user", content="请修复这个问题。", raw_metadata={}),
            HistoryMessage(
                role="assistant",
                content="已完成。\n```python\nprint('ok')\n```",
                raw_metadata={"reasoning_content": "先检查再修改"},
            ),
            HistoryMessage(
                role="assistant",
                content="",
                tool_calls=[{"name": "terminal", "arguments": {"cmd": "pytest"}}],
                raw_metadata={},
            ),
            HistoryMessage(
                role="tool",
                content="2 passed",
                tool_name="terminal",
                raw_metadata={},
            ),
            HistoryMessage(role="system", content="policy", raw_metadata={}),
        ]

        allocation = build_context_allocation(messages)
        tokens = allocation.tokens_by_key

        self.assertEqual(tokens["reasoning"], estimate_tokens("先检查再修改"))
        self.assertEqual(tokens["code"], estimate_tokens("print('ok')"))
        self.assertGreater(tokens["conversation"], 0)
        self.assertGreater(tokens["tools"], 0)
        self.assertGreater(tokens["system"], 0)
        self.assertEqual(allocation.total_tokens, sum(tokens.values()))
        self.assertAlmostEqual(sum(segment.percent for segment in allocation.segments), 100.0)
        self.assertEqual(allocation.segments[0].start_percent, 0.0)
        self.assertAlmostEqual(
            allocation.segments[-1].start_percent + allocation.segments[-1].percent,
            100.0,
        )

    def test_context_allocation_rounding_never_creates_negative_svg_segments(self):
        segments = _segments_from_counts(
            {
                "reasoning": 529_203,
                "code": 146_040,
                "conversation": 295_529,
                "tools": 146_535,
                "system": 1,
            }
        )

        self.assertTrue(all(segment.percent >= 0.1 for segment in segments))
        self.assertEqual(sum(segment.percent for segment in segments), 100.0)
        self.assertEqual(segments[0].start_percent, 0.0)
        self.assertEqual(segments[-1].start_percent + segments[-1].percent, 100.0)


class UsageDashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user(username="usage-alice", password="safe-pass-1")
        self.bob = User.objects.create_user(username="usage-bob", password="safe-pass-2")
        self.parent = HistorySession.objects.create(
            owner=self.alice,
            uploader=self.alice,
            external_id="alice-parent",
            title="Alice token session",
            input_tokens=100_000,
            output_tokens=10_000,
            cache_read_tokens=50,
            reasoning_tokens=3,
        )
        HistorySession.objects.create(
            owner=self.alice,
            uploader=self.alice,
            parent_session=self.parent,
            external_id="alice-child",
            title="Alice child token session",
            input_tokens=30_000,
            output_tokens=3_000,
            cache_read_tokens=20,
            reasoning_tokens=1,
        )
        self.bob_parent = HistorySession.objects.create(
            owner=self.bob,
            uploader=self.bob,
            external_id="bob-private",
            title="Bob private token session",
            input_tokens=900,
            output_tokens=90,
        )
        HistorySession.objects.create(
            owner=self.alice,
            uploader=self.alice,
            parent_session=self.bob_parent,
            external_id="malformed-cross-owner-child",
            title="Malformed cross-owner child",
            input_tokens=9_000_000,
            output_tokens=9_000_000,
        )

    def test_usage_dashboard_is_owner_scoped_and_aggregates_subagent_usage(self):
        login = self.client.post(
            reverse("login"),
            {"username": "usage-alice", "password": "safe-pass-1"},
        )
        self.assertEqual(login.status_code, 302)

        response = self.client.get(reverse("history:usage-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["usage_stats"]["input_tokens"], 130_000)
        self.assertEqual(response.context["usage_stats"]["output_tokens"], 13_000)
        self.assertEqual(response.context["usage_stats"]["cache_read_tokens"], 70)
        self.assertEqual(response.context["usage_stats"]["reasoning_tokens"], 4)
        self.assertEqual(response.context["usage_stats"]["threads"], 1)
        self.assertEqual(list(response.context["sessions"]), [self.parent])
        self.assertContains(response, "Token 用量")
        self.assertContains(response, "130,000")
        self.assertContains(response, "13,000")
        self.assertContains(response, "Alice token session")
        self.assertNotContains(response, "Bob private token session")
        self.assertNotContains(response, "Malformed cross-owner child")

    def test_session_detail_shows_exact_tokens_and_estimated_context_allocation(self):
        HistoryMessage.objects.create(
            session=self.parent,
            source_message_id="usage-user",
            role="user",
            content="请实现 token 仪表盘",
        )
        HistoryMessage.objects.create(
            session=self.parent,
            source_message_id="usage-assistant",
            role="assistant",
            content="实现完成。\n```python\nprint('usage')\n```",
            raw_metadata={"reasoning_content": "先测试再实现"},
        )
        login = self.client.post(
            reverse("login"),
            {"username": "usage-alice", "password": "safe-pass-1"},
        )
        self.assertEqual(login.status_code, 302)

        response = self.client.get(reverse("history:session-detail", args=[self.parent.pk]))

        usage = response.context["session_usage"]
        self.assertEqual(usage["input_tokens"], 130_000)
        self.assertEqual(usage["output_tokens"], 13_000)
        self.assertEqual(usage["reasoning_tokens"], 4)
        self.assertGreater(response.context["context_allocation"].tokens_by_key["code"], 0)
        self.assertContains(response, "130,000")
        self.assertContains(response, "Context allocation")
        self.assertContains(response, '<svg class="context-allocation-bar"', html=False)
        self.assertContains(response, "推理")
        self.assertContains(response, "代码")
        self.assertContains(response, "估算")
