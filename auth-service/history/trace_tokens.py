from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from datetime import timezone as datetime_timezone
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AccountIdentity, ClientSession, TraceUploadToken


@dataclass(frozen=True)
class IssuedTraceToken:
    access_token: str
    record: TraceUploadToken
    rotated: bool


@dataclass(frozen=True)
class TraceTokenIntrospection:
    record: TraceUploadToken | None
    reason: str
    explicit_revocation: bool
    revocation: TraceTokenRevocation | None = None


@dataclass(frozen=True)
class TraceTokenRevocation:
    account_id: str
    session_id: str
    installation_id: str
    revoked_at: str


class TraceTokenIssuanceError(ValueError):
    pass


class _TraceTokenAuthorityUnavailable(Exception):
    pass


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def session_key_digest(value: str) -> str:
    return token_digest(value)


def _active_client_session(client_session: ClientSession) -> ClientSession:
    # Runs inside the issuing IMMEDIATE transaction, so this re-read observes
    # the latest committed state; SQLite offers no row locks to take instead.
    from history.client_sessions import enforce_credential_binding

    session = ClientSession.objects.select_related("account__user").get(
        pk=client_session.pk
    )
    if not session.account.user.is_active:
        raise TraceTokenIssuanceError("account_disabled")
    if session.account.state == AccountIdentity.State.REVOKED:
        raise TraceTokenIssuanceError("account_revoked")
    session = enforce_credential_binding(session)
    if session.revoked_at is not None:
        raise TraceTokenIssuanceError("session_revoked")
    return session


def _trace_token_authority(*, client_session, user, session_key, installation_id):
    native = client_session is not None
    legacy_values = (user, session_key, installation_id)
    if native:
        if any(value is not None for value in legacy_values):
            raise ValueError("Trace token issuance requires exactly one authority form")
        session = _active_client_session(client_session)
        return session.account.user, session.credential_digest, session.installation_id, session
    if any(value is None for value in legacy_values) or not isinstance(session_key, str):
        raise ValueError("Trace token issuance requires exactly one authority form")
    return user, session_key_digest(session_key), installation_id, None


