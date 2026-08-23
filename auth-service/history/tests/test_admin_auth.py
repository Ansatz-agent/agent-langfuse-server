from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


class AccountAdministrationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="alice", password="safe-test-pass-1")
        self.admin = user_model.objects.create_superuser(
            username="owner", password="safe-test-pass-3", email="owner@example.test"
        )

    def test_normal_user_cannot_access_django_admin(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:index"))

        self.assertIn(response.status_code, {302, 403})
        self.assertNotEqual(response.status_code, 200)

    def test_staff_but_non_superuser_cannot_access_admin(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:index"))

        self.assertIn(response.status_code, {302, 403})
        self.assertNotEqual(response.status_code, 200)

    def test_superuser_can_manage_accounts_in_admin(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("admin:auth_user_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alice")

    def test_disabled_account_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            reverse("login"),
            {"username": "alice", "password": "safe-test-pass-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse("_auth_user_id" in self.client.session)

    def test_public_registration_path_does_not_exist(self):
        response = self.client.get("/register/")

        self.assertEqual(response.status_code, 404)

    def test_public_account_lifecycle_routes_do_not_exist(self):
        paths = [
            "/signup/",
            "/register/",
            "/accounts/signup/",
            "/accounts/register/",
            "/accounts/password_reset/",
            "/accounts/password_reset/done/",
            "/accounts/password_change/",
            "/accounts/password_change/done/",
            "/accounts/reset/test-user/test-token/",
            "/accounts/reset/done/",
            "/accounts/invite/",
            "/accounts/invitation/",
            "/api/accounts/",
            "/api/signup/",
            "/api/invitations/",
            "/api/password-reset/",
            "/api/password-change/",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
                self.assertEqual(self.client.post(path).status_code, 404)

    def test_client_session_endpoint_never_exposes_account_actions(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["hermes_absolute_session_expires_at"] = (
            timezone.now() + timedelta(hours=1)
        ).isoformat()
        session.save()
        body = self.client.get(reverse("client-session")).json()
        self.assertNotIn("signup", body)
        self.assertNotIn("password_reset", body)
        self.assertNotIn("invitation", body)

    def test_wrong_password_eventually_hits_lockout_response(self):
        for _ in range(11):
            response = self.client.post(
                reverse("login"),
                {"username": "alice", "password": "wrong-password"},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"], "Too many login attempts. Try again later.")
