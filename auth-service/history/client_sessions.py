import hashlib
import secrets
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from history.models import AccountIdentity, ClientSession


_INTERNAL_REVOCATION_REASONS = frozenset(
    {
        ClientSession.RevocationReason.SIGNED_OUT,
        ClientSession.RevocationReason.SESSION_REVOKED,
        ClientSession.RevocationReason.ACCOUNT_DISABLED,
        ClientSession.RevocationReason.ACCOUNT_REVOKED,
    }
)
_SESSION_REVOCATION_CODES = {
    ClientSession.RevocationReason.SIGNED_OUT: ClientSession.RevocationReason.SESSION_REVOKED,
    ClientSession.RevocationReason.SESSION_REVOKED: ClientSession.RevocationReason.SESSION_REVOKED,
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


def credential_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def account_identity_for_user(user) -> AccountIdentity:
    identity, _ = AccountIdentity.objects.get_or_create(user=user)
    return identity


@transaction.atomic
def issue_client_session(*, user, installation_id, client_version) -> IssuedClientSession:
    account = account_identity_for_user(user)
    access_token = secrets.token_urlsafe(32)
    now = timezone.now()
    record = ClientSession.objects.create(
        account=account,
        installation_id=installation_id,
        credential_digest=credential_digest(access_token),
        client_version=client_version,
        created_at=now,
        last_seen_at=now,
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

    last_seen_at = timezone.now()
    updated = ClientSession.objects.filter(
        pk=record.pk,
        revoked_at__isnull=True,
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


@transaction.atomic
def revoke_client_session(*, session, reason) -> ClientSession:
    _validate_revocation_reason(reason)
    ClientSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
        revoked_at=timezone.now(),
        revocation_reason=reason,
    )
    session.refresh_from_db()
    return session


@transaction.atomic
def revoke_account_sessions(*, account, reason) -> int:
    _validate_revocation_reason(reason)
    return ClientSession.objects.filter(
        account=account,
        revoked_at__isnull=True,
    ).update(
        revoked_at=timezone.now(),
        revocation_reason=reason,
    )
