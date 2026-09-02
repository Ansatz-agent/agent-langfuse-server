import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AccountIdentity(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"

    account_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="account_identity",
    )
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.ACTIVE,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            persisted_account_id = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("account_id", flat=True)
                .first()
            )
            if (
                persisted_account_id is not None
                and persisted_account_id != self.account_id
            ):
                raise ValidationError({"account_id": "Account ID cannot be changed."})
        return super().save(*args, **kwargs)


class ClientSession(models.Model):
    class RevocationReason(models.TextChoices):
        SIGNED_OUT = "signed_out", "Signed out"
        SESSION_REVOKED = "session_revoked", "Session revoked"
        SUPERSEDED = "superseded", "Superseded by reissue"
        CREDENTIAL_CHANGED = "credential_changed", "Credential changed"
        ACCOUNT_DISABLED = "account_disabled", "Account disabled"
        ACCOUNT_REVOKED = "account_revoked", "Account revoked"

    session_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    account = models.ForeignKey(
        AccountIdentity,
        on_delete=models.PROTECT,
        related_name="client_sessions",
    )
    installation_id = models.UUIDField()
    credential_digest = models.CharField(max_length=64, unique=True)
    # Keyed HMAC of the user's password state (Django's session auth hash),
    # never a credential itself; a password change makes it stale.
    auth_state_digest = models.CharField(max_length=64, blank=True, default="")
    client_version = models.CharField(max_length=64)
    created_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(
        max_length=32,
        choices=RevocationReason.choices,
        blank=True,
    )

    def save(self, *args, **kwargs):
        if self.pk:
            persisted = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("session_id", "installation_id")
                .first()
            )
            if persisted is not None and persisted["session_id"] != self.session_id:
                raise ValidationError({"session_id": "Session ID cannot be changed."})
            if (
                persisted is not None
                and persisted["installation_id"] != self.installation_id
            ):
                raise ValidationError(
                    {"installation_id": "Installation ID cannot be changed."}
                )
        return super().save(*args, **kwargs)


class HistorySessionQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if user.is_superuser:
            return self
        return self.filter(owner=user)


class HistorySession(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="history_sessions",
    )
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_history_sessions",
    )
    parent_session = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="subagent_threads",
        null=True,
        blank=True,
    )
    external_id = models.CharField(max_length=255)
    title = models.CharField(max_length=500, blank=True)
    source = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=100, blank=True)
    message_count = models.PositiveIntegerField(default=0)
    tool_call_count = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cache_read_tokens = models.PositiveBigIntegerField(default=0)
    cache_write_tokens = models.PositiveBigIntegerField(default=0)
    reasoning_tokens = models.PositiveBigIntegerField(default=0)
    raw_metadata = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    objects = HistorySessionQuerySet.as_manager()

    class Meta:
        ordering = ["-started_at", "-imported_at", "external_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "external_id"],
                name="unique_history_session_per_owner",
            )
        ]
        indexes = [
            models.Index(fields=["owner", "started_at"], name="history_owner_started_idx"),
            models.Index(fields=["owner", "title"], name="history_owner_title_idx"),
        ]

    def __str__(self):
        return self.title or self.external_id


class UserMemoryPool(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_pool",
    )
    memory_markdown = models.TextField(blank=True)
    user_markdown = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["owner_id"]

    def __str__(self):
        return f"Memory pool for {self.owner}"


class MemoryIngestJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        DELETED = "deleted", "Deleted"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="memory_ingest_jobs",
    )
    session = models.ForeignKey(
        "history.HistorySession",
        on_delete=models.PROTECT,
        related_name="memory_ingest_jobs",
    )
    source_key = models.CharField(max_length=128, unique=True)
    message_ids = models.JSONField(default=list)
    chunk_index = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=1)
    content_sha256 = models.CharField(max_length=64)
    redaction_version = models.CharField(max_length=32, default="v1")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    mem0_memory_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="memory_job_status_idx"),
            models.Index(fields=["owner", "session"], name="memory_job_owner_session_idx"),
        ]

    def __str__(self):
        return f"Memory job {self.source_key} ({self.status})"


class HistoryMessage(models.Model):
    session = models.ForeignKey(
        HistorySession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    source_message_id = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=50)
    content = models.TextField(blank=True)
    timestamp = models.DateTimeField(null=True, blank=True)
    tool_name = models.CharField(max_length=255, blank=True)
    tool_call_id = models.CharField(max_length=255, blank=True)
    tool_calls = models.JSONField(default=list, blank=True)
    raw_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["timestamp", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "source_message_id"],
                condition=~models.Q(source_message_id=""),
                name="unique_source_message_per_session",
            )
        ]
        indexes = [models.Index(fields=["session", "timestamp"], name="message_session_time_idx")]

    def __str__(self):
        return f"{self.role}: {self.content[:60]}"


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_import_batches",
    )
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_import_batches",
    )
    original_filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    imported_sessions = models.PositiveIntegerField(default=0)
    skipped_sessions = models.PositiveIntegerField(default=0)
    imported_messages = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "created_at"], name="import_owner_created_idx")]

    def __str__(self):
        return f"{self.original_filename} ({self.status})"


class TraceUploadToken(models.Model):
    class RevocationReason(models.TextChoices):
        ROTATED = "rotated", "Rotated"
        REVOKED = "revoked", "Revoked"

    token_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    digest = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trace_upload_tokens",
    )
    client_session = models.ForeignKey(
        ClientSession,
        on_delete=models.PROTECT,
        related_name="trace_upload_tokens",
        null=True,
        blank=True,
    )
    session_key_digest = models.CharField(max_length=64)
    installation_id = models.UUIDField()
    scope = models.CharField(max_length=64, default="trace:write")
    audience = models.CharField(max_length=128, default="ansatz-trace-gateway")
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(
        max_length=16,
        choices=RevocationReason.choices,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["digest"], name="trace_token_digest_idx"),
            models.Index(fields=["expires_at"], name="trace_token_expiry_idx"),
            models.Index(
                fields=["user", "installation_id"],
                name="trace_token_user_install_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "session_key_digest", "installation_id"],
                condition=models.Q(revoked_at__isnull=True),
                name="unique_active_trace_token_session_install",
            )
        ]

    def __str__(self):
        return str(self.token_id)
