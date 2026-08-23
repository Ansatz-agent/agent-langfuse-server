from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from history.models import HistorySession, ImportBatch


class HistoryModelTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(username="alice", password="safe-test-pass-1")
        self.bob = user_model.objects.create_user(username="bob", password="safe-test-pass-2")
        self.admin = user_model.objects.create_superuser(
            username="owner", password="safe-test-pass-3", email="owner@example.test"
        )

    def test_external_id_is_unique_per_owner(self):
        HistorySession.objects.create(
            owner=self.alice, uploader=self.admin, external_id="session-1"
        )
        HistorySession.objects.create(owner=self.bob, uploader=self.admin, external_id="session-1")

        self.assertEqual(HistorySession.objects.filter(external_id="session-1").count(), 2)

    def test_duplicate_external_id_for_same_owner_is_rejected(self):
        HistorySession.objects.create(
            owner=self.alice, uploader=self.admin, external_id="session-1"
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            HistorySession.objects.create(
                owner=self.alice,
                uploader=self.admin,
                external_id="session-1",
            )

    def test_session_requires_uploader(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HistorySession.objects.create(
                owner=self.alice,
                external_id="session-without-uploader",
            )

    def test_owner_with_history_cannot_be_deleted(self):
        HistorySession.objects.create(
            owner=self.alice, uploader=self.admin, external_id="session-1"
        )

        with self.assertRaises(ProtectedError):
            self.alice.delete()

    def test_uploader_with_history_cannot_be_deleted(self):
        HistorySession.objects.create(
            owner=self.alice, uploader=self.admin, external_id="session-1"
        )

        with self.assertRaises(ProtectedError):
            self.admin.delete()

    def test_parent_with_threads_is_protected_from_deletion(self):
        parent = HistorySession.objects.create(
            owner=self.alice, uploader=self.admin, external_id="parent"
        )
        HistorySession.objects.create(
            owner=self.alice,
            uploader=self.admin,
            parent_session=parent,
            external_id="child",
        )

        with self.assertRaises(ProtectedError):
            parent.delete()

    def test_visible_to_scopes_normal_users_and_allows_superuser(self):
        alice_session = HistorySession.objects.create(
            owner=self.alice, uploader=self.admin, external_id="alice-1"
        )
        bob_session = HistorySession.objects.create(
            owner=self.bob, uploader=self.admin, external_id="bob-1"
        )

        self.assertQuerySetEqual(
            HistorySession.objects.visible_to(self.alice), [alice_session], ordered=False
        )
        self.assertQuerySetEqual(
            HistorySession.objects.visible_to(self.admin),
            [alice_session, bob_session],
            ordered=False,
        )

    def test_import_batch_records_metadata_without_uploaded_content(self):
        batch = ImportBatch.objects.create(
            owner=self.alice,
            uploader=self.alice,
            original_filename="history.jsonl",
            sha256="a" * 64,
            status=ImportBatch.Status.SUCCEEDED,
            imported_sessions=3,
            skipped_sessions=1,
            imported_messages=20,
        )

        self.assertEqual(batch.imported_sessions, 3)
        self.assertNotIn("content", {field.name for field in ImportBatch._meta.fields})
        self.assertNotIn("file", {field.name for field in ImportBatch._meta.fields})
