import hashlib
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from history.client_sessions import (
    ClientSessionIssuanceError,
    account_identity_for_user,
    disable_account,
    issue_client_session,
    revoke_account,
    resolve_client_session,
    revoke_account_sessions,
    revoke_client_session,
)
from history.models import AccountIdentity, ClientSession, TraceUploadToken


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

    def test_persisted_session_installation_binding_cannot_be_changed(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        issued.record.installation_id = uuid.UUID(
            "22222222-2222-4222-8222-222222222222"
        )

        with self.assertRaises(ValidationError):
            issued.record.save()

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

    def test_well_formed_unknown_token_is_not_explicit_revocation(self):
        resolution = resolve_client_session(
            token="u" * 43,
            installation_id=INSTALLATION_ID,
        )

        self.assertEqual(
            (resolution.record, resolution.code, resolution.explicit_revocation),
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

    def test_disable_account_rejects_session_issuance_between_terminal_state_and_bulk_revoke(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        original_revoke = revoke_account_sessions

        def issue_during_terminal_transition(*, account, reason):
            with self.assertRaisesRegex(ClientSessionIssuanceError, "account_disabled"):
                issue_client_session(
                    user=self.user,
                    installation_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
                    client_version="0.17.1",
                )
            return original_revoke(account=account, reason=reason)

        with patch(
            "history.client_sessions.revoke_account_sessions",
            side_effect=issue_during_terminal_transition,
        ):
            disable_account(user=self.user)

        self.user.refresh_from_db()
        issued.record.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(issued.record.revocation_reason, "account_disabled")
        self.assertEqual(ClientSession.objects.count(), 1)

    def test_revoked_account_rejects_new_session_issuance(self):
        account = account_identity_for_user(self.user)
        revoke_account(account=account)

        with self.assertRaisesRegex(ClientSessionIssuanceError, "account_revoked"):
            issue_client_session(
                user=self.user,
                installation_id=INSTALLATION_ID,
                client_version="0.17.0",
            )

        self.assertEqual(ClientSession.objects.count(), 0)

    def test_terminal_account_transition_rolls_back_if_session_revocation_fails(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )

        with patch(
            "history.client_sessions.revoke_account_sessions",
            side_effect=RuntimeError("session revoke failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "session revoke failed"):
                revoke_account(account=issued.record.account)

        self.user.refresh_from_db()
        issued.record.account.refresh_from_db()
        issued.record.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(issued.record.account.state, "active")
        self.assertIsNone(issued.record.account.revoked_at)
        self.assertIsNone(issued.record.revoked_at)

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

    def test_terminal_state_precedence_is_user_then_account_then_session(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        revoke_client_session(
            session=issued.record,
            reason=ClientSession.RevocationReason.SIGNED_OUT,
        )
        account = issued.record.account
        account.state = AccountIdentity.State.REVOKED
        account.revoked_at = timezone.now()
        account.revocation_reason = ClientSession.RevocationReason.ACCOUNT_REVOKED
        account.save(update_fields=["state", "revoked_at", "revocation_reason"])
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        disabled = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )
        self.assertEqual(disabled.code, "account_disabled")

        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        account_revoked = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )
        self.assertEqual(account_revoked.code, "account_revoked")

        account.state = AccountIdentity.State.ACTIVE
        account.revoked_at = None
        account.revocation_reason = ""
        account.save(update_fields=["state", "revoked_at", "revocation_reason"])
        session_revoked = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )
        self.assertEqual(session_revoked.code, "session_revoked")

    def test_invalid_persisted_reason_does_not_escape_external_wire_vocabulary(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        ClientSession.objects.filter(pk=issued.record.pk).update(
            revoked_at=timezone.now(),
            revocation_reason="unexpected_internal_reason",
        )

        resolution = resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )

        self.assertEqual(
            (resolution.code, resolution.explicit_revocation),
            ("session_revoked", True),
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

    def test_session_and_account_revocations_retain_and_revoke_bound_trace_tokens(self):
        from history.trace_tokens import issue_trace_token

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
        first_token = issue_trace_token(client_session=first.record)
        second_token = issue_trace_token(client_session=second.record)

        revoke_client_session(
            session=first.record,
            reason=ClientSession.RevocationReason.SESSION_REVOKED,
        )
        first_token.record.refresh_from_db()
        second_token.record.refresh_from_db()
        self.assertEqual(first_token.record.revocation_reason, "revoked")
        self.assertIsNotNone(first_token.record.revoked_at)
        self.assertIsNone(second_token.record.revoked_at)

        revoke_account_sessions(
            account=first.record.account,
            reason=ClientSession.RevocationReason.ACCOUNT_REVOKED,
        )
        second_token.record.refresh_from_db()
        self.assertEqual(second_token.record.revocation_reason, "revoked")
        self.assertIsNotNone(second_token.record.revoked_at)
        self.assertEqual(TraceUploadToken.objects.count(), 2)

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

    def test_revocation_writers_reject_reasons_outside_internal_vocabulary(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )

        with self.assertRaises(ValueError):
            revoke_client_session(session=issued.record, reason="expired")
        with self.assertRaises(ValueError):
            revoke_account_sessions(account=issued.record.account, reason="expired")

        issued.record.refresh_from_db()
        self.assertIsNone(issued.record.revoked_at)
        self.assertEqual(issued.record.revocation_reason, "")

    def test_revocation_writer_accepts_each_internal_reason(self):
        cases = (
            ("signed_out", "session_revoked"),
            ("session_revoked", "session_revoked"),
            ("account_disabled", "account_disabled"),
            ("account_revoked", "account_revoked"),
        )

        for index, (reason, external_code) in enumerate(cases, start=1):
            with self.subTest(reason=reason):
                issued = issue_client_session(
                    user=self.user,
                    installation_id=uuid.UUID(int=index),
                    client_version="0.17.0",
                )
                revoke_client_session(session=issued.record, reason=reason)
                self.assertEqual(issued.record.revocation_reason, reason)
                resolution = resolve_client_session(
                    token=issued.access_token,
                    installation_id=issued.record.installation_id,
                )
                self.assertEqual(resolution.code, external_code)

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

    def test_invalid_revoked_and_unknown_resolution_leave_last_seen_unchanged(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        previous = timezone.now() - timedelta(days=1)
        ClientSession.objects.filter(pk=issued.record.pk).update(last_seen_at=previous)

        resolve_client_session(
            token=issued.access_token,
            installation_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        )
        issued.record.refresh_from_db()
        self.assertEqual(issued.record.last_seen_at, previous)

        revoke_client_session(
            session=issued.record,
            reason=ClientSession.RevocationReason.SESSION_REVOKED,
        )
        resolve_client_session(
            token=issued.access_token,
            installation_id=INSTALLATION_ID,
        )
        issued.record.refresh_from_db()
        self.assertEqual(issued.record.last_seen_at, previous)

        resolve_client_session(
            token="u" * 43,
            installation_id=INSTALLATION_ID,
        )
        issued.record.refresh_from_db()
        self.assertEqual(issued.record.last_seen_at, previous)

    def test_revocation_during_resolution_prevents_last_seen_update_and_active_result(self):
        issued = issue_client_session(
            user=self.user,
            installation_id=INSTALLATION_ID,
            client_version="0.17.0",
        )
        previous = issued.record.created_at - timedelta(days=1)
        ClientSession.objects.filter(pk=issued.record.pk).update(last_seen_at=previous)
        revoked_at = issued.record.created_at + timedelta(seconds=1)
        attempted_last_seen = revoked_at + timedelta(seconds=1)

        def revoke_before_last_seen_update():
            ClientSession.objects.filter(pk=issued.record.pk).update(
                revoked_at=revoked_at,
                revocation_reason=ClientSession.RevocationReason.SESSION_REVOKED,
            )
            return attempted_last_seen

        with patch(
            "history.client_sessions.timezone.now",
            side_effect=revoke_before_last_seen_update,
        ):
            resolution = resolve_client_session(
                token=issued.access_token,
                installation_id=INSTALLATION_ID,
            )

        issued.record.refresh_from_db()
        self.assertEqual(
            (resolution.code, resolution.explicit_revocation),
            ("session_revoked", True),
        )
        self.assertEqual(issued.record.last_seen_at, previous)
        self.assertEqual(resolution.record.revoked_at, revoked_at)

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
