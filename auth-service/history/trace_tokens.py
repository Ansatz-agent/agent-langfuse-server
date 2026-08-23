from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import TraceUploadToken


@dataclass(frozen=True)
class IssuedTraceToken:
    access_token: str
    record: TraceUploadToken
    rotated: bool


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def session_key_digest(value: str) -> str:
    return token_digest(value)


@transaction.atomic
def issue_trace_token(*, user, session_key: str, installation_id: UUID):
    now = timezone.now()
    session_digest = session_key_digest(session_key)
    current = TraceUploadToken.objects.filter(
        user=user,
        session_key_digest=session_digest,
        installation_id=installation_id,
        revoked_at__isnull=True,
    )
    rotated = current.exists()
    current.update(revoked_at=now)
    access_token = secrets.token_urlsafe(32)
    record = TraceUploadToken.objects.create(
        digest=token_digest(access_token),
        user=user,
        session_key_digest=session_digest,
        installation_id=installation_id,
        scope=settings.TRACE_UPLOAD_TOKEN_SCOPE,
        audience=settings.TRACE_UPLOAD_TOKEN_AUDIENCE,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.TRACE_UPLOAD_TOKEN_TTL_SECONDS),
    )
    return IssuedTraceToken(
        access_token=access_token,
        record=record,
        rotated=rotated,
    )


def introspect_trace_token(value: str) -> TraceUploadToken | None:
    if not isinstance(value, str) or not 32 <= len(value) <= 128:
        return None
    record = (
        TraceUploadToken.objects.select_related("user")
        .filter(digest=token_digest(value))
        .first()
    )
    now = timezone.now()
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at <= now
        or not record.user.is_active
        or record.scope != settings.TRACE_UPLOAD_TOKEN_SCOPE
        or record.audience != settings.TRACE_UPLOAD_TOKEN_AUDIENCE
    ):
        return None
    return record


def revoke_session_trace_tokens(*, user, session_key: str) -> int:
    if not session_key:
        return 0
    return TraceUploadToken.objects.filter(
        user=user,
        session_key_digest=session_key_digest(session_key),
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())


def revoke_device_trace_tokens(*, user, installation_id: UUID) -> int:
    return TraceUploadToken.objects.filter(
        user=user,
        installation_id=installation_id,
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())
