import hashlib
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from history.client_sessions import (
    account_identity_for_user,
    issue_client_session,
    resolve_client_session,
    revoke_account_sessions,
    revoke_client_session,
)
from history.models import AccountIdentity, ClientSession


INSTALLATION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


class ClientSessionServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice")

    def test_issue_resolve_revoke_preserves_digest_only_evidence(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )

        self.assertEqual(
            issued.record.credential_digest,
            hashlib.sha256(issued.access_token.encode()).hexdigest(),
        )
        self.assertNotEqual(issued.record.credential_digest, issued.access_token)
        active = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )
        self.assertEqual(
            (active.record, active.code, active.explicit_revocation),
            (issued.record, None, False),
        )
        revoke_client_session(session=issued.record, reason="session_revoked")
        revoked = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )
        self.assertEqual(
            (revoked.record, revoked.code, revoked.explicit_revocation),
            (issued.record, "session_revoked", True),
        )

    def test_installation_mismatch_is_not_explicit_revocation(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )

        resolution = resolve_client_session(
            token=issued.access_token,
            installation_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        )

        self.assertEqual(
            (resolution.record, resolution.code, resolution.explicit_revocation),
            (None, "invalid_session_credential", False),
        )

    def test_malformed_token_is_not_explicit_revocation(self):
        for token in (None, "x" * 31, "x" * 129):
            with self.subTest(token=token):
                resolution = resolve_client_session(
                    token=token,
                    installation_id=INSTALLATION_ID,
                )
                self.assertEqual(
                    (
                        resolution.record,
                        resolution.code,
                        resolution.explicit_revocation,
                    ),
                    (None, "invalid_session_credential", False),
                )

    def test_inactive_user_is_explicitly_account_disabled(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        resolution = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )

        self.assertEqual(
            (resolution.record, resolution.code, resolution.explicit_revocation),
            (issued.record, "account_disabled", True),
        )

    def test_revoked_account_identity_is_explicitly_account_revoked(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        issued.record.account.state = AccountIdentity.State.REVOKED
        issued.record.account.revoked_at = timezone.now()
        issued.record.account.revocation_reason = "account_revoked"
        issued.record.account.save(
            update_fields=["state", "revoked_at", "revocation_reason"]
        )

        resolution = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )

        self.assertEqual(
            (resolution.record, resolution.code, resolution.explicit_revocation),
            (issued.record, "account_revoked", True),
        )

    def test_signed_out_evidence_is_reported_as_session_revoked(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )

        revoke_client_session(
            session=issued.record,
            reason=ClientSession.RevocationReason.SIGNED_OUT,
        )
        resolution = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )

        self.assertEqual(issued.record.revocation_reason, "signed_out")
        self.assertEqual(
            (resolution.record, resolution.code, resolution.explicit_revocation),
            (issued.record, "session_revoked", True),
        )

    def test_revoking_one_session_does_not_revoke_second_session(self):
        first = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        second = issue_client_session(
            user=self.user,
            installation_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
            client_version="0.17.1",
        )

        revoke_client_session(
            session=first.record,
            reason=ClientSession.RevocationReason.SESSION_REVOKED,
        )

        first_resolution = resolve_client_session(
            token=first.access_token,
            installation_id=first.record.installation_id,
        )
        second_resolution = resolve_client_session(
            token=second.access_token,
            installation_id=second.record.installation_id,
        )
        self.assertEqual(first_resolution.code, "session_revoked")
        self.assertIsNone(second_resolution.code)
        self.assertEqual(ClientSession.objects.count(), 2)
        self.assertEqual(first.record.account, second.record.account)

    def test_account_revocation_retains_and_revokes_every_active_session(self):
        first = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        second = issue_client_session(
            user=self.user,
            installation_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
            client_version="0.17.1",
        )

        updated = revoke_account_sessions(
            account=account_identity_for_user(self.user),
            reason=ClientSession.RevocationReason.ACCOUNT_REVOKED,
        )

        self.assertEqual(updated, 2)
        self.assertEqual(ClientSession.objects.count(), 2)
        self.assertEqual(
            set(ClientSession.objects.values_list("revocation_reason", flat=True)),
            {"account_revoked"},
        )
        self.assertIsNotNone(ClientSession.objects.get(pk=first.record.pk).revoked_at)
        self.assertIsNotNone(ClientSession.objects.get(pk=second.record.pk).revoked_at)

    def test_later_account_revoke_does_not_overwrite_session_revocation_evidence(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        revoke_client_session(
            session=issued.record,
            reason=ClientSession.RevocationReason.SIGNED_OUT,
        )
        signed_out_at = issued.record.revoked_at

        updated = revoke_account_sessions(
            account=issued.record.account,
            reason=ClientSession.RevocationReason.ACCOUNT_REVOKED,
        )

        issued.record.refresh_from_db()
        self.assertEqual(updated, 0)
        self.assertEqual(issued.record.revocation_reason, "signed_out")
        self.assertEqual(issued.record.revoked_at, signed_out_at)

    def test_active_resolution_updates_last_seen(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        previous = timezone.now() - timedelta(days=1)
        ClientSession.objects.filter(pk=issued.record.pk).update(last_seen_at=previous)

        resolution = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )

        self.assertGreater(resolution.record.last_seen_at, previous)

    def test_session_token_is_not_logged(self):
        with self.assertNoLogs(level=0):
            issued = issue_client_session(
                user=self.user,
                installation_id=INSTALLATION_ID,
                client_version="0.17.0",
            )
            resolve_client_session(
                token=issued.access_token,
                installation_id=INSTALLATION_ID,
            )
            revoke_client_session(
                session=issued.record,
                reason=ClientSession.RevocationReason.SESSION_REVOKED,
            )
