import hashlib
import secrets
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from history.models import AccountIdentity, ClientSession


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

    if not record.account.user.is_active:
        return ClientSessionResolution(record, "account_disabled", True)
    if record.account.state == AccountIdentity.State.REVOKED:
        return ClientSessionResolution(record, "account_revoked", True)
    if record.revoked_at is not None:
        code = record.revocation_reason
        if code == ClientSession.RevocationReason.SIGNED_OUT:
            code = ClientSession.RevocationReason.SESSION_REVOKED
        return ClientSessionResolution(record, code, True)

    record.last_seen_at = timezone.now()
    record.save(update_fields=["last_seen_at"])
    return ClientSessionResolution(record, None, False)


@transaction.atomic
def revoke_client_session(*, session, reason) -> ClientSession:
    ClientSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
        revoked_at=timezone.now(),
        revocation_reason=reason,
    )
    session.refresh_from_db()
    return session


@transaction.atomic
def revoke_account_sessions(*, account, reason) -> int:
    return ClientSession.objects.filter(
        account=account,
        revoked_at__isnull=True,
    ).update(
        revoked_at=timezone.now(),
        revocation_reason=reason,
    )
