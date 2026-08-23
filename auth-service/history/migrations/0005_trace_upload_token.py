import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("history", "0004_historysession_usage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TraceUploadToken",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "token_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("digest", models.CharField(max_length=64, unique=True)),
                ("session_key_digest", models.CharField(max_length=64)),
                ("installation_id", models.UUIDField()),
                (
                    "scope",
                    models.CharField(default="trace:write", max_length=64),
                ),
                (
                    "audience",
                    models.CharField(
                        default="ansatz-trace-gateway",
                        max_length=128,
                    ),
                ),
                ("created_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="trace_upload_tokens",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["digest"], name="trace_token_digest_idx"),
                    models.Index(
                        fields=["expires_at"],
                        name="trace_token_expiry_idx",
                    ),
                    models.Index(
                        fields=["user", "installation_id"],
                        name="trace_token_user_install_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(revoked_at__isnull=True),
                        fields=(
                            "user",
                            "session_key_digest",
                            "installation_id",
                        ),
                        name="unique_active_trace_token_session_install",
                    )
                ],
            },
        )
    ]
