import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from history.models import AccountIdentity, ClientSession


_INTERNAL_REVOCATION_REASONS = frozenset(
    {
        ClientSession.RevocationReason.SIGNED_OUT,
        ClientSession.RevocationReason.SESSION_REVOKED,
        ClientSession.RevocationReason.SUPERSEDED,
        ClientSession.RevocationReason.CREDENTIAL_CHANGED,
        ClientSession.RevocationReason.ACCOUNT_DISABLED,
        ClientSession.RevocationReason.ACCOUNT_REVOKED,
    }
)
_SESSION_REVOCATION_CODES = {
    ClientSession.RevocationReason.SIGNED_OUT: ClientSession.RevocationReason.SESSION_REVOKED,
    ClientSession.RevocationReason.SESSION_REVOKED: ClientSession.RevocationReason.SESSION_REVOKED,
    ClientSession.RevocationReason.SUPERSEDED: ClientSession.RevocationReason.SESSION_REVOKED,
    ClientSession.RevocationReason.CREDENTIAL_CHANGED: (
        ClientSession.RevocationReason.SESSION_REVOKED
    ),
    ClientSession.RevocationReason.ACCOUNT_DISABLED: (
        ClientSession.RevocationReason.ACCOUNT_DISABLED
    ),
    ClientSession.RevocationReason.ACCOUNT_REVOKED: ClientSession.RevocationReason.ACCOUNT_REVOKED,
}


@dataclass(frozen=True)
class IssuedClientSession:
    access_token: str
    record: ClientSession


@dataclass(frozen=True)
class ClientSessionResolution:
    record: ClientSession | None
    code: str | None
    explicit_revocation: bool


class ClientSessionIssuanceError(ValueError):
    pass


class ClientSessionRateLimitError(Exception):
    def __init__(self, retry_after_seconds: int):
        super().__init__("client_session_rate_limited")
        self.retry_after_seconds = retry_after_seconds


def credential_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def account_identity_for_user(user) -> AccountIdentity:
    identity, _ = AccountIdentity.objects.get_or_create(user=user)
    return identity


# SQLite has no row locks (SELECT ... FOR UPDATE is a silent no-op), so
# serialization comes from the connection's IMMEDIATE transaction mode: every
# atomic block takes the single database write lock at BEGIN.  Inside such a
# block a plain re-read observes the latest committed state, which is all the
# check-then-write transactions below need.
def _current_user(user):
    return get_user_model().objects.get(pk=user.pk)


def _current_account_identity_for_user(user) -> AccountIdentity:
    account = account_identity_for_user(user)
    return AccountIdentity.objects.get(pk=account.pk)


def _credential_binding_current(record: ClientSession) -> bool:
    digest = record.auth_state_digest
    if not digest:
        return True
    user = record.account.user
    if constant_time_compare(digest, user.get_session_auth_hash()):
        return True
    return any(
        constant_time_compare(digest, fallback)
        for fallback in user.get_session_auth_fallback_hash()
    )


def enforce_credential_binding(session: ClientSession) -> ClientSession:
    """Terminally revoke a still-active session whose password binding is stale."""
    if session.revoked_at is None and not _credential_binding_current(session):
        return revoke_client_session(
            session=session,
            reason=ClientSession.RevocationReason.CREDENTIAL_CHANGED,
        )
    return session


def _enforce_issuance_rate_cap(*, account, now) -> None:
    window = timedelta(seconds=settings.CLIENT_SESSION_ISSUANCE_RATE_WINDOW_SECONDS)
    recent = ClientSession.objects.filter(
        account=account, created_at__gte=now - window
    )
    if recent.count() < settings.CLIENT_SESSION_ISSUANCE_RATE_LIMIT:
        return
    oldest = recent.order_by("created_at").values_list("created_at", flat=True).first()
    retry_after = max(1, int((oldest + window - now).total_seconds()) + 1)
    raise ClientSessionRateLimitError(retry_after)


@transaction.atomic
def issue_client_session(*, user, installation_id, client_version) -> IssuedClientSession:
    user = _current_user(user)
    account = _current_account_identity_for_user(user)
    if not user.is_active:
        raise ClientSessionIssuanceError("account_disabled")
    if account.state == AccountIdentity.State.REVOKED:
        raise ClientSessionIssuanceError("account_revoked")
    now = timezone.now()
    _enforce_issuance_rate_cap(account=account, now=now)
    superseded = ClientSession.objects.filter(
        account=account,
        installation_id=installation_id,
        revoked_at__isnull=True,
    ).order_by("pk")
    for prior in superseded:
        _revoke_active_client_session(
            session=prior,
            reason=ClientSession.RevocationReason.SUPERSEDED,
        )
    access_token = secrets.token_urlsafe(32)
    record = ClientSession.objects.create(
        account=account,
        installation_id=installation_id,
        credential_digest=credential_digest(access_token),
        client_version=client_version,
        created_at=now,
        last_seen_at=now,
        auth_state_digest=user.get_session_auth_hash(),
    )
    return IssuedClientSession(access_token=access_token, record=record)


