from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from history.memory_service import delete_all_memories
from history.models import MemoryIngestJob


class Command(BaseCommand):
    help = "Delete all Mem0 memories for one Django user."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--user-id", type=int)
        group.add_argument("--username")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Refusing to erase memories without --confirm")
        User = get_user_model()
        try:
            if options.get("user_id") is not None:
                user = User.objects.get(pk=options["user_id"])
            else:
                user = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError("User does not exist") from exc

        delete_all_memories(user=user)
        with transaction.atomic():
            MemoryIngestJob.objects.filter(owner=user).update(
                status=MemoryIngestJob.Status.DELETED,
                mem0_memory_ids=[],
            )
        self.stdout.write(self.style.SUCCESS("user_memories_deleted=true"))
