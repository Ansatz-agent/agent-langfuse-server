import hashlib
import secrets
from dataclasses import dataclass

from django.contrib.auth import get_user_model
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


class ClientSessionIssuanceError(ValueError):
    pass


def credential_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def account_identity_for_user(user) -> AccountIdentity:
    identity, _ = AccountIdentity.objects.get_or_create(user=user)
    return identity


def _locked_user(user):
    return get_user_model().objects.select_for_update().get(pk=user.pk)


def _locked_account_identity_for_user(user) -> AccountIdentity:
    account = account_identity_for_user(user)
    return AccountIdentity.objects.select_for_update().get(pk=account.pk)


@transaction.atomic
def issue_client_session(*, user, installation_id, client_version) -> IssuedClientSession:
    user = _locked_user(user)
    account = _locked_account_identity_for_user(user)
    if not user.is_active:
        raise ClientSessionIssuanceError("account_disabled")
    if account.state == AccountIdentity.State.REVOKED:
        raise ClientSessionIssuanceError("account_revoked")
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


def _locked_client_session(session: ClientSession) -> ClientSession:
    initial = ClientSession.objects.select_related("account").get(pk=session.pk)
    user = _locked_user(initial.account.user)
    _locked_account_identity_for_user(user)
    return ClientSession.objects.select_for_update().get(pk=session.pk)


def _revoke_locked_client_session(*, session: ClientSession, reason: str) -> bool:
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
    locked_session = _locked_client_session(session)
    _revoke_locked_client_session(session=locked_session, reason=reason)
    session.refresh_from_db()
    return session


@transaction.atomic
def revoke_account_sessions(*, account, reason) -> int:
    _validate_revocation_reason(reason)
    user = _locked_user(account.user)
    account = _locked_account_identity_for_user(user)
    sessions = list(
        ClientSession.objects.select_for_update()
        .filter(account=account)
        .order_by("pk")
    )
    revoked = 0
    for session in sessions:
        revoked += _revoke_locked_client_session(session=session, reason=reason)
    return revoked


@transaction.atomic
def disable_account(*, user) -> AccountIdentity:
    user = _locked_user(user)
    account = _locked_account_identity_for_user(user)
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
    user = _locked_user(account.user)
    account = _locked_account_identity_for_user(user)
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
