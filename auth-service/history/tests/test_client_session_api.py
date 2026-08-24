from datetime import datetime, timedelta

from axes.models import AccessAttempt
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone


@override_settings(HERMES_SESSION_ABSOLUTE_AGE_SECONDS=3600)
class ClientSessionApiTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"
        self.user = get_user_model().objects.create_user(
            username="alice", password="safe-test-pass-1"
        )

    def login(self):
        response = self.client.post(
            reverse("login"),
            {"username": "alice", "password": "safe-test-pass-1"},
        )
        self.assertEqual(response.status_code, 302)

    def test_anonymous_response_is_strict_401_json(self):
        response = self.client.get(reverse("client-session"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"authenticated": False})

    def test_login_sets_absolute_expiry_and_authenticated_schema(self):
        before = timezone.now()
        self.login()
        response = self.client.get(reverse("client-session"))
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(body),
            {
                "authenticated",
                "sub",
                "username",
                "role",
                "server_time",
                "session_expires_at",
                "trace_dashboard_url",
            },
        )
        self.assertIs(body["authenticated"], True)
        self.assertEqual(body["sub"], str(self.user.pk))
        self.assertEqual(body["username"], "alice")
        self.assertEqual(body["role"], "user")
        self.assertEqual(body["trace_dashboard_url"], "/traces/")
        expires = datetime.fromisoformat(body["session_expires_at"])
        self.assertGreaterEqual(expires, before + timedelta(minutes=59))

    def test_staff_session_reports_admin_role(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.login()
        self.assertEqual(
            self.client.get(reverse("client-session")).json()["role"],
            "admin",
        )

    def test_status_checks_do_not_slide_expiry(self):
        self.login()
        first = self.client.get(reverse("client-session")).json()
        second = self.client.get(reverse("client-session")).json()
        self.assertEqual(first["session_expires_at"], second["session_expires_at"])

    def test_status_checks_do_not_increment_axes_after_failed_login(self):
        self.client.post(
            reverse("login"),
            {"username": "alice", "password": "wrong-password"},
        )
        before = AccessAttempt.objects.count()
        self.assertGreater(before, 0)
        for _ in range(3):
            self.client.get(reverse("client-session"))
        self.assertEqual(AccessAttempt.objects.count(), before)

    def test_login_rotates_session_key(self):
        session = self.client.session
        session["pre_login"] = True
        session.save()
        before = session.session_key
        self.login()
        self.assertNotEqual(self.client.session.session_key, before)

    def test_expired_absolute_timestamp_is_rejected(self):
        self.login()
        session = self.client.session
        session["hermes_absolute_session_expires_at"] = (
            timezone.now() - timedelta(seconds=1)
        ).isoformat()
        session.save()
        response = self.client.get(reverse("client-session"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"authenticated": False})

    def test_missing_absolute_timestamp_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("client-session"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"authenticated": False})

    def test_rejected_status_is_read_only_and_does_not_logout_admin(self):
        admin = get_user_model().objects.create_superuser(
            username="server-admin", password="safe-admin-pass-1"
        )
        self.client.force_login(admin)
        session_key = self.client.session.session_key
        self.assertEqual(self.client.get(reverse("client-session")).status_code, 401)
        self.assertEqual(self.client.session.session_key, session_key)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_expired_absolute_session_cannot_read_memory_or_history(self):
        self.login()
        session = self.client.session
        session["hermes_absolute_session_expires_at"] = (
            timezone.now() - timedelta(seconds=1)
        ).isoformat()
        session.save()
        for name in ("history:memory-pool", "history:session-list"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)
