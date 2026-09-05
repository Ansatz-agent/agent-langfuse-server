from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from history.memory_service import _provider_config, enqueue_session_memory_jobs, memory_chunks
from history.models import HistoryMessage, HistorySession, MemoryIngestJob


class MemoryServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="memory-service-user", password="safe-memory-pass-1"
        )
        self.session = HistorySession.objects.create(
            owner=self.user,
            uploader=self.user,
            external_id="memory-service-session",
            title="Memory service test",
        )

    def test_chunks_include_only_user_and_assistant_and_redact_content(self):
        HistoryMessage.objects.create(
            session=self.session,
            role="user",
            content="Please use api_key=synthetic-secret for this test.",
        )
        HistoryMessage.objects.create(
            session=self.session,
            role="tool",
            content="Tool output must not enter memory.",
        )
        HistoryMessage.objects.create(
            session=self.session,
            role="assistant",
            content="I will not retain the credential.",
        )

        chunks = memory_chunks(self.session)

        self.assertEqual(len(chunks), 1)
        self.assertEqual([message["role"] for message in chunks[0].messages], ["user", "assistant"])
        self.assertNotIn("synthetic-secret", chunks[0].messages[0]["content"])

    def test_enqueue_is_idempotent_for_the_same_session(self):
        HistoryMessage.objects.create(session=self.session, role="user", content="Remember this.")

        first = enqueue_session_memory_jobs(self.session)
        second = enqueue_session_memory_jobs(self.session)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(MemoryIngestJob.objects.count(), 1)
        job = MemoryIngestJob.objects.get()
        self.assertEqual(job.owner_id, self.user.pk)
        self.assertEqual(job.message_ids, [HistoryMessage.objects.get().pk])

    def test_provider_config_supports_role_specific_endpoint_and_key(self):
        with patch.dict(
            "os.environ",
            {
                "MEMORY_OPENAI_BASE_URL": "https://primary.example/v1",
                "MEMORY_PROVIDER_API_KEY": "primary-key",
                "MEMORY_LLM_OPENAI_BASE_URL": "https://alternate.example/v1",
                "MEMORY_LLM_API_KEY": "alternate-key",
            },
            clear=False,
        ):
            config = _provider_config(
                "openai",
                "alternate-model",
                base_url_env="MEMORY_LLM_OPENAI_BASE_URL",
                api_key_env="MEMORY_LLM_API_KEY",
                reasoning_effort="high",
                is_reasoning_model=True,
            )

        self.assertEqual(config["config"]["openai_base_url"], "https://alternate.example/v1")
        self.assertEqual(config["config"]["api_key"], "alternate-key")
        self.assertEqual(config["config"]["reasoning_effort"], "high")
        self.assertTrue(config["config"]["is_reasoning_model"])

    def test_provider_config_falls_back_to_primary_for_embedding(self):
        with patch.dict(
            "os.environ",
            {
                "MEMORY_OPENAI_BASE_URL": "https://primary.example/v1",
                "MEMORY_PROVIDER_API_KEY": "primary-key",
                "MEMORY_EMBEDDER_OPENAI_BASE_URL": "",
                "MEMORY_EMBEDDER_API_KEY": "",
            },
            clear=False,
        ):
            config = _provider_config(
                "openai",
                "embedding-model",
                base_url_env="MEMORY_EMBEDDER_OPENAI_BASE_URL",
                api_key_env="MEMORY_EMBEDDER_API_KEY",
            )

        self.assertEqual(config["config"]["openai_base_url"], "https://primary.example/v1")
        self.assertEqual(config["config"]["api_key"], "primary-key")
