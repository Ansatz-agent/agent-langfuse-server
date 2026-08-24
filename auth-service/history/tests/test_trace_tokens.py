from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from history.auth_views import ABSOLUTE_EXPIRY_KEY
from history.models import TraceUploadToken


INTERNAL_SECRET = "internal-test-secret-A1b2C3d4E5f6G7h8J9k0"
INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"
SECOND_INSTALLATION_ID = "22222222-2222-4222-8222-222222222222"


@override_settings(
    TRACE_GATEWAY_INTERNAL_SECRET=INTERNAL_SECRET,
    CSRF_TRUSTED_ORIGINS=["https://testserver"],
)
class TraceTokenTests(TestCase):
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

    def authenticate(self) -> str:
        self.client.force_login(self.user)
        session = self.client.session
        session[ABSOLUTE_EXPIRY_KEY] = (
            timezone.now() + timedelta(hours=1)
        ).isoformat()
        session.save()
        self.client.get(reverse("login"))
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def issue(
        self,
        *,
        installation_id: str = INSTALLATION_ID,
        csrf: str | None = None,
    ):
        if csrf is None:
            csrf = self.authenticate()
        return self.client.post(
            reverse("trace-token"),
            data=json.dumps(
                {
                    "installation_id": installation_id,
                    "client_version": "0.17.0",
                    "telemetry_schema_version": "1",
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )

    def introspect(self, token: str, *, secret: str = INTERNAL_SECRET):
        return self.client.post(
            reverse("trace-token-introspect"),
            data=json.dumps({"token": token}),
            content_type="application/json",
            HTTP_X_ANSATZ_INTERNAL_TOKEN=secret,
        )

    def test_issue_rejects_anonymous_and_missing_csrf(self):
        self.client.get(reverse("login"))
        csrf = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        anonymous = self.issue(csrf=csrf)
        self.assertEqual(anonymous.status_code, 401, anonymous.content)
        self.assertEqual(anonymous.json(), {"detail": "authentication_required"})
        self.assertEqual(anonymous["Cache-Control"], "no-store")

        self.authenticate()
        missing_csrf = self.client.post(
            reverse("trace-token"),
            data=json.dumps(
                {
                    "installation_id": INSTALLATION_ID,
                    "client_version": "0.17.0",
                    "telemetry_schema_version": "1",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(TraceUploadToken.objects.count(), 0)

    def test_issue_stores_only_digest_with_exact_900_second_expiry(self):
        csrf = self.authenticate()
        fixed_now = timezone.now().replace(microsecond=0)

        with patch("history.trace_tokens.timezone.now", return_value=fixed_now):
            response = self.issue(csrf=csrf)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Cache-Control"], "no-store")
        body = response.json()
        self.assertEqual(
            set(body),
            {"access_token", "expires_at", "expires_in", "installation_id"},
        )
        self.assertEqual(body["expires_in"], 900)
        self.assertEqual(body["installation_id"], INSTALLATION_ID)
        self.assertEqual(
            datetime.fromisoformat(body["expires_at"]),
            fixed_now + timedelta(seconds=900),
        )

        record = TraceUploadToken.objects.get()
        expected_digest = hashlib.sha256(body["access_token"].encode()).hexdigest()
        self.assertEqual(record.digest, expected_digest)
        self.assertEqual(record.expires_at, fixed_now + timedelta(seconds=900))
        self.assertNotEqual(record.digest, body["access_token"])
        self.assertNotIn(
            body["access_token"],
            " ".join(str(value) for value in record.__dict__.values()),
        )

    def test_rotation_revokes_prior_session_installation_token(self):
        csrf = self.authenticate()
        first = self.issue(csrf=csrf)
        second = self.issue(csrf=csrf)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        first_record = TraceUploadToken.objects.get(
            digest=hashlib.sha256(first.json()["access_token"].encode()).hexdigest()
        )
        second_record = TraceUploadToken.objects.get(
            digest=hashlib.sha256(second.json()["access_token"].encode()).hexdigest()
        )
        self.assertIsNotNone(first_record.revoked_at)
        self.assertIsNone(second_record.revoked_at)
        self.assertNotEqual(first_record.token_id, second_record.token_id)

    def test_active_introspection_has_exact_trusted_shape(self):
        issued = self.issue().json()
        response = self.introspect(issued["access_token"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "active",
                "token_id",
                "platform_user_id",
                "platform_username",
                "account_id",
                "session_id",
                "installation_id",
                "expires_at",
                "scope",
                "audience",
            },
        )
        self.assertIs(body["active"], True)
        self.assertEqual(body["platform_user_id"], str(self.user.pk))
        self.assertEqual(body["platform_username"], self.user.username)
        self.assertIsNone(body["session_id"])
        self.assertEqual(body["installation_id"], INSTALLATION_ID)
        self.assertEqual(body["scope"], "trace:write")
        self.assertEqual(body["audience"], "ansatz-trace-gateway")

    def test_inactive_cases_are_uniform(self):
        issued = self.issue().json()
        token = issued["access_token"]
        record = TraceUploadToken.objects.get()

        cases = []
        cases.append(("malformed", "not-a-token", None, "invalid_token", False))
        cases.append(("unknown", "z" * 43, None, "invalid_token", False))
        cases.append(("expired", token, {"expires_at": timezone.now()}, "token_expired", False))
        cases.append(("revoked", token, {"revoked_at": timezone.now()}, "token_revoked", False))
        cases.append(("wrong-scope", token, {"scope": "other"}, "invalid_token", False))
        cases.append(("wrong-audience", token, {"audience": "other"}, "invalid_token", False))

        for label, candidate, updates, reason, explicit_revocation in cases:
            with self.subTest(label=label):
                record.refresh_from_db()
                record.expires_at = timezone.now() + timedelta(minutes=15)
                record.revoked_at = None
                record.scope = "trace:write"
                record.audience = "ansatz-trace-gateway"
                if updates:
                    for key, value in updates.items():
                        setattr(record, key, value)
                record.save()
                response = self.introspect(candidate)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {
                        "active": False,
                        "reason": reason,
                        "explicit_revocation": explicit_revocation,
                    },
                )
                self.assertEqual(response["Cache-Control"], "no-store")

        record.expires_at = timezone.now() + timedelta(minutes=15)
        record.revoked_at = None
        record.scope = "trace:write"
        record.audience = "ansatz-trace-gateway"
        record.save()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(
            self.introspect(token).json(),
            {
                "active": False,
                "reason": "account_disabled",
                "explicit_revocation": True,
            },
        )

    def test_internal_credential_is_constant_shape_and_never_logged(self):
        issued = self.issue().json()
        upload_token = issued["access_token"]
        wrong_secret = "wrong-internal-secret-that-must-not-leak"
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            response = self.introspect(upload_token, secret=wrong_secret)
        finally:
            root.removeHandler(handler)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"active": False})
        combined = response.content.decode() + stream.getvalue()
        self.assertNotIn(wrong_secret, combined)
        self.assertNotIn(upload_token, combined)

    def test_logout_revokes_current_session_tokens_before_session_clear(self):
        csrf = self.authenticate()
        issued = self.issue(csrf=csrf).json()
        response = self.client.post(reverse("logout"), HTTP_X_CSRFTOKEN=csrf)

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(TraceUploadToken.objects.get().revoked_at)
        self.assertEqual(
            self.introspect(issued["access_token"]).json(),
            {
                "active": False,
                "reason": "token_revoked",
                "explicit_revocation": False,
            },
        )

    def test_device_revoke_only_revokes_current_users_selected_installation(self):
        csrf = self.authenticate()
        first = self.issue(csrf=csrf).json()
        second = self.issue(
            installation_id=SECOND_INSTALLATION_ID,
            csrf=csrf,
        ).json()
        response = self.client.post(
            reverse("trace-token-revoke-device"),
            data=json.dumps({"installation_id": INSTALLATION_ID}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"installation_id": INSTALLATION_ID, "revoked": 1},
        )
        self.assertEqual(
            self.introspect(first["access_token"]).json(),
            {
                "active": False,
                "reason": "token_revoked",
                "explicit_revocation": False,
            },
        )
        self.assertIs(self.introspect(second["access_token"]).json()["active"], True)

    def test_issue_rejects_invalid_json_schema_and_content_type(self):
        csrf = self.authenticate()
        wrong_type = self.client.post(
            reverse("trace-token"),
            data="{}",
            content_type="text/plain",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(wrong_type.status_code, 415)

        invalid_values = (
            {},
            {
                "installation_id": "not-a-uuid",
                "client_version": "0.17.0",
                "telemetry_schema_version": "1",
            },
            {
                "installation_id": INSTALLATION_ID,
                "client_version": "",
                "telemetry_schema_version": "2",
            },
        )
        for value in invalid_values:
            with self.subTest(value=value):
                response = self.client.post(
                    reverse("trace-token"),
                    data=json.dumps(value),
                    content_type="application/json",
                    HTTP_X_CSRFTOKEN=csrf,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response["Cache-Control"], "no-store")
