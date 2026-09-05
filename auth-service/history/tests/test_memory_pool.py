from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from history.models import (
    AccountIdentity,
    HistoryMessage,
    HistorySession,
    MemoryIngestJob,
    UserMemoryPool,
)


class MemoryPoolTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(username="memory-alice", password="safe-pass-1")
        self.bob = user_model.objects.create_user(username="memory-bob", password="safe-pass-2")

    def login_alice(self) -> None:
        response = self.client.post(
            reverse("login"),
            {"username": "memory-alice", "password": "safe-pass-1"},
        )
        self.assertEqual(response.status_code, 302)

    def test_user_can_view_only_their_own_memory_pool(self):
        UserMemoryPool.objects.create(
            owner=self.bob,
            memory_markdown="Bob's private memory",
        )
        self.login_alice()

        response = self.client.get(reverse("history:memory-pool"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Bob's private memory")
        self.assertContains(response, "MEMORY.md")
        self.assertContains(response, "USER.md")

    def test_user_can_save_memory_and_user_markdown_to_their_pool(self):
        self.login_alice()

        response = self.client.post(
            reverse("history:memory-pool"),
            {
                "memory_markdown": "## Local facts\n\n- Server uses Podman.",
                "user_markdown": "## Preferences\n\n- Prefers concise status documents.",
            },
        )

        self.assertRedirects(response, reverse("history:memory-pool"))
        pool = UserMemoryPool.objects.get(owner=self.alice)
        self.assertIn("Podman", pool.memory_markdown)
        self.assertIn("concise", pool.user_markdown)
        self.assertFalse(UserMemoryPool.objects.filter(owner=self.bob).exists())

    def test_user_can_upload_local_markdown_files(self):
        self.login_alice()

        response = self.client.post(
            reverse("history:memory-pool"),
            {
                "memory_file": SimpleUploadedFile(
                    "MEMORY.md",
                    b"## Imported memory\n\n- Imported from local Hermes.",
                ),
                "user_file": SimpleUploadedFile(
                    "USER.md",
                    b"## Imported profile\n\n- Chinese responses.",
                ),
            },
        )

        self.assertRedirects(response, reverse("history:memory-pool"))
        pool = UserMemoryPool.objects.get(owner=self.alice)
        self.assertIn("Imported from local Hermes", pool.memory_markdown)
        self.assertIn("Chinese responses", pool.user_markdown)

    def test_uploading_only_memory_file_preserves_existing_user_markdown(self):
        UserMemoryPool.objects.create(
            owner=self.alice,
            memory_markdown="Old memory",
            user_markdown="Keep this user profile",
        )
        self.login_alice()

        response = self.client.post(
            reverse("history:memory-pool"),
            {
                "memory_file": SimpleUploadedFile(
                    "MEMORY.md",
                    b"New memory only",
                ),
            },
        )

        self.assertRedirects(response, reverse("history:memory-pool"))
        pool = UserMemoryPool.objects.get(owner=self.alice)
        self.assertEqual(pool.memory_markdown, "New memory only")
        self.assertEqual(pool.user_markdown, "Keep this user profile")

    def test_uploading_only_user_file_preserves_existing_memory_markdown(self):
        UserMemoryPool.objects.create(
            owner=self.alice,
            memory_markdown="Keep this memory",
            user_markdown="Old profile",
        )
        self.login_alice()

        response = self.client.post(
            reverse("history:memory-pool"),
            {
                "user_file": SimpleUploadedFile(
                    "USER.md",
                    b"New profile only",
                ),
            },
        )

        self.assertRedirects(response, reverse("history:memory-pool"))
        pool = UserMemoryPool.objects.get(owner=self.alice)
        self.assertEqual(pool.memory_markdown, "Keep this memory")
        self.assertEqual(pool.user_markdown, "New profile only")

    def test_memory_pool_is_not_addressable_by_another_user_id(self):
        self.login_alice()

        response = self.client.get(reverse("history:memory-pool"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "memory-bob")

    def test_only_admin_can_view_all_extracted_memories_with_sources(self):
        AccountIdentity.objects.create(user=self.alice)
        AccountIdentity.objects.create(user=self.bob)
        session = HistorySession.objects.create(
            owner=self.bob,
            uploader=self.bob,
            external_id="catalog-session",
            title="Catalog source",
        )
        message = HistoryMessage.objects.create(
            session=session, role="user", content="A preference"
        )
        MemoryIngestJob.objects.create(
            owner=self.bob,
            session=session,
            source_key="catalog-job",
            message_ids=[message.pk],
            content_sha256="a" * 64,
            mem0_memory_ids=["catalog-memory"],
            status=MemoryIngestJob.Status.SUCCEEDED,
        )
        self.login_alice()
        self.assertEqual(self.client.get(reverse("history:memory-catalog")).status_code, 404)

        self.alice.is_superuser = True
        self.alice.save(update_fields=["is_superuser"])
        with patch(
            "history.views.list_all_memories",
            return_value=[
                {
                    "id": "catalog-memory",
                    "memory": "Likes concise reports",
                    "user": self.bob.username,
                    "session": session,
                }
            ],
        ):
            response = self.client.get(reverse("history:memory-catalog"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Likes concise reports")
        self.assertContains(response, "Catalog source")
        self.assertContains(response, "memory-bob")
