import json
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from history.client_sessions import (
    disable_account,
    issue_client_session,
    revoke_account,
    revoke_client_session,
)
from history.models import ClientSession, TraceUploadToken

INTERNAL_SECRET = "internal-test-secret-A1b2C3d4E5f6G7h8J9k0"  # noqa: S105
INSTALLATION_ID = "11111111-1111-4111-8111-811111111111"
SECOND_INSTALLATION_ID = "22222222-2222-4222-8222-822222222222"


@override_settings(TRACE_GATEWAY_INTERNAL_SECRET=INTERNAL_SECRET)
class NativeTraceTokenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice")

    def issue_native_session(self, installation_id=INSTALLATION_ID):
        issued = issue_client_session(
            user=self.user,
            installation_id=installation_id,
            client_version="0.17.0",
        )
        return {
            "account_id": str(issued.record.account.account_id),
            "session_id": str(issued.record.session_id),
            "installation_id": str(issued.record.installation_id),
            "session_token": issued.access_token,
        }

    def native_headers(self, session):
        return {
            "HTTP_AUTHORIZATION": f"Bearer {session['session_token']}",
            "HTTP_X_ANSATZ_INSTALLATION_ID": session["installation_id"],
        }

    def issue_native_trace(self, session):
        return self.client.post(reverse("native-trace-token"), **self.native_headers(session))

    def introspect(self, token):
        return self.client.post(
            reverse("trace-token-introspect"),
            data=json.dumps({"token": token}),
            content_type="application/json",
            HTTP_X_ANSATZ_INTERNAL_TOKEN=INTERNAL_SECRET,
        )

    def test_native_trace_introspection_separates_refresh_from_explicit_revoke(self):
        session = self.issue_native_session()
        token = self.issue_native_trace(session).json()["access_token"]

        active = self.introspect(token)
        self.assertEqual(active.status_code, 200)
        self.assertEqual(
            (active.json()["active"], active.json()["account_id"], active.json()["session_id"]),
            (True, session["account_id"], session["session_id"]),
        )

        record = TraceUploadToken.objects.get()
        record.expires_at = timezone.now()
        record.save(update_fields=["expires_at"])
        self.assertEqual(
            self.introspect(token).json(),
            {"active": False, "reason": "token_expired", "explicit_revocation": False},
        )

        record.expires_at = timezone.now() + timedelta(minutes=15)
        record.save(update_fields=["expires_at"])
        revoke_client_session(
            session=record.client_session,
            reason=ClientSession.RevocationReason.SESSION_REVOKED,
        )
        self.assertEqual(
            self.introspect(token).json(),
            {
                "active": False,
                "reason": "session_revoked",
                "explicit_revocation": True,
                "account_id": session["account_id"],
                "session_id": session["session_id"],
                "installation_id": session["installation_id"],
                "revoked_at": record.client_session.revoked_at.isoformat(),
            },
        )

    def test_native_terminal_introspection_has_exact_trusted_identity_and_timestamp(self):
        cases = (
            ("session", "session_revoked"),
            ("disabled", "account_disabled"),
            ("account", "account_revoked"),
        )

        for label, expected_reason in cases:
            with self.subTest(label=label):
                user = get_user_model().objects.create_user(username=f"terminal-{label}")
                issued_session = issue_client_session(
                    user=user,
                    installation_id=INSTALLATION_ID,
                    client_version="0.17.0",
                )
                session = {
                    "account_id": str(issued_session.record.account.account_id),
                    "session_id": str(issued_session.record.session_id),
                    "installation_id": str(issued_session.record.installation_id),
                    "session_token": issued_session.access_token,
                }
                token = self.issue_native_trace(session).json()["access_token"]
                frozen = datetime(2026, 8, 25, 1, 2, 3, 456789, tzinfo=datetime_timezone.utc)

                if label == "session":
                    revoke_client_session(
                        session=issued_session.record,
                        reason=ClientSession.RevocationReason.SESSION_REVOKED,
                    )
                elif label == "disabled":
                    disable_account(user=user)
                else:
                    revoke_account(account=issued_session.record.account)
                ClientSession.objects.filter(pk=issued_session.record.pk).update(revoked_at=frozen)

                response = self.introspect(token)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertEqual(
                    response.json(),
                    {
                        "active": False,
                        "reason": expected_reason,
                        "explicit_revocation": True,
                        "account_id": session["account_id"],
                        "session_id": session["session_id"],
                        "installation_id": session["installation_id"],
                        "revoked_at": "2026-08-25T01:02:03.456789+00:00",
                    },
                )
                combined = response.content.decode()
                self.assertNotIn(token, combined)
                self.assertNotIn(session["session_token"], combined)

    def test_native_token_with_missing_or_inconsistent_session_binding_is_unavailable(self):
        session = self.issue_native_session()
        token = self.issue_native_trace(session).json()["access_token"]
        record = TraceUploadToken.objects.get()
        other_user = get_user_model().objects.create_user(username="mallory")

        corruptions = (
            ("missing", {"client_session": None}),
            ("user mismatch", {"user": other_user}),
            ("installation mismatch", {"installation_id": SECOND_INSTALLATION_ID}),
            ("credential mismatch", {"session_key_digest": "0" * 64}),
        )
        for label, updates in corruptions:
            with self.subTest(label=label):
                record.refresh_from_db()
                record.client_session_id = ClientSession.objects.get(
                    session_id=session["session_id"]
                ).pk
                record.user = self.user
                record.installation_id = INSTALLATION_ID
                record.session_key_digest = record.client_session.credential_digest
                for field, value in updates.items():
                    setattr(record, field, value)
                record.save()

                self.assertEqual(
                    self.introspect(token).json(),
                    {
                        "active": False,
                        "reason": "authentication_unavailable",
                        "explicit_revocation": False,
                    },
                )

    def test_prior_session_terminal_evidence_is_immutable_across_account_revoke(self):
        session = self.issue_native_session()
        token = self.issue_native_trace(session).json()["access_token"]
        record = TraceUploadToken.objects.get()
        frozen = datetime(2026, 8, 24, 23, 59, 58, tzinfo=datetime_timezone.utc)
        ClientSession.objects.filter(pk=record.client_session_id).update(
            revoked_at=frozen,
            revocation_reason=ClientSession.RevocationReason.SESSION_REVOKED,
        )

        revoke_account(account=record.client_session.account)

        response = self.introspect(token).json()
        self.assertEqual(response["reason"], "session_revoked")
        self.assertEqual(response["revoked_at"], "2026-08-24T23:59:58+00:00")
        self.assertEqual(response["account_id"], session["account_id"])
        self.assertEqual(response["session_id"], session["session_id"])

    def test_native_issue_is_bearer_only_and_no_store(self):
        session = self.issue_native_session()
        issued = self.issue_native_trace(session)

        self.assertEqual(issued.status_code, 201)
        self.assertEqual(issued["Cache-Control"], "no-store")
        self.assertEqual(
            set(issued.json()),
            {"access_token", "expires_at", "expires_in", "installation_id"},
        )
        self.assertEqual(issued.json()["installation_id"], INSTALLATION_ID)

        for headers in ({}, {"HTTP_AUTHORIZATION": "Token token"}):
            with self.subTest(headers=headers):
                unavailable = self.client.post(reverse("native-trace-token"), **headers)
                self.assertEqual(unavailable.status_code, 401)
                self.assertEqual(
                    unavailable.json(),
                    {
                        "state": "unavailable",
                        "code": "invalid_session_credential",
                        "retryable": True,
                    },
                )
                self.assertEqual(unavailable["Cache-Control"], "no-store")

        rejected = self.client.get(reverse("native-trace-token"), **self.native_headers(session))
        self.assertEqual(rejected.status_code, 405)
        self.assertEqual(rejected["Allow"], "POST")
        self.assertEqual(rejected["Cache-Control"], "no-store")

    def test_native_rotation_retains_rows_and_marks_prior_token_rotated(self):
        session = self.issue_native_session()
        first = self.issue_native_trace(session).json()["access_token"]
        second = self.issue_native_trace(session).json()["access_token"]

        self.assertEqual(TraceUploadToken.objects.count(), 2)
        prior = TraceUploadToken.objects.get(digest__isnull=False, revoked_at__isnull=False)
        self.assertEqual(prior.revocation_reason, "rotated")
        self.assertEqual(
            self.introspect(first).json(),
            {"active": False, "reason": "token_rotated", "explicit_revocation": False},
        )
        self.assertTrue(self.introspect(second).json()["active"])

    def test_every_native_session_and_account_revoke_revokes_bound_rows(self):
        first = self.issue_native_session()
        second = self.issue_native_session(SECOND_INSTALLATION_ID)
        first_token = self.issue_native_trace(first).json()["access_token"]
        second_token = self.issue_native_trace(second).json()["access_token"]

        first_record = TraceUploadToken.objects.get(
            digest__isnull=False, client_session__session_id=first["session_id"]
        )
        revoke_client_session(
            session=first_record.client_session,
            reason=ClientSession.RevocationReason.SIGNED_OUT,
        )
        first_record.refresh_from_db()
        self.assertEqual(first_record.revocation_reason, "revoked")
        self.assertIsNotNone(first_record.revoked_at)
        self.assertEqual(
            self.introspect(first_token).json(),
            {
                "active": False,
                "reason": "session_revoked",
                "explicit_revocation": True,
                "account_id": first["account_id"],
                "session_id": first["session_id"],
                "installation_id": first["installation_id"],
                "revoked_at": first_record.client_session.revoked_at.isoformat(),
            },
        )
        self.assertTrue(self.introspect(second_token).json()["active"])

        second_record = TraceUploadToken.objects.get(
            client_session__session_id=second["session_id"]
        )
        revoke_account(account=second_record.client_session.account)
        second_record.refresh_from_db()
        self.assertEqual(second_record.revocation_reason, "revoked")
        self.assertIsNotNone(second_record.revoked_at)
        self.assertEqual(
            self.introspect(second_token).json(),
            {
                "active": False,
                "reason": "account_revoked",
                "explicit_revocation": True,
                "account_id": second["account_id"],
                "session_id": second["session_id"],
                "installation_id": second["installation_id"],
                "revoked_at": second_record.client_session.revoked_at.isoformat(),
            },
        )
