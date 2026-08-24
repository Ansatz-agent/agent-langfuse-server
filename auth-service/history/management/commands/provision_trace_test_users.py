from __future__ import annotations

import os
import secrets
from pathlib import Path
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

TEST_IDENTITIES = (
    ("A", "trace-e2e-a-20260823", "trace-e2e-a-20260823@c2sml.invalid"),
    ("B", "trace-e2e-b-20260823", "trace-e2e-b-20260823@c2sml.invalid"),
)


class Command(BaseCommand):
    help = "Provision two non-admin users for the controlled Voice Trace E2E run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=Path,
            required=True,
            help="New owner-only credential file. Existing paths are never overwritten.",
        )

    def handle(self, *args, **options):
        output = options["output"].expanduser()
        if output.exists() or output.is_symlink():
            raise CommandError("credential output already exists")
        if not output.parent.is_dir():
            raise CommandError("credential output parent does not exist")

        user_model = get_user_model()
        existing = {
            user.username: user
            for user in user_model.objects.filter(
                username__in=[username for _, username, _ in TEST_IDENTITIES]
            )
        }
        if any(user.is_staff or user.is_superuser for user in existing.values()):
            raise CommandError("refusing to repurpose a privileged existing identity")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        file_descriptor = None
        try:
            file_descriptor = os.open(output, flags, 0o600)
            os.fchmod(file_descriptor, 0o600)
            credentials = []
            with transaction.atomic():
                for label, username, email in TEST_IDENTITIES:
                    password = secrets.token_urlsafe(32)
                    installation_id = uuid4()
                    user, _ = user_model.objects.get_or_create(
                        username=username,
                        defaults={"email": email},
                    )
                    if user.is_staff or user.is_superuser:
                        raise CommandError(
                            "refusing to repurpose a privileged existing identity"
                        )
                    user.email = email
                    user.is_active = True
                    user.is_staff = False
                    user.is_superuser = False
                    user.set_password(password)
                    user.save(
                        update_fields=[
                            "email",
                            "is_active",
                            "is_staff",
                            "is_superuser",
                            "password",
                        ]
                    )
                    credentials.extend(
                        (
                            f"USER_{label}_ID={user.pk}",
                            f"USER_{label}_USERNAME={username}",
                            f"USER_{label}_EMAIL={email}",
                            f"USER_{label}_PASSWORD={password}",
                            f"USER_{label}_INSTALLATION_ID={installation_id}",
                        )
                    )

                payload = "\n".join(
                    ["AUTH_BASE_URL=https://c2sml.cn/auth", *credentials, ""]
                )
                with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                    file_descriptor = None
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
        except Exception:
            if file_descriptor is not None:
                os.close(file_descriptor)
            output.unlink(missing_ok=True)
            raise

        self.stdout.write(
            self.style.SUCCESS(
                "Provisioned two active non-admin Trace E2E users; "
                "credentials were written to an owner-only file."
            )
        )
