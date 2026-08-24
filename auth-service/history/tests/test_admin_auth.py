import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from history.client_sessions import issue_client_session


class AccountAdministrationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="alice", password="safe-test-pass-1")
        self.admin = user_model.objects.create_superuser(
            username="owner", password="safe-test-pass-3", email="owner@example.test"
        )

    def two_native_sessions(self, user):
        first = issue_client_session(
            user=user,
            installation_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            client_version="0.17.0",
        )
        second = issue_client_session(
            user=user,
            installation_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
            client_version="0.17.0",
        )
        return first.record, second.record

    def test_admin_session_revoke_is_isolated_and_disable_reaches_remaining_sessions(self):
        first, second = self.two_native_sessions(self.user)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("admin:history_clientsession_changelist"),
            {"action": "revoke_sessions", "_selected_action": [first.pk]},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.revocation_reason, "session_revoked")
        self.assertIsNone(second.revoked_at)

        response = self.client.post(
            reverse("admin:auth_user_changelist"),
            {"action": "disable_accounts", "_selected_action": [self.user.pk]},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(first.revocation_reason, "session_revoked")
        self.assertEqual(second.revocation_reason, "account_disabled")

    def test_admin_account_revoke_preserves_prior_session_evidence(self):
        first, second = self.two_native_sessions(self.user)
        self.client.force_login(self.admin)

        self.client.post(
            reverse("admin:history_clientsession_changelist"),
            {"action": "revoke_sessions", "_selected_action": [first.pk]},
            follow=True,
        )
        response = self.client.post(
            reverse("admin:history_accountidentity_changelist"),
            {
                "action": "revoke_accounts",
                "_selected_action": [first.account_id],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        first.account.refresh_from_db()
        self.assertEqual(first.account.state, "revoked")
        self.assertEqual(first.account.revocation_reason, "account_revoked")
        self.assertEqual(first.revocation_reason, "session_revoked")
        self.assertEqual(second.revocation_reason, "account_revoked")
        self.assertIsNotNone(first.account.revoked_at)
        self.assertIsNotNone(second.revoked_at)

    def test_reenabling_an_account_does_not_revive_revoked_sessions(self):
        first, second = self.two_native_sessions(self.user)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("admin:auth_user_changelist"),
            {"action": "disable_accounts", "_selected_action": [self.user.pk]},
            follow=True,
        )

        self.user.refresh_from_db()
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(first.revocation_reason, "account_disabled")
        self.assertEqual(second.revocation_reason, "account_disabled")
        self.assertIsNotNone(first.revoked_at)
        self.assertIsNotNone(second.revoked_at)

    def test_identity_and_session_admin_records_cannot_be_added_or_deleted(self):
        first, _ = self.two_native_sessions(self.user)
        self.client.force_login(self.admin)

        for url in (
            reverse("admin:history_accountidentity_add"),
            reverse("admin:history_clientsession_add"),
            reverse("admin:history_accountidentity_delete", args=[first.account_id]),
            reverse("admin:history_clientsession_delete", args=[first.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

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