@transaction.atomic
def issue_trace_token(
    *,
    client_session: ClientSession | None = None,
    user=None,
    session_key: str | None = None,
    installation_id: UUID | None = None,
):
    now = timezone.now()
    user, session_digest, installation_id, bound_session = _trace_token_authority(
        client_session=client_session,
        user=user,
        session_key=session_key,
        installation_id=installation_id,
    )
    if bound_session is None:
        current = TraceUploadToken.objects.filter(
            user=user,
            session_key_digest=session_digest,
            installation_id=installation_id,
            revoked_at__isnull=True,
        )
    else:
        current = TraceUploadToken.objects.filter(
            client_session=bound_session,
            revoked_at__isnull=True,
        )
    rotated = current.exists()
    current.update(
        revoked_at=now,
        revocation_reason=TraceUploadToken.RevocationReason.ROTATED,
    )
    access_token = secrets.token_urlsafe(32)
    record = TraceUploadToken.objects.create(
        digest=token_digest(access_token),
        user=user,
        session_key_digest=session_digest,
        installation_id=installation_id,
        client_session=bound_session,
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


def _native_binding(record: TraceUploadToken) -> ClientSession | None:
    if record.client_session_id is None:
        missing_relation = ClientSession.objects.filter(
            credential_digest=record.session_key_digest,
            installation_id=record.installation_id,
        ).exists()
        if missing_relation:
            raise _TraceTokenAuthorityUnavailable
        return None
    session = record.client_session
    if (
        record.user_id != session.account.user_id
        or record.installation_id != session.installation_id
        or record.session_key_digest != session.credential_digest
    ):
        raise _TraceTokenAuthorityUnavailable
    return session


def _revocation_evidence(
    session: ClientSession,
) -> tuple[str, TraceTokenRevocation] | None:
    reason = None
    revoked_at = None
    if session.revoked_at is not None:
        reason = {
            ClientSession.RevocationReason.SIGNED_OUT: "session_revoked",
            ClientSession.RevocationReason.SESSION_REVOKED: "session_revoked",
            ClientSession.RevocationReason.SUPERSEDED: "session_revoked",
            ClientSession.RevocationReason.CREDENTIAL_CHANGED: "session_revoked",
            ClientSession.RevocationReason.ACCOUNT_DISABLED: "account_disabled",
            ClientSession.RevocationReason.ACCOUNT_REVOKED: "account_revoked",
        }.get(session.revocation_reason)
        revoked_at = session.revoked_at
        if reason is None:
            raise _TraceTokenAuthorityUnavailable
    elif session.account.state == AccountIdentity.State.REVOKED:
        if (
            session.account.revocation_reason != ClientSession.RevocationReason.ACCOUNT_REVOKED
            or session.account.revoked_at is None
        ):
            raise _TraceTokenAuthorityUnavailable
        reason = "account_revoked"
        revoked_at = session.account.revoked_at
    elif not session.account.user.is_active:
        # User has no durable disabled-at field.  The administrative service
        # transaction records that evidence on every still-active Session;
        # without it there is no trustworthy timestamp to expose.
        raise _TraceTokenAuthorityUnavailable
    else:
        return None
    if not timezone.is_aware(revoked_at):
        raise _TraceTokenAuthorityUnavailable
    evidence = TraceTokenRevocation(
        account_id=str(session.account.account_id),
        session_id=str(session.session_id),
        installation_id=str(session.installation_id),
        revoked_at=revoked_at.astimezone(datetime_timezone.utc).isoformat(),
    )
    return reason, evidence


def introspect_trace_token(value: str) -> TraceTokenIntrospection:
    if not isinstance(value, str) or not 32 <= len(value) <= 128:
        return TraceTokenIntrospection(None, "invalid_token", False)
    record = (
        TraceUploadToken.objects.select_related(
            "user__account_identity",
            "client_session__account__user",
        )
        .filter(digest=token_digest(value))
        .first()
    )
    if record is None:
        return TraceTokenIntrospection(None, "invalid_token", False)
    now = timezone.now()
    try:
        binding = _native_binding(record)
        if (
            binding is not None
            and binding.revoked_at is None
            and binding.account.state == AccountIdentity.State.ACTIVE
            and binding.account.user.is_active
        ):
            from history.client_sessions import enforce_credential_binding

            binding = enforce_credential_binding(binding)
        revocation = _revocation_evidence(binding) if binding is not None else None
    except _TraceTokenAuthorityUnavailable:
        return TraceTokenIntrospection(None, "authentication_unavailable", False)
    if binding is not None:
        if revocation is not None:
            reason, evidence = revocation
            return TraceTokenIntrospection(record, reason, True, evidence)
    else:
        legacy_account, _ = AccountIdentity.objects.get_or_create(user=record.user)
        if not record.user.is_active:
            return TraceTokenIntrospection(record, "account_disabled", True)
        if legacy_account.state == AccountIdentity.State.REVOKED:
            return TraceTokenIntrospection(record, "account_revoked", True)
    if record.expires_at <= now:
        return TraceTokenIntrospection(record, "token_expired", False)
    if record.revoked_at is not None:
        reason = (
            "token_rotated"
            if record.revocation_reason == TraceUploadToken.RevocationReason.ROTATED
            else "token_revoked"
        )
        return TraceTokenIntrospection(record, reason, False)
    if (
        record.scope != settings.TRACE_UPLOAD_TOKEN_SCOPE
        or record.audience != settings.TRACE_UPLOAD_TOKEN_AUDIENCE
    ):
        return TraceTokenIntrospection(record, "invalid_token", False)
    return TraceTokenIntrospection(record, "active", False)


def revoke_session_trace_tokens(*, user, session_key: str) -> int:
    if not session_key:
        return 0
    return TraceUploadToken.objects.filter(
        user=user,
        session_key_digest=session_key_digest(session_key),
        revoked_at__isnull=True,
    ).update(
        revoked_at=timezone.now(),
        revocation_reason=TraceUploadToken.RevocationReason.REVOKED,
    )


def revoke_device_trace_tokens(*, user, installation_id: UUID) -> int:
    return TraceUploadToken.objects.filter(
        user=user,
        installation_id=installation_id,
        revoked_at__isnull=True,
    ).update(
        revoked_at=timezone.now(),
        revocation_reason=TraceUploadToken.RevocationReason.REVOKED,
    )


def revoke_client_session_trace_tokens(*, client_session: ClientSession) -> int:
    return TraceUploadToken.objects.filter(
        client_session=client_session,
        revoked_at__isnull=True,
    ).update(
        revoked_at=timezone.now(),
        revocation_reason=TraceUploadToken.RevocationReason.REVOKED,
    )
