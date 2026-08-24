from __future__ import annotations

import io
import stat
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class ProvisionTraceTestUsersCommandTests(TestCase):
    def setUp(self):
        self.scratch = Path(settings.BASE_DIR) / "tmp" / "provision-trace-users-tests"
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.output = self.scratch / f"{self._testMethodName}.env"
        self.output.unlink(missing_ok=True)
        self.addCleanup(self.output.unlink, missing_ok=True)

    @staticmethod
    def parse_credentials(path: Path) -> dict[str, str]:
        return dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        )

    def test_creates_two_active_non_admin_users_and_owner_only_credentials(self):
        stdout = io.StringIO()

        call_command("provision_trace_test_users", output=self.output, stdout=stdout)

        credentials = self.parse_credentials(self.output)
        self.assertEqual(
            set(credentials),
            {
                "AUTH_BASE_URL",
                "USER_A_ID",
                "USER_A_USERNAME",
                "USER_A_EMAIL",
                "USER_A_PASSWORD",
                "USER_A_INSTALLATION_ID",
                "USER_B_ID",
                "USER_B_USERNAME",
                "USER_B_EMAIL",
                "USER_B_PASSWORD",
                "USER_B_INSTALLATION_ID",
            },
        )
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(credentials["AUTH_BASE_URL"], "https://c2sml.cn/auth")
        self.assertNotEqual(
            credentials["USER_A_INSTALLATION_ID"],
            credentials["USER_B_INSTALLATION_ID"],
        )

        user_model = get_user_model()
        for label in ("A", "B"):
            user = user_model.objects.get(username=credentials[f"USER_{label}_USERNAME"])
            self.assertEqual(str(user.pk), credentials[f"USER_{label}_ID"])
            self.assertEqual(user.email, credentials[f"USER_{label}_EMAIL"])
            self.assertTrue(user.is_active)
            self.assertFalse(user.is_staff)
            self.assertFalse(user.is_superuser)
            self.assertTrue(user.check_password(credentials[f"USER_{label}_PASSWORD"]))
            self.assertNotIn(credentials[f"USER_{label}_PASSWORD"], stdout.getvalue())

    def test_refuses_to_overwrite_an_existing_credentials_file(self):
        self.output.write_text("preserve-me\n", encoding="utf-8")

        with self.assertRaisesRegex(CommandError, "already exists"):
            call_command("provision_trace_test_users", output=self.output)

        self.assertEqual(self.output.read_text(encoding="utf-8"), "preserve-me\n")
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_refuses_to_repurpose_a_privileged_existing_identity(self):
        user_model = get_user_model()
        user_model.objects.create_superuser(
            username="trace-e2e-a-20260823",
            email="existing-admin@example.invalid",
            password="test-only-existing-admin-password",
        )

        with self.assertRaisesRegex(CommandError, "privileged"):
            call_command("provision_trace_test_users", output=self.output)

        self.assertFalse(self.output.exists())
