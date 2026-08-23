import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from history.models import HistorySession


class ImportViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(username="alice", password="safe-test-pass-1")
        self.bob = user_model.objects.create_user(username="bob", password="safe-test-pass-2")
        self.admin = user_model.objects.create_superuser(
            username="owner", password="safe-test-pass-3", email="owner@example.test"
        )

    def login_as(self, username, password):
        response = self.client.post(
            reverse("login"),
            {"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 302)

    def payload(self, external_id="uploaded-session"):
        return {
            "id": external_id,
            "title": "Uploaded title",
            "messages": [{"id": 1, "role": "user", "content": "uploaded content"}],
        }

    def file(self, rows):
        content = "\n".join(json.dumps(row) for row in rows).encode()
        return SimpleUploadedFile("upload.jsonl", content, content_type="application/x-ndjson")

    def test_normal_user_import_is_owned_by_logged_in_user_even_with_owner_id_field(self):
        self.login_as("alice", "safe-test-pass-1")

        response = self.client.post(
            reverse("history:session-import"),
            {"history_file": self.file([self.payload()]), "owner_id": self.bob.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(HistorySession.objects.get().owner, self.alice)

    def test_superuser_can_choose_import_owner(self):
        self.login_as("owner", "safe-test-pass-3")

        response = self.client.post(
            reverse("history:session-import"),
            {"history_file": self.file([self.payload()]), "owner_id": self.bob.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(HistorySession.objects.get().owner, self.bob)

    def test_invalid_import_returns_error_without_creating_history(self):
        self.login_as("alice", "safe-test-pass-1")
        bad_file = SimpleUploadedFile(
            "upload.jsonl", b"{not valid json}", content_type="application/x-ndjson"
        )

        response = self.client.post(reverse("history:session-import"), {"history_file": bad_file})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Invalid JSON", status_code=400)
        self.assertEqual(HistorySession.objects.count(), 0)
