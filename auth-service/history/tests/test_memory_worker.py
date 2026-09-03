from django.contrib.auth import get_user_model
from django.test import TestCase

from history.management.commands.memory_worker import Command
from history.models import HistorySession, MemoryIngestJob


class MemoryWorkerTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="memory-worker-user", password="safe-memory-pass-1"
        )
        self.session = HistorySession.objects.create(
            owner=self.user,
            uploader=self.user,
            external_id="memory-worker-session",
            title="Memory worker test",
        )

    def _job(self, *, status, attempts, next_attempt_at=None):
        return MemoryIngestJob.objects.create(
            owner=self.user,
            session=self.session,
            source_key=f"worker-{status}-{attempts}",
            message_ids=[],
            content_sha256="a" * 64,
            status=status,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
        )

    def test_exhausted_failed_job_is_not_claimed_again(self):
        job = self._job(status=MemoryIngestJob.Status.FAILED, attempts=8)

        claimed = Command()._claim_job(max_attempts=8)

        self.assertIsNone(claimed)
        job.refresh_from_db()
        self.assertEqual(job.status, MemoryIngestJob.Status.FAILED)
        self.assertEqual(job.attempts, 8)

    def test_pending_job_is_claimed_when_under_attempt_limit(self):
        job = self._job(status=MemoryIngestJob.Status.PENDING, attempts=0)

        claimed = Command()._claim_job(max_attempts=8)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.pk, job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, MemoryIngestJob.Status.RUNNING)
        self.assertEqual(job.attempts, 1)
