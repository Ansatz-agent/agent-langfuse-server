import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from history.importer import ImportValidationError, import_history
from history.models import HistoryMessage, HistorySession, ImportBatch


def upload(rows, name="history.jsonl"):
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode()
    return SimpleUploadedFile(name, body, content_type="application/x-ndjson")


def session_row(external_id="session-1", content="hello", **extra):
    row = {
        "id": external_id,
        "title": "Imported session",
        "source": "cli",
        "model": "test-model",
        "started_at": 1767225600,
        "message_count": 1,
        "messages": [
            {
                "id": 1,
                "role": "user",
                "content": content,
                "timestamp": 1767225660,
                "tool_calls": [],
            }
        ],
    }
    row.update(extra)
    return row


class ImporterTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(username="alice", password="safe-test-pass-1")
        self.bob = user_model.objects.create_user(username="bob", password="safe-test-pass-2")

    def test_valid_jsonl_imports_session_and_messages_for_explicit_owner(self):
        result = import_history(upload([session_row()]), owner=self.alice, uploader=self.alice)

        self.assertEqual(result.imported_sessions, 1)
        self.assertEqual(result.imported_messages, 1)
        session = HistorySession.objects.get()
        self.assertEqual(session.owner, self.alice)
        self.assertEqual(session.external_id, "session-1")
        self.assertEqual(session.messages.get().content, "hello")

    def test_import_persists_exact_session_token_usage(self):
        row = session_row(
            input_tokens=123_456,
            output_tokens=7_890,
            cache_read_tokens=222_333,
            cache_write_tokens=444,
            reasoning_tokens=5_678,
        )

        import_history(upload([row]), owner=self.alice, uploader=self.alice)

        session = HistorySession.objects.get()
        self.assertEqual(session.input_tokens, 123_456)
        self.assertEqual(session.output_tokens, 7_890)
        self.assertEqual(session.cache_read_tokens, 222_333)
        self.assertEqual(session.cache_write_tokens, 444)
        self.assertEqual(session.reasoning_tokens, 5_678)

    def test_import_rejects_negative_session_token_usage(self):
        row = session_row(input_tokens=-1)

        with self.assertRaisesRegex(ImportValidationError, "input_tokens"):
            import_history(upload([row]), owner=self.alice, uploader=self.alice)

        self.assertEqual(HistorySession.objects.count(), 0)
        self.assertEqual(ImportBatch.objects.get().status, ImportBatch.Status.FAILED)

    def test_import_preserves_hermes_display_kind_metadata(self):
        row = session_row()
        row["messages"][0]["display_kind"] = "async_delegation_complete"
        row["messages"][0]["display_metadata"] = {
            "delegation_id": "deleg_test123",
            "task_count": 2,
        }

        import_history(upload([row]), owner=self.alice, uploader=self.alice)

        self.assertEqual(
            HistoryMessage.objects.get().raw_metadata,
            {
                "display_kind": "async_delegation_complete",
                "display_metadata": {
                    "delegation_id": "deleg_test123",
                    "task_count": 2,
                },
            },
        )

    def test_import_redacts_secrets_inside_display_metadata(self):
        row = session_row()
        row["messages"][0]["display_kind"] = "async_delegation_complete"
        row["messages"][0]["display_metadata"] = {
            "delegation_id": "deleg_test123",
            "access_token": "synthetic-display-token",
        }

        import_history(upload([row]), owner=self.alice, uploader=self.alice)

        metadata = HistoryMessage.objects.get().raw_metadata
        self.assertEqual(metadata["display_kind"], "async_delegation_complete")
        self.assertNotIn("synthetic-display-token", json.dumps(metadata))

    def test_import_links_child_to_parent_and_records_uploader(self):
        child = session_row("child", parent_session_id="parent")
        parent = session_row("parent")

        import_history(upload([child, parent]), owner=self.alice, uploader=self.bob)

        parent_session = HistorySession.objects.get(external_id="parent")
        child_session = HistorySession.objects.get(external_id="child")
        self.assertEqual(parent_session.uploader, self.bob)
        self.assertEqual(child_session.uploader, self.bob)
        self.assertEqual(child_session.parent_session, parent_session)

    def test_import_accepts_nested_subagent_threads_from_portal_export(self):
        parent = session_row("parent")
        parent["subagent_threads"] = [session_row("child")]

        result = import_history(upload([parent]), owner=self.alice, uploader=self.bob)

        self.assertEqual(result.imported_sessions, 2)
        parent_session = HistorySession.objects.get(external_id="parent")
        child_session = HistorySession.objects.get(external_id="child")
        self.assertEqual(child_session.parent_session, parent_session)

    def test_import_rejects_nested_threads_deeper_than_one_level(self):
        parent = session_row("parent")
        child = session_row("child")
        child["subagent_threads"] = [session_row("grandchild")]
        parent["subagent_threads"] = [child]

        with self.assertRaisesRegex(ImportValidationError, "one subagent level"):
            import_history(upload([parent]), owner=self.alice, uploader=self.bob)

        self.assertEqual(HistorySession.objects.count(), 0)

    def test_import_rejects_session_that_is_its_own_parent(self):
        row = session_row("loop", parent_session_id="loop")

        with self.assertRaisesRegex(ImportValidationError, "Parent cycle"):
            import_history(upload([row]), owner=self.alice, uploader=self.alice)

        self.assertEqual(HistorySession.objects.count(), 0)
        self.assertEqual(ImportBatch.objects.get().status, ImportBatch.Status.FAILED)

    def test_import_rejects_parent_cycle(self):
        first = session_row("first", parent_session_id="second")
        second = session_row("second", parent_session_id="first")

        with self.assertRaisesRegex(ImportValidationError, "Parent cycle"):
            import_history(upload([first, second]), owner=self.alice, uploader=self.alice)

        self.assertEqual(HistorySession.objects.count(), 0)

    def test_payload_owner_fields_cannot_change_ownership(self):
        row = session_row(owner_id=self.bob.pk, user_id=self.bob.pk, owner="bob")

        import_history(upload([row]), owner=self.alice, uploader=self.alice)

        self.assertEqual(HistorySession.objects.get().owner, self.alice)

    def test_repeated_import_is_idempotent_and_skips_existing_session(self):
        import_history(upload([session_row()]), owner=self.alice, uploader=self.alice)

        result = import_history(upload([session_row()]), owner=self.alice, uploader=self.alice)

        self.assertEqual(result.imported_sessions, 0)
        self.assertEqual(result.skipped_sessions, 1)
        self.assertEqual(HistorySession.objects.count(), 1)
        self.assertEqual(HistoryMessage.objects.count(), 1)

    def test_reimport_repairs_missing_parent_without_changing_original_uploader(self):
        existing_child = HistorySession.objects.create(
            owner=self.alice,
            uploader=self.alice,
            external_id="child",
            raw_metadata={"parent_session_id": "parent"},
        )
        child = session_row("child", parent_session_id="parent")
        parent = session_row("parent")

        result = import_history(upload([child, parent]), owner=self.alice, uploader=self.bob)

        existing_child.refresh_from_db()
        self.assertEqual(result.imported_sessions, 1)
        self.assertEqual(result.skipped_sessions, 1)
        self.assertEqual(existing_child.parent_session.external_id, "parent")
        self.assertEqual(existing_child.uploader, self.alice)

    def test_reimport_rejects_conflicting_existing_parent(self):
        first_parent = HistorySession.objects.create(
            owner=self.alice,
            uploader=self.alice,
            external_id="first-parent",
        )
        HistorySession.objects.create(
            owner=self.alice,
            uploader=self.alice,
            parent_session=first_parent,
            external_id="child",
        )
        conflicting = session_row("child", parent_session_id="second-parent")
        second_parent = session_row("second-parent")

        with self.assertRaisesRegex(ImportValidationError, "conflicting parent"):
            import_history(
                upload([conflicting, second_parent]),
                owner=self.alice,
                uploader=self.bob,
            )

        self.assertFalse(HistorySession.objects.filter(external_id="second-parent").exists())

    def test_import_rejects_flat_parent_chain_deeper_than_one_level(self):
        rows = [
            session_row("root"),
            session_row("child", parent_session_id="root"),
            session_row("grandchild", parent_session_id="child"),
        ]

        with self.assertRaisesRegex(ImportValidationError, "one subagent level"):
            import_history(upload(rows), owner=self.alice, uploader=self.bob)

        self.assertEqual(HistorySession.objects.count(), 0)

    def test_same_external_id_can_be_imported_by_another_owner(self):
        import_history(upload([session_row()]), owner=self.alice, uploader=self.alice)

        import_history(upload([session_row()]), owner=self.bob, uploader=self.bob)

        self.assertEqual(HistorySession.objects.filter(external_id="session-1").count(), 2)

    def test_malformed_later_line_rolls_back_all_history_writes(self):
        body = (json.dumps(session_row(external_id="valid-first")) + "\n" + "{not-json}\n").encode()
        uploaded = SimpleUploadedFile("bad.jsonl", body, content_type="application/x-ndjson")

        with self.assertRaises(ImportValidationError):
            import_history(uploaded, owner=self.alice, uploader=self.alice)

        self.assertEqual(HistorySession.objects.count(), 0)
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.status, ImportBatch.Status.FAILED)
        self.assertNotEqual(batch.error_summary, "")

    @override_settings(HISTORY_IMPORT_MAX_BYTES=10)
    def test_oversized_input_is_rejected_without_history_writes(self):
        with self.assertRaises(ImportValidationError):
            import_history(upload([session_row()]), owner=self.alice, uploader=self.alice)

        self.assertEqual(HistorySession.objects.count(), 0)

    @override_settings(HISTORY_IMPORT_MAX_SESSIONS=1)
    def test_session_count_limit_is_enforced(self):
        rows = [session_row("one"), session_row("two")]

        with self.assertRaises(ImportValidationError):
            import_history(upload(rows), owner=self.alice, uploader=self.alice)

        self.assertEqual(HistorySession.objects.count(), 0)

    def test_secret_patterns_are_redacted_before_persistence(self):
        content = "Authorization: Bearer top-secret-token and password=SuperSecret123!"

        import_history(
            upload([session_row(content=content)]), owner=self.alice, uploader=self.alice
        )

        stored = HistoryMessage.objects.get().content
        self.assertNotIn("top-secret-token", stored)
        self.assertNotIn("SuperSecret123!", stored)
        self.assertIn("[REDACTED]", stored)

    def test_chinese_secret_labels_are_redacted(self):
        content = "用户名 root\n密码 chinese-test-password-123\nAPI 密钥：custom-api-key-value"

        import_history(
            upload([session_row(content=content)]), owner=self.alice, uploader=self.alice
        )

        stored = HistoryMessage.objects.get().content
        self.assertNotIn("chinese-test-password-123", stored)
        self.assertNotIn("custom-api-key-value", stored)
        self.assertGreaterEqual(stored.count("[REDACTED]"), 2)

    def test_all_authorization_schemes_and_cookie_headers_are_redacted(self):
        content = (
            "Authorization: Basic dGVzdC11c2VyOnRlc3QtcGFzcw==\n"
            "Authorization: Token opaque-authorization-credential\n"
            "Cookie: sessionid=sensitive-session-cookie"
        )

        import_history(
            upload([session_row(content=content)]), owner=self.alice, uploader=self.alice
        )

        stored = HistoryMessage.objects.get().content
        self.assertNotIn("dGVzdC11c2VyOnRlc3QtcGFzcw==", stored)
        self.assertNotIn("opaque-authorization-credential", stored)
        self.assertNotIn("sensitive-session-cookie", stored)
        self.assertGreaterEqual(stored.count("[REDACTED]"), 3)

    def test_private_key_and_cookie_fields_are_redacted_recursively(self):
        row = session_row(
            content={
                "private_key": "synthetic-private-key-material",
                "privateKey": "synthetic-camel-private-key",
                "cookie": "sessionid=synthetic-cookie-value",
                "nested": {
                    "session_id": "synthetic-session-id",
                    "accessToken": "synthetic-camel-access-token",
                },
            }
        )
        row["messages"][0]["tool_calls"] = [
            {
                "private_key": "synthetic-tool-private-key",
                "setCookie": "sessionid=synthetic-tool-cookie",
            }
        ]

        import_history(upload([row]), owner=self.alice, uploader=self.alice)

        stored = HistoryMessage.objects.get()
        serialized = stored.content + json.dumps(stored.tool_calls)
        self.assertNotIn("synthetic-private-key-material", serialized)
        self.assertNotIn("synthetic-camel-private-key", serialized)
        self.assertNotIn("synthetic-cookie-value", serialized)
        self.assertNotIn("synthetic-session-id", serialized)
        self.assertNotIn("synthetic-camel-access-token", serialized)
        self.assertNotIn("synthetic-tool-private-key", serialized)
        self.assertNotIn("synthetic-tool-cookie", serialized)

    def test_contextual_headers_and_sensitive_key_variants_are_redacted(self):
        row = session_row(
            content={
                "authorizationHeader": "Bearer synthetic-auth-header",
                "bearerToken": "synthetic-bearer-token",
                "APIKey": "synthetic-uppercase-api-key",
                "privateKeyPem": "synthetic-private-key-pem",
                "headers": [
                    {"name": "Authorization", "value": "Bearer synthetic-header-list"},
                    {"key": "Cookie", "value": "sessionid=synthetic-cookie-list"},
                    ["Proxy-Authorization", "Basic synthetic-header-pair"],
                ],
            }
        )

        import_history(upload([row]), owner=self.alice, uploader=self.alice)

        stored = HistoryMessage.objects.get().content
        for leaked_value in (
            "synthetic-auth-header",
            "synthetic-bearer-token",
            "synthetic-uppercase-api-key",
            "synthetic-private-key-pem",
            "synthetic-header-list",
            "synthetic-cookie-list",
            "synthetic-header-pair",
        ):
            self.assertNotIn(leaked_value, stored)

    def test_pem_private_key_blocks_are_redacted(self):
        content = (
            "-----BEGIN PRIVATE KEY-----\n"
            "c3ludGhldGljLXByaXZhdGUta2V5LW1hdGVyaWFs\n"
            "-----END PRIVATE KEY-----"
        )

        import_history(
            upload([session_row(content=content)]), owner=self.alice, uploader=self.alice
        )

        stored = HistoryMessage.objects.get().content
        self.assertNotIn("c3ludGhldGljLXByaXZhdGUta2V5LW1hdGVyaWFs", stored)
        self.assertIn("[REDACTED PRIVATE KEY]", stored)

    def test_upload_content_is_not_stored_in_import_batch(self):
        import_history(
            upload([session_row(content="private words")]), owner=self.alice, uploader=self.alice
        )

        batch = ImportBatch.objects.get()
        serialized = " ".join(
            [batch.original_filename, batch.sha256, batch.error_summary, batch.status]
        )
        self.assertNotIn("private words", serialized)
        self.assertEqual(len(batch.sha256), 64)

    def test_structured_sensitive_keys_are_redacted_recursively(self):
        row = session_row(
            content={
                "password": "hunter2-secret",
                "api_key": "plain-service-key-123",
                "nested": {"access_token": "opaque-token-123456"},
                "token_count": 7,
            }
        )

        import_history(upload([row]), owner=self.alice, uploader=self.alice)

        stored = HistoryMessage.objects.get().content
        self.assertNotIn("hunter2-secret", stored)
        self.assertNotIn("plain-service-key-123", stored)
        self.assertNotIn("opaque-token-123456", stored)
        self.assertIn("token_count", stored)

    def test_json_and_environment_secret_syntax_is_redacted(self):
        content = (
            '{"password":"json-password", "api_key":"json-api-key"}\n'
            "OPENAI_API_KEY=environment-api-key"
        )

        import_history(
            upload([session_row(content=content)]), owner=self.alice, uploader=self.alice
        )

        stored = HistoryMessage.objects.get().content
        self.assertNotIn("json-password", stored)
        self.assertNotIn("json-api-key", stored)
        self.assertNotIn("environment-api-key", stored)
