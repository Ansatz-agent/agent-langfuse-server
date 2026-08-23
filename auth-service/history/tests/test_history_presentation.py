from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from history.models import HistoryMessage, HistorySession


class HistoryPresentationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="history-reader", password="safe-test-pass-1"
        )
        self.uploader = user_model.objects.create_superuser(
            username="history-uploader",
            password="safe-test-pass-2",
            email="uploader@example.test",
        )
        self.session = HistorySession.objects.create(
            owner=self.owner,
            uploader=self.uploader,
            external_id="readable-history",
            title="Readable history",
        )

    def login_as_owner(self):
        response = self.client.post(
            reverse("login"),
            {"username": "history-reader", "password": "safe-test-pass-1"},
        )
        self.assertEqual(response.status_code, 302)

    def add_message(self, source_id, role, content, **extra):
        return HistoryMessage.objects.create(
            session=self.session,
            source_message_id=source_id,
            role=role,
            content=content,
            **extra,
        )

    def test_detail_groups_messages_into_user_led_turns_on_the_server(self):
        system = self.add_message("system-1", "system", "System context")
        user_one = self.add_message("user-1", "user", "First request")
        assistant_one = self.add_message("assistant-1", "assistant", "First answer")
        tool_one = self.add_message("tool-1", "tool", "Tool result", tool_name="terminal")
        user_two = self.add_message("user-2", "user", "Second request")
        assistant_two = self.add_message("assistant-2", "assistant", "Second answer")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertEqual(response.status_code, 200)
        presentation = response.context["history_presentation"]
        self.assertEqual([message.message.pk for message in presentation.preamble], [system.pk])
        self.assertEqual(len(presentation.turns), 2)
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user_one.pk, assistant_one.pk],
        )
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].context_messages],
            [tool_one.pk],
        )
        self.assertEqual(
            [message.message.pk for message in presentation.turns[1].messages],
            [user_two.pk, assistant_two.pk],
        )
        self.assertTrue(presentation.turns[0].is_complete)
        self.assertTrue(presentation.turns[1].is_complete)

    def test_detail_builds_server_side_turns_for_subagent_threads(self):
        thread = HistorySession.objects.create(
            owner=self.owner,
            uploader=self.uploader,
            parent_session=self.session,
            external_id="readable-thread",
            title="Readable thread",
        )
        thread_user = HistoryMessage.objects.create(
            session=thread,
            source_message_id="thread-user",
            role="user",
            content="Investigate the issue",
        )
        thread_assistant = HistoryMessage.objects.create(
            session=thread,
            source_message_id="thread-assistant",
            role="assistant",
            content="Investigation complete",
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presented_thread = response.context["session"].visible_subagent_threads[0]
        self.assertEqual(
            [
                message.message.pk
                for message in presented_thread.history_presentation.turns[0].messages
            ],
            [thread_user.pk, thread_assistant.pk],
        )

    def test_async_delegation_completion_is_an_event_in_the_previous_turn(self):
        user_one = self.add_message("user-1", "user", "Investigate the deployment.")
        assistant_one = self.add_message("assistant-1", "assistant", "Workers are running.")
        notification = self.add_message(
            "delegation-event",
            "user",
            "[ASYNC DELEGATION BATCH COMPLETE — deleg_test123]\n"
            "A background fan-out of 2 subagent(s) you dispatched earlier has finished.\n\n"
            "--- ✓ TASK 1/2: inspect deployment ---\nCompleted successfully.",
        )
        user_two = self.add_message("user-2", "user", "Apply the result.")
        assistant_two = self.add_message("assistant-2", "assistant", "Applied.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(len(presentation.turns), 2)
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user_one.pk, assistant_one.pk],
        )
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].context_messages],
            [notification.pk],
        )
        self.assertTrue(presentation.turns[0].is_complete)
        self.assertEqual(
            [message.message.pk for message in presentation.turns[1].messages],
            [user_two.pk, assistant_two.pk],
        )
        presented_event = presentation.turns[0].context_messages[-1]
        self.assertTrue(presented_event.is_control_event)
        self.assertEqual(presented_event.normalized_role, "event")
        self.assertContains(response, "后台委托完成")
        self.assertContains(
            response,
            f'<details id="message-{notification.pk}" class="message control-event message-event">',
            html=False,
        )

    def test_async_delegation_display_metadata_prevents_a_new_turn(self):
        user = self.add_message("user-1", "user", "Run a background check.")
        assistant = self.add_message("assistant-1", "assistant", "Started.")
        self.add_message(
            "delegation-metadata-event",
            "user",
            "Opaque event payload",
            raw_metadata={"display_kind": "async_delegation_complete"},
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(len(presentation.turns), 1)
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user.pk, assistant.pk],
        )
        self.assertTrue(presentation.turns[0].context_messages[-1].is_control_event)

    def test_context_compaction_is_not_a_user_turn_and_keeps_following_iteration_context(self):
        compaction = self.add_message(
            "compaction",
            "user",
            "[CONTEXT COMPACTION — REFERENCE ONLY]\nEarlier turns were compacted.",
        )
        continuation = self.add_message("continuation", "assistant", "Continuing from context.")
        tool = self.add_message("context-tool", "tool", "Tool output", tool_name="terminal")
        user = self.add_message("real-user", "user", "What happened?")
        assistant = self.add_message("real-assistant", "assistant", "The task continued.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(
            [message.message.pk for message in presentation.preamble],
            [compaction.pk, continuation.pk, tool.pk],
        )
        self.assertEqual(len(presentation.turns), 1)
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user.pk, assistant.pk],
        )
        self.assertTrue(presentation.preamble[0].is_context_artifact)
        self.assertNotContains(response, "Turn 2")

    def test_tool_iteration_is_collapsed_while_final_agent_reply_stays_visible(self):
        user = self.add_message("iteration-user", "user", "Inspect the service.")
        tool_call = self.add_message(
            "iteration-assistant-tool-call",
            "assistant",
            "",
            tool_calls=[{"name": "terminal", "arguments": {"cmd": "status"}}],
        )
        tool = self.add_message("iteration-tool", "tool", "active", tool_name="terminal")
        final = self.add_message("iteration-final", "assistant", "The service is active.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user.pk, final.pk],
        )
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].context_messages],
            [tool_call.pk, tool.pk],
        )
        self.assertContains(response, "会话上下文 · 2 条")

    def test_memory_tool_calls_stay_visible_outside_turn_context(self):
        user = self.add_message("memory-user", "user", "Remember this preference.")
        memory_call = self.add_message(
            "memory-assistant-call",
            "assistant",
            "",
            tool_calls=[
                {
                    "name": "memory",
                    "arguments": {"action": "add", "content": "Saved preference."},
                }
            ],
        )
        memory_result = self.add_message(
            "memory-tool-result",
            "tool",
            '{"success":true,"target":"user"}',
            tool_name="memory",
        )
        final = self.add_message("memory-final", "assistant", "Saved in your memory pool.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user.pk, memory_call.pk, final.pk],
        )
        self.assertNotIn(
            memory_result.pk,
            [message.message.pk for message in presentation.turns[0].messages],
        )
        self.assertEqual(presentation.turns[0].context_messages, ())
        self.assertContains(response, "记忆 add")
        self.assertContains(response, "Saved preference.")
        self.assertContains(response, "Saved in your memory pool.")

    def test_memory_call_shows_only_first_arguments_content_and_hides_result(self):
        user = self.add_message("memory-dedup-user", "user", "Remember that I like apples.")
        memory_call = self.add_message(
            "memory-dedup-call",
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call_a272p16hkTAsE4PCuLl1YDsD",
                    "call_id": "call_a272p16hkTAsE4PCuLl1YDsD",
                    "response_item_id": "fc_00765a5b3e5b9c39016a7eb0de4ea88194893c2f245be036f1",
                    "type": "function",
                    "function": {
                        "name": "memory",
                        "arguments": (
                            '{"target":"user","action":"add","content":"用户喜欢吃苹果。"}'
                        ),
                    },
                }
            ],
        )
        memory_result = self.add_message(
            "memory-dedup-result",
            "tool",
            '{"success":true,"done":true,"target":"user","usage":"saved"}',
            tool_name="memory",
            tool_call_id="call_a272p16hkTAsE4PCuLl1YDsD",
        )
        final = self.add_message("memory-dedup-final", "assistant", "Done.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user.pk, memory_call.pk, final.pk],
        )
        self.assertNotIn(
            memory_result.pk,
            [message.message.pk for message in presentation.turns[0].messages],
        )
        self.assertContains(response, "记忆 add")
        self.assertContains(response, "用户喜欢吃苹果。")
        self.assertNotContains(response, "call_a272p16hkTAsE4PCuLl1YDsD")
        self.assertNotContains(response, "response_item_id")
        self.assertNotContains(response, "&quot;usage&quot;", html=False)
        self.assertNotContains(response, "saved")
        self.assertNotContains(response, "target")

    def test_memory_tools_before_first_user_are_outside_global_context_collapse(self):
        memory_call = self.add_message(
            "memory-preamble-call",
            "assistant",
            "",
            tool_calls=[{"name": "memory", "arguments": {"action": "add"}}],
        )
        memory_result = self.add_message(
            "memory-preamble-result",
            "tool",
            "Memory saved.",
            tool_name="memory",
        )
        context_tool = self.add_message("ordinary-preamble-tool", "tool", "Other output")
        self.add_message("preamble-user", "user", "Continue.")
        self.add_message("preamble-assistant", "assistant", "Done.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(
            [message.message.pk for message in presentation.preamble_memory_tools],
            [memory_call.pk],
        )
        self.assertNotIn(
            memory_result.pk,
            [message.message.pk for message in presentation.preamble_memory_tools],
        )
        self.assertEqual(
            [message.message.pk for message in presentation.preamble],
            [context_tool.pk],
        )

    def test_memory_delete_and_replace_use_their_own_titles(self):
        self.add_message("memory-actions-user", "user", "Update the saved memory.")
        self.add_message(
            "memory-delete-call",
            "assistant",
            "",
            tool_calls=[
                {
                    "function": {
                        "name": "memory",
                        "arguments": '{"target":"user","action":"delete","content":"旧内容"}',
                    }
                }
            ],
        )
        self.add_message(
            "memory-delete-result",
            "tool",
            "deleted",
            tool_name="memory",
        )
        self.add_message("memory-actions-next-user", "user", "Replace the memory.")
        self.add_message(
            "memory-replace-call",
            "assistant",
            "",
            tool_calls=[
                {
                    "function": {
                        "name": "memory",
                        "arguments": '{"target":"user","action":"replace","content":"新内容"}',
                    }
                }
            ],
        )
        self.add_message(
            "memory-replace-result",
            "tool",
            "replaced",
            tool_name="memory",
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertContains(response, "记忆 delete")
        self.assertContains(response, "记忆 replace")
        self.assertNotContains(response, "记忆 add")

    def test_mixed_memory_and_regular_tool_calls_preserve_regular_call(self):
        user = self.add_message("mixed-user", "user", "Check and remember this.")
        mixed_call = self.add_message(
            "mixed-assistant-call",
            "assistant",
            "",
            tool_calls=[
                {
                    "function": {
                        "name": "memory",
                        "arguments": '{"target":"user","action":"add","content":"保留这条"}',
                    }
                },
                {
                    "function": {
                        "name": "terminal",
                        "arguments": '{"cmd":"status"}',
                    }
                },
            ],
        )
        self.add_message("mixed-memory-result", "tool", "saved", tool_name="memory")
        terminal_result = self.add_message(
            "mixed-terminal-result",
            "tool",
            "active",
            tool_name="terminal",
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        mixed_parts = [
            message
            for turn in presentation.turns
            for message in (*turn.messages, *turn.context_messages)
            if message.message.pk == mixed_call.pk
        ]
        self.assertEqual(len(mixed_parts), 2)
        self.assertTrue(any(message.is_memory_tool for message in mixed_parts))
        regular_parts = [message for message in mixed_parts if not message.is_memory_tool]
        self.assertEqual(
            [call.get("function", {}).get("name") for call in regular_parts[0].display_tool_calls],
            ["terminal"],
        )
        self.assertContains(response, "记忆 add")
        self.assertContains(response, "terminal")
        self.assertContains(response, "active")
        self.assertContains(response, f'id="message-{mixed_call.pk}-memory"', html=False)
        self.assertContains(response, f'id="message-{mixed_call.pk}-tools"', html=False)
        self.assertIn(
            terminal_result.pk,
            [message.message.pk for message in presentation.turns[0].context_messages],
        )
        self.assertIn(user.pk, [message.message.pk for message in presentation.turns[0].messages])

    def test_memory_result_at_end_does_not_change_turn_completeness(self):
        self.add_message("memory-incomplete-user", "user", "Remember this.")
        self.add_message(
            "memory-incomplete-call",
            "assistant",
            "",
            tool_calls=[
                {
                    "function": {
                        "name": "memory",
                        "arguments": '{"target":"user","action":"add","content":"未完成"}',
                    }
                }
            ],
        )
        self.add_message("memory-incomplete-result", "tool", "saved", tool_name="memory")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertTrue(response.context["history_presentation"].turns[0].is_complete)
        self.assertContains(response, "未完成")

    def test_malformed_memory_arguments_are_not_silently_presented_as_empty(self):
        self.add_message("memory-invalid-user", "user", "Remember this.")
        self.add_message(
            "memory-invalid-call",
            "assistant",
            "",
            tool_calls=[
                {
                    "function": {
                        "name": "memory",
                        "arguments": '{"action":"add","content":',
                    }
                }
            ],
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertContains(response, "记忆 参数无效")

    def test_memory_only_preamble_does_not_show_empty_session_message(self):
        self.add_message(
            "memory-only-call",
            "assistant",
            "",
            tool_calls=[{"name": "memory", "arguments": {"action": "add"}}],
        )
        self.add_message(
            "memory-only-result",
            "tool",
            "Memory saved.",
            tool_name="memory",
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertContains(response, "记忆 add", count=1)
        self.assertNotContains(response, "此会话没有消息。")

    def test_control_event_before_first_real_user_stays_in_preamble(self):
        notification = self.add_message(
            "early-delegation-event",
            "user",
            "[ASYNC DELEGATION COMPLETE — deleg_early]",
        )
        user = self.add_message("user-1", "user", "Continue.")
        assistant = self.add_message("assistant-1", "assistant", "Done.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(
            [message.message.pk for message in presentation.preamble],
            [notification.pk],
        )
        self.assertEqual(len(presentation.turns), 1)
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user.pk, assistant.pk],
        )

    def test_near_prefix_and_non_user_prefix_are_not_control_events(self):
        user_one = self.add_message("user-1", "user", "Quote the event format.")
        assistant_one = self.add_message(
            "assistant-1",
            "assistant",
            "[ASYNC DELEGATION COMPLETE — deleg_assistant]",
        )
        user_two = self.add_message(
            "user-2",
            "user",
            "[ASYNC DELEGATION COMPLETE — this is ordinary unfinished text",
            raw_metadata=[],
        )
        assistant_two = self.add_message("assistant-2", "assistant", "Still a real turn.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        presentation = response.context["history_presentation"]
        self.assertEqual(len(presentation.turns), 2)
        self.assertEqual(
            [message.message.pk for message in presentation.turns[0].messages],
            [user_one.pk, assistant_one.pk],
        )
        self.assertEqual(
            [message.message.pk for message in presentation.turns[1].messages],
            [user_two.pk, assistant_two.pk],
        )
        self.assertFalse(
            any(m.is_control_event for turn in presentation.turns for m in turn.messages)
        )

    def test_detail_renders_message_markdown_on_the_server(self):
        self.add_message(
            "markdown-user",
            "user",
            "# Deployment plan\n\n- Back up the database\n- Run **tests**\n\nUse `uv run`.",
        )
        self.add_message(
            "markdown-assistant",
            "assistant",
            "```python\nprint('verified')\n```",
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertContains(response, "<h1>Deployment plan</h1>", html=False)
        self.assertContains(response, "<strong>tests</strong>", html=False)
        self.assertContains(response, "<code>uv run</code>", html=False)
        self.assertContains(response, '<code class="language-python">', html=False)

    def test_rendered_markdown_marks_history_links_as_untrusted(self):
        self.add_message("link-user", "user", "Read [the docs](https://example.test/docs).")
        self.add_message("link-assistant", "assistant", "Done.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertContains(
            response,
            '<a href="https://example.test/docs" rel="nofollow noreferrer">the docs</a>',
            html=False,
        )

    def test_tool_messages_are_collapsible_and_have_stable_page_anchors(self):
        self.add_message("tool-user", "user", "Run the check.")
        tool = self.add_message(
            "tool-result",
            "tool",
            "exit code: 0",
            tool_name="terminal",
        )
        self.add_message("tool-assistant", "assistant", "The check passed.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertContains(
            response,
            f'<details id="message-{tool.pk}" class="message tool-event message-tool">',
            html=False,
        )
        self.assertContains(response, "terminal")
        self.assertContains(response, "exit code: 0")

    def test_presentation_keeps_every_turn_without_windowing_or_compression(self):
        for index in range(15):
            self.add_message(f"user-{index}", "user", f"Request {index}")
            self.add_message(f"assistant-{index}", "assistant", f"Answer {index}")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertEqual(len(response.context["history_presentation"].turns), 15)
        self.assertContains(response, "Request 0")
        self.assertContains(response, "Request 7")
        self.assertContains(response, "Answer 14")

    def test_markdown_does_not_activate_javascript_links(self):
        self.add_message("unsafe-link-user", "user", "[click](javascript:alert(1))")
        self.add_message("unsafe-link-assistant", "assistant", "Ignored.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertNotContains(response, 'href="javascript:', html=False)
        self.assertContains(response, "javascript:alert(1)")

    def test_markdown_does_not_auto_load_remote_images(self):
        self.add_message(
            "remote-image-user",
            "user",
            "![tracking pixel](https://tracker.example.test/pixel.png)",
        )
        self.add_message("remote-image-assistant", "assistant", "Ignored.")
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.session.pk]))

        self.assertNotContains(response, "<img", html=False)
        self.assertContains(response, "[图片：tracking pixel]")
