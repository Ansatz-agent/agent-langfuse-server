from django.conf import settings
from django.db import models


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
