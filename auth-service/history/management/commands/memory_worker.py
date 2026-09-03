import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from history.memory_service import MemoryUnavailable, add_chunk, memory_chunks, memory_enabled
from history.models import MemoryIngestJob


class Command(BaseCommand):
    help = "Process pending Mem0 history ingestion jobs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--batch-size", type=int, default=10)
        parser.add_argument("--sleep-seconds", type=float, default=5.0)
        parser.add_argument("--max-attempts", type=int, default=8)

    def _claim_job(self, max_attempts: int):
        now = timezone.now()
        with transaction.atomic():
            job = (
                MemoryIngestJob.objects.select_for_update()
                .filter(status__in=[MemoryIngestJob.Status.PENDING, MemoryIngestJob.Status.FAILED])
                .filter(attempts__lt=max_attempts)
                .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
                .select_related("owner", "session")
                .order_by("created_at", "id")
                .first()
            )
            if job is None:
                return None
            job.status = MemoryIngestJob.Status.RUNNING
            job.attempts += 1
            job.last_error = ""
            job.save(update_fields=["status", "attempts", "last_error"])
            return job

    def _fail(self, job, error: Exception, max_attempts: int):
        now = timezone.now()
        exhausted = job.attempts >= max_attempts
        job.status = MemoryIngestJob.Status.FAILED
        job.last_error = str(error)[:1000]
        job.next_attempt_at = None if exhausted else now + timedelta(
            seconds=min(3600, 2 ** min(job.attempts, 10))
        )
        job.save(update_fields=["status", "last_error", "next_attempt_at"])

    def _process_one(self, job, max_attempts: int):
        try:
            chunks = memory_chunks(job.session)
            if job.chunk_index >= len(chunks):
                raise MemoryUnavailable("memory_source_chunk_missing")
            chunk = chunks[job.chunk_index]
            if tuple(job.message_ids) != chunk.message_ids:
                raise MemoryUnavailable("memory_source_changed")
            memory_ids = add_chunk(
                user=job.owner,
                session=job.session,
                job=job,
                chunk=chunk,
            )
        except Exception as exc:
            self._fail(job, exc, max_attempts)
            return False

        job.status = MemoryIngestJob.Status.SUCCEEDED
        job.mem0_memory_ids = memory_ids
        job.next_attempt_at = None
        job.processed_at = timezone.now()
        job.last_error = ""
        job.save(
            update_fields=[
                "status",
                "mem0_memory_ids",
                "next_attempt_at",
                "processed_at",
                "last_error",
            ]
        )
        return True

    def handle(self, *args, **options):
        if not memory_enabled():
            self.stdout.write("MEMORY_ENABLED is false; no jobs processed")
            return
        batch_size = max(1, min(options["batch_size"], 100))
        max_attempts = max(1, options["max_attempts"])
        processed = 0
        while True:
            current = 0
            while current < batch_size:
                job = self._claim_job(max_attempts)
                if job is None:
                    break
                self._process_one(job, max_attempts)
                processed += 1
                current += 1
            if options["once"]:
                break
            time.sleep(max(0.1, options["sleep_seconds"]))
        self.stdout.write(self.style.SUCCESS(f"jobs_processed={processed}"))
