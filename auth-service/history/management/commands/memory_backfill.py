from django.core.management.base import BaseCommand
from django.db import transaction

from history.memory_service import enqueue_session_memory_jobs, memory_chunks
from history.models import HistorySession


class Command(BaseCommand):
    help = "Enqueue owner-scoped history sessions for Mem0 ingestion."

    def add_arguments(self, parser):
        parser.add_argument("--owner-id", type=int)
        parser.add_argument("--session-id", type=int)
        parser.add_argument("--limit", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        sessions = HistorySession.objects.select_related("owner").order_by("pk")
        if options.get("owner_id") is not None:
            sessions = sessions.filter(owner_id=options["owner_id"])
        if options.get("session_id") is not None:
            sessions = sessions.filter(pk=options["session_id"])
        if options.get("limit") is not None:
            if options["limit"] < 1:
                raise ValueError("--limit must be positive")
            sessions = sessions[: options["limit"]]

        totals = {"sessions": 0, "eligible_messages": 0, "chunks": 0, "jobs": 0}
        for session in sessions.iterator():
            chunks = memory_chunks(session)
            totals["sessions"] += 1
            totals["eligible_messages"] += sum(len(chunk.messages) for chunk in chunks)
            totals["chunks"] += len(chunks)
            if not options["dry_run"]:
                with transaction.atomic():
                    totals["jobs"] += enqueue_session_memory_jobs(session)

        self.stdout.write(
            self.style.SUCCESS(
                "sessions={sessions} eligible_messages={eligible_messages} "
                "chunks={chunks} jobs_created={jobs} dry_run={dry_run}".format(
                    **totals, dry_run=bool(options["dry_run"])
                )
            )
        )
