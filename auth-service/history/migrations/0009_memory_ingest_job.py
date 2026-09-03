import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("history", "0008_client_session_auth_binding"),
    ]

    operations = [
        migrations.CreateModel(
            name="MemoryIngestJob",
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
                    "source_key",
                    models.CharField(max_length=128, unique=True),
                ),
                ("message_ids", models.JSONField(default=list)),
                ("chunk_index", models.PositiveIntegerField(default=0)),
                ("chunk_count", models.PositiveIntegerField(default=1)),
                ("content_sha256", models.CharField(max_length=64)),
                ("redaction_version", models.CharField(default="v1", max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("deleted", "Deleted"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("mem0_memory_ids", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="memory_ingest_jobs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="memory_ingest_jobs",
                        to="history.historysession",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
                "indexes": [
                    models.Index(
                        fields=["status", "next_attempt_at"],
                        name="memory_job_status_idx",
                    ),
                    models.Index(
                        fields=["owner", "session"],
                        name="memory_job_owner_session_idx",
                    ),
                ],
            },
        ),
    ]
