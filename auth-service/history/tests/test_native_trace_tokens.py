import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from history.auth_views import ABSOLUTE_EXPIRY_KEY
from history.client_sessions import issue_client_session, revoke_account, revoke_client_session
from history.models import ClientSession, TraceUploadToken


INTERNAL_SECRET = "internal-test-secret-A1b2C3d4E5f6G7h8J9k0"
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
            {"active": False, "reason": "session_revoked", "explicit_revocation": True},
        )

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

        first_record = TraceUploadToken.objects.get(digest__isnull=False, client_session__session_id=first["session_id"])
        revoke_client_session(
            session=first_record.client_session,
            reason=ClientSession.RevocationReason.SIGNED_OUT,
        )
        first_record.refresh_from_db()
        self.assertEqual(first_record.revocation_reason, "revoked")
        self.assertIsNotNone(first_record.revoked_at)
        self.assertEqual(
            self.introspect(first_token).json(),
            {"active": False, "reason": "session_revoked", "explicit_revocation": True},
        )
        self.assertTrue(self.introspect(second_token).json()["active"])

        second_record = TraceUploadToken.objects.get(client_session__session_id=second["session_id"])
        revoke_account(account=second_record.client_session.account)
        second_record.refresh_from_db()
        self.assertEqual(second_record.revocation_reason, "revoked")
        self.assertIsNotNone(second_record.revoked_at)
        self.assertEqual(
            self.introspect(second_token).json(),
            {"active": False, "reason": "account_revoked", "explicit_revocation": True},
        )
