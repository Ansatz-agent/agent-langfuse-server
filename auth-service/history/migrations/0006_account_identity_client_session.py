import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_account_identities(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    AccountIdentity = apps.get_model("history", "AccountIdentity")

    for user in User.objects.order_by("pk").iterator():
        AccountIdentity.objects.create(
            account_id=uuid.uuid4(),
            user_id=user.pk,
            state="active",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("history", "0005_trace_upload_token"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountIdentity",
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
                    "account_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[("active", "Active"), ("revoked", "Revoked")],
                        default="active",
                        max_length=16,
                    ),
                ),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("revocation_reason", models.CharField(blank=True, max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="account_identity",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ClientSession",
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
                    "session_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("installation_id", models.UUIDField()),
                ("credential_digest", models.CharField(max_length=64, unique=True)),
                ("client_version", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "revocation_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("signed_out", "Signed out"),
                            ("session_revoked", "Session revoked"),
                            ("account_disabled", "Account disabled"),
                            ("account_revoked", "Account revoked"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="client_sessions",
                        to="history.accountidentity",
                    ),
                ),
            ],
        ),
        migrations.RunPython(
            backfill_account_identities,
            migrations.RunPython.noop,
        ),
    ]