def _explicit_revocation(record: ClientSession) -> ClientSessionResolution | None:
    if not record.account.user.is_active:
        return ClientSessionResolution(record, "account_disabled", True)
    if record.account.state == AccountIdentity.State.REVOKED:
        return ClientSessionResolution(record, "account_revoked", True)
    if record.revoked_at is not None:
        code = _SESSION_REVOCATION_CODES.get(
            record.revocation_reason,
            ClientSession.RevocationReason.SESSION_REVOKED,
        )
        return ClientSessionResolution(record, code, True)
    return None


def resolve_client_session(*, token, installation_id) -> ClientSessionResolution:
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        return ClientSessionResolution(None, "invalid_session_credential", False)

    record = (
        ClientSession.objects.select_related("account__user")
        .filter(credential_digest=credential_digest(token))
        .first()
    )
    if record is None or record.installation_id != installation_id:
        return ClientSessionResolution(None, "invalid_session_credential", False)

    revocation = _explicit_revocation(record)
    if revocation is not None:
        return revocation
    if not _credential_binding_current(record):
        record = enforce_credential_binding(record)
        record = ClientSession.objects.select_related("account__user").get(pk=record.pk)
        return ClientSessionResolution(
            record,
            _SESSION_REVOCATION_CODES[ClientSession.RevocationReason.CREDENTIAL_CHANGED],
            True,
        )

    # Status polling is read-mostly: only refresh last_seen_at once it has
    # aged past the configured interval, with a conditional update that
    # cannot resurrect a session revoked between the read and the write.
    last_seen_at = timezone.now()
    min_interval = timedelta(
        seconds=settings.CLIENT_SESSION_LAST_SEEN_MIN_INTERVAL_SECONDS
    )
    if record.last_seen_at > last_seen_at - min_interval:
        return ClientSessionResolution(record, None, False)
    updated = ClientSession.objects.filter(
        pk=record.pk,
        revoked_at__isnull=True,
        last_seen_at__lte=last_seen_at - min_interval,
        account__state=AccountIdentity.State.ACTIVE,
        account__user__is_active=True,
    ).update(last_seen_at=last_seen_at)

    record = ClientSession.objects.select_related("account__user").get(pk=record.pk)
    revocation = _explicit_revocation(record)
    if revocation is not None:
        return revocation
    if updated == 1:
        return ClientSessionResolution(record, None, False)
    return ClientSessionResolution(None, "invalid_session_credential", False)


def _validate_revocation_reason(reason: str) -> None:
    if reason not in _INTERNAL_REVOCATION_REASONS:
        raise ValueError("Unsupported client session revocation reason")


def _revoke_active_client_session(*, session: ClientSession, reason: str) -> bool:
    updated = ClientSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
        revoked_at=timezone.now(),
        revocation_reason=reason,
    )
    from history.trace_tokens import revoke_client_session_trace_tokens

    revoke_client_session_trace_tokens(client_session=session)
    return bool(updated)


@transaction.atomic
def revoke_client_session(*, session, reason) -> ClientSession:
    _validate_revocation_reason(reason)
    current = ClientSession.objects.select_related("account").get(pk=session.pk)
    _revoke_active_client_session(session=current, reason=reason)
    session.refresh_from_db()
    return session


@transaction.atomic
def revoke_account_sessions(*, account, reason) -> int:
    _validate_revocation_reason(reason)
    user = _current_user(account.user)
    account = _current_account_identity_for_user(user)
    sessions = list(ClientSession.objects.filter(account=account).order_by("pk"))
    revoked = 0
    for session in sessions:
        revoked += _revoke_active_client_session(session=session, reason=reason)
    return revoked


@transaction.atomic
def disable_account(*, user) -> AccountIdentity:
    user = _current_user(user)
    account = _current_account_identity_for_user(user)
    if user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active"])
    revoke_account_sessions(
        account=account,
        reason=ClientSession.RevocationReason.ACCOUNT_DISABLED,
    )
    return account


@transaction.atomic
def revoke_account(*, account) -> AccountIdentity:
    user = _current_user(account.user)
    account = _current_account_identity_for_user(user)
    if account.state == AccountIdentity.State.ACTIVE:
        account.state = AccountIdentity.State.REVOKED
        account.revoked_at = timezone.now()
        account.revocation_reason = ClientSession.RevocationReason.ACCOUNT_REVOKED
        account.save(update_fields=["state", "revoked_at", "revocation_reason"])
    revoke_account_sessions(
        account=account,
        reason=ClientSession.RevocationReason.ACCOUNT_REVOKED,
    )
    return account
