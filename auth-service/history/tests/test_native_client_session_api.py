import json
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from history.auth_views import ABSOLUTE_EXPIRY_KEY
from history.models import ClientSession


INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"


@override_settings(CSRF_TRUSTED_ORIGINS=["https://testserver"])
class NativeClientSessionApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="alice",
            password="safe-test-pass-1",
        )
        self.client = Client(
            enforce_csrf_checks=True,
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_ORIGIN="https://testserver",
        )

    def authenticate_web_session(self):
        self.client.force_login(self.user)
        session = self.client.session
        session[ABSOLUTE_EXPIRY_KEY] = (
            timezone.now() + timedelta(hours=1)
        ).isoformat()
        session.save()
        self.client.get(reverse("login"))
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def issue(self):
        csrf = self.authenticate_web_session()
        response = self.client.post(
            reverse("native-client-session"),
            data=json.dumps(
                {
                    "installation_id": INSTALLATION_ID,
                    "client_version": "0.17.0",
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_web_bootstrap_status_and_explicit_revoke_have_exact_shapes(self):
        csrf = self.authenticate_web_session()
        issued = self.client.post(
            reverse("native-client-session"),
            data=json.dumps(
                {
                    "installation_id": INSTALLATION_ID,
                    "client_version": "0.17.0",
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(issued.status_code, 201)
        self.assertEqual(issued["Cache-Control"], "no-store")
        self.assertEqual(
            set(issued.json()),
            {
                "account_id",
                "session_id",
                "session_token",
                "installation_id",
                "username",
                "issued_at",
            },
        )
        body = issued.json()
        self.assertEqual(body["installation_id"], INSTALLATION_ID)
        self.assertEqual(body["username"], self.user.username)
        self.assertEqual(UUID(body["account_id"]).version, 4)
        self.assertEqual(UUID(body["session_id"]).version, 4)

        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {body['session_token']}",
            "HTTP_X_ANSATZ_INSTALLATION_ID": INSTALLATION_ID,
        }
        active = self.client.get(reverse("native-client-session"), **headers)
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active["Cache-Control"], "no-store")
        self.assertEqual(
            active.json()["state"],
            "active",
        )
        self.assertEqual(
            set(active.json()),
            {
                "state",
                "account_id",
                "session_id",
                "installation_id",
                "username",
                "server_time",
            },
        )

        ClientSession.objects.filter(session_id=body["session_id"]).update(
            revoked_at=timezone.now(),
            revocation_reason="session_revoked",
        )
        revoked = self.client.get(reverse("native-client-session"), **headers)
        self.assertEqual(revoked.status_code, 403)
        self.assertEqual(revoked["Cache-Control"], "no-store")
        self.assertEqual(
            revoked.json()["code"],
            "session_revoked",
        )
        self.assertIs(revoked.json()["retryable"], False)

    def test_issue_rejects_invalid_web_or_json_requests_without_writing(self):
        self.client.get(reverse("login"))
        csrf = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        anonymous = self.client.post(
            reverse("native-client-session"),
            data=json.dumps(
                {
                    "installation_id": INSTALLATION_ID,
                    "client_version": "0.17.0",
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous.json(), {"detail": "authentication_required"})
        self.assertEqual(anonymous["Cache-Control"], "no-store")

        csrf = self.authenticate_web_session()
        missing_csrf = self.client.post(
            reverse("native-client-session"),
            data=json.dumps(
                {
                    "installation_id": INSTALLATION_ID,
                    "client_version": "0.17.0",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(missing_csrf["Cache-Control"], "no-store")

        cases = (
            ("wrong-content-type", "{}", "text/plain", 415),
            ("invalid-json", "[", "application/json", 400),
            (
                "duplicate-key",
                '{"installation_id":"11111111-1111-4111-8111-111111111111",'
                '"client_version":"0.17.0","client_version":"0.17.1"}',
                "application/json",
                400,
            ),
            (
                "unexpected-key",
                json.dumps(
                    {
                        "installation_id": INSTALLATION_ID,
                        "client_version": "0.17.0",
                        "extra": True,
                    }
                ),
                "application/json",
                400,
            ),
            (
                "uppercase-installation",
                json.dumps(
                    {
                        "installation_id": "A1111111-1111-4111-8111-111111111111",
                        "client_version": "0.17.0",
                    }
                ),
                "application/json",
                400,
            ),
        )
        for label, data, content_type, status in cases:
            with self.subTest(label=label):
                response = self.client.post(
                    reverse("native-client-session"),
                    data=data,
                    content_type=content_type,
                    HTTP_X_CSRFTOKEN=csrf,
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(ClientSession.objects.count(), 0)

    def test_bearer_resolution_is_strict_and_retryable_when_unavailable(self):
        issued = self.issue()
        unavailable = {
            "state": "unavailable",
            "code": "invalid_session_credential",
            "retryable": True,
        }
        cases = (
            ("missing-bearer", {}),
            (
                "wrong-scheme",
                {
                    "HTTP_AUTHORIZATION": f"Token {issued['session_token']}",
                    "HTTP_X_ANSATZ_INSTALLATION_ID": INSTALLATION_ID,
                },
            ),
            (
                "whitespace-in-bearer",
                {
                    "HTTP_AUTHORIZATION": f"Bearer {issued['session_token']} extra",
                    "HTTP_X_ANSATZ_INSTALLATION_ID": INSTALLATION_ID,
                },
            ),
            (
                "uppercase-installation",
                {
                    "HTTP_AUTHORIZATION": f"Bearer {issued['session_token']}",
                    "HTTP_X_ANSATZ_INSTALLATION_ID": "A1111111-1111-4111-8111-111111111111",
                },
            ),
        )
        for label, headers in cases:
            with self.subTest(label=label):
                response = self.client.get(
                    reverse("native-client-session"),
                    **headers,
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), unavailable)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertNotIn(issued["session_token"], response.content.decode())

    def test_delete_persists_signed_out_and_only_allows_delete(self):
        issued = self.issue()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {issued['session_token']}",
            "HTTP_X_ANSATZ_INSTALLATION_ID": INSTALLATION_ID,
        }
        rejected = self.client.get(reverse("native-client-session-current"), **headers)
        self.assertEqual(rejected.status_code, 405)
        self.assertEqual(rejected["Allow"], "DELETE")
        self.assertEqual(rejected["Cache-Control"], "no-store")

        signed_out = self.client.delete(
            reverse("native-client-session-current"),
            **headers,
        )
        self.assertEqual(signed_out.status_code, 204)
        self.assertEqual(signed_out["Cache-Control"], "no-store")
        record = ClientSession.objects.get(session_id=issued["session_id"])
        self.assertEqual(record.revocation_reason, "signed_out")
        self.assertIsNotNone(record.revoked_at)

        revoked = self.client.get(reverse("native-client-session"), **headers)
        self.assertEqual(
            revoked.json(),
            {
                "state": "revoked",
                "code": "session_revoked",
                "account_id": issued["account_id"],
                "session_id": issued["session_id"],
                "revoked_at": record.revoked_at.isoformat(),
                "retryable": False,
            },
        )
