import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from history.models import AccountIdentity, ClientSession


class AccountIdentityModelTests(TestCase):
    def test_persisted_account_id_cannot_be_changed(self):
        user = get_user_model().objects.create_user(username="immutable-account")
        identity = AccountIdentity.objects.create(user=user)
        original_account_id = identity.account_id

        identity.account_id = uuid.uuid4()

        with self.assertRaises(ValidationError):
            identity.save()

        identity.refresh_from_db()
        self.assertEqual(identity.account_id, original_account_id)

    def test_user_with_account_identity_cannot_be_deleted(self):
        user = get_user_model().objects.create_user(username="protected-account")
        AccountIdentity.objects.create(user=user)

        with self.assertRaises(ProtectedError):
            user.delete()


class ClientSessionModelTests(TestCase):
    def test_persisted_session_id_cannot_be_changed(self):
        user = get_user_model().objects.create_user(username="immutable-session")
        identity = AccountIdentity.objects.create(user=user)
        now = timezone.now()
        session = ClientSession.objects.create(
            account=identity,
            installation_id=uuid.uuid4(),
            credential_digest="a" * 64,
            client_version="1.0.0",
            created_at=now,
            last_seen_at=now,
        )
        original_session_id = session.session_id

        session.session_id = uuid.uuid4()

        with self.assertRaises(ValidationError):
            session.save()

        session.refresh_from_db()
        self.assertEqual(session.session_id, original_session_id)

    def test_account_with_retained_client_session_cannot_be_deleted(self):
        user = get_user_model().objects.create_user(username="protected-session")
        identity = AccountIdentity.objects.create(user=user)
        now = timezone.now()
        ClientSession.objects.create(
            account=identity,
            installation_id=uuid.uuid4(),
            credential_digest="b" * 64,
            client_version="1.0.0",
            created_at=now,
            last_seen_at=now,
        )

        with self.assertRaises(ProtectedError):
            identity.delete()
