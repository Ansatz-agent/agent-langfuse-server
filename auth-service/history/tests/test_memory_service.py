from django.contrib.auth import get_user_model
from django.test import TestCase

from history.memory_service import enqueue_session_memory_jobs, memory_chunks
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
