from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

MIGRATE_FROM = [("history", "0001_initial")]
MIGRATE_TO = [("history", "0002_add_session_relationships_and_uploader")]


class MigrationTestBase(TransactionTestCase):
    def migrate_to_old_state(self):
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        return executor.loader.project_state(MIGRATE_FROM).apps

    def migrate_to_new_state(self):
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_TO)
        return executor.loader.project_state(MIGRATE_TO).apps


class SessionRelationshipMigrationTests(MigrationTestBase):
    def setUp(self):
        super().setUp()
        old_apps = self.migrate_to_old_state()

        User = old_apps.get_model("auth", "User")
        ImportBatch = old_apps.get_model("history", "ImportBatch")
        HistorySession = old_apps.get_model("history", "HistorySession")

        owner = User.objects.create(username="owner")
        uploader = User.objects.create(username="uploader")
        ImportBatch.objects.create(
            owner_id=owner.pk,
            uploader_id=uploader.pk,
            original_filename="history.jsonl",
            sha256="a" * 64,
            status="succeeded",
            imported_sessions=2,
        )
        HistorySession.objects.create(
            owner_id=owner.pk,
            external_id="parent",
            raw_metadata={},
        )
        HistorySession.objects.create(
            owner_id=owner.pk,
            external_id="child",
            raw_metadata={"parent_session_id": "parent"},
        )

        self.apps = self.migrate_to_new_state()

    def test_existing_sessions_receive_uploader_and_parent_relationship(self):
        HistorySession = self.apps.get_model("history", "HistorySession")

        parent = HistorySession.objects.get(external_id="parent")
        child = HistorySession.objects.get(external_id="child")
        self.assertEqual(parent.uploader.username, "uploader")
        self.assertEqual(child.uploader.username, "uploader")
        self.assertEqual(child.parent_session_id, parent.pk)


class AmbiguousUploaderMigrationTests(MigrationTestBase):
    def setUp(self):
        super().setUp()
        self.old_apps = self.migrate_to_old_state()
        User = self.old_apps.get_model("auth", "User")
        ImportBatch = self.old_apps.get_model("history", "ImportBatch")
        HistorySession = self.old_apps.get_model("history", "HistorySession")

        owner = User.objects.create(username="owner")
        first = User.objects.create(username="first-uploader")
        second = User.objects.create(username="second-uploader")
        for uploader, digest in ((first, "a" * 64), (second, "b" * 64)):
            ImportBatch.objects.create(
                owner_id=owner.pk,
                uploader_id=uploader.pk,
                original_filename="history.jsonl",
                sha256=digest,
                status="succeeded",
                imported_sessions=1,
            )
        HistorySession.objects.create(owner_id=owner.pk, external_id="session")

    def tearDown(self):
        old_apps = self.migrate_to_old_state()
        old_apps.get_model("history", "HistoryMessage").objects.all().delete()
        old_apps.get_model("history", "HistorySession").objects.all().delete()
        old_apps.get_model("history", "ImportBatch").objects.all().delete()
        old_apps.get_model("auth", "User").objects.all().delete()
        self.migrate_to_new_state()
        super().tearDown()

    def test_migration_rejects_ambiguous_uploader_attribution(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one successful uploader"):
            self.migrate_to_new_state()


class InvalidParentMigrationTestBase(MigrationTestBase):
    expected_error = ""

    def setUp(self):
        super().setUp()
        self.old_apps = self.migrate_to_old_state()
        User = self.old_apps.get_model("auth", "User")
        ImportBatch = self.old_apps.get_model("history", "ImportBatch")
        self.HistorySession = self.old_apps.get_model("history", "HistorySession")
        self.owner = User.objects.create(username="owner")
        uploader = User.objects.create(username="uploader")
        ImportBatch.objects.create(
            owner_id=self.owner.pk,
            uploader_id=uploader.pk,
            original_filename="history.jsonl",
            sha256="a" * 64,
            status="succeeded",
            imported_sessions=3,
        )

    def tearDown(self):
        old_apps = self.migrate_to_old_state()
        old_apps.get_model("history", "HistoryMessage").objects.all().delete()
        old_apps.get_model("history", "HistorySession").objects.all().delete()
        old_apps.get_model("history", "ImportBatch").objects.all().delete()
        old_apps.get_model("auth", "User").objects.all().delete()
        self.migrate_to_new_state()
        super().tearDown()

    def assert_migration_blocked(self):
        with self.assertRaisesRegex(RuntimeError, self.expected_error):
            self.migrate_to_new_state()


class OrphanParentMigrationTests(InvalidParentMigrationTestBase):
    expected_error = "missing parent"

    def setUp(self):
        super().setUp()
        self.HistorySession.objects.create(
            owner_id=self.owner.pk,
            external_id="orphan",
            raw_metadata={"parent_session_id": "missing"},
        )

    def test_orphan_parent_blocks_migration(self):
        self.assert_migration_blocked()


class CyclicParentMigrationTests(InvalidParentMigrationTestBase):
    expected_error = "cycle"

    def setUp(self):
        super().setUp()
        self.HistorySession.objects.create(
            owner_id=self.owner.pk,
            external_id="first",
            raw_metadata={"parent_session_id": "second"},
        )
        self.HistorySession.objects.create(
            owner_id=self.owner.pk,
            external_id="second",
            raw_metadata={"parent_session_id": "first"},
        )

    def test_parent_cycle_blocks_migration(self):
        self.assert_migration_blocked()


class DeepParentMigrationTests(InvalidParentMigrationTestBase):
    expected_error = "one subagent level"

    def setUp(self):
        super().setUp()
        self.HistorySession.objects.create(
            owner_id=self.owner.pk,
            external_id="root",
            raw_metadata={},
        )
        self.HistorySession.objects.create(
            owner_id=self.owner.pk,
            external_id="child",
            raw_metadata={"parent_session_id": "root"},
        )
        self.HistorySession.objects.create(
            owner_id=self.owner.pk,
            external_id="grandchild",
            raw_metadata={"parent_session_id": "child"},
        )

    def test_deep_parent_chain_blocks_migration(self):
        self.assert_migration_blocked()


class TokenUsageMigrationTests(TransactionTestCase):
    migrate_from = [("history", "0003_usermemorypool")]
    migrate_to = [("history", "0004_historysession_usage")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("auth", "User")
        HistorySession = old_apps.get_model("history", "HistorySession")
        owner = User.objects.create(username="usage-owner")
        HistorySession.objects.create(
            owner_id=owner.pk,
            uploader_id=owner.pk,
            external_id="usage-session",
            raw_metadata={
                "input_tokens": 123_456,
                "output_tokens": 7_890,
                "cache_read_tokens": 222_333,
                "cache_write_tokens": 444,
                "reasoning_tokens": 5_678,
            },
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def test_existing_raw_metadata_is_backfilled_into_usage_columns(self):
        HistorySession = self.apps.get_model("history", "HistorySession")

        session = HistorySession.objects.get(external_id="usage-session")
        self.assertEqual(session.input_tokens, 123_456)
        self.assertEqual(session.output_tokens, 7_890)
        self.assertEqual(session.cache_read_tokens, 222_333)
        self.assertEqual(session.cache_write_tokens, 444)
        self.assertEqual(session.reasoning_tokens, 5_678)


class AccountIdentityMigrationTests(TransactionTestCase):
    migrate_from = [("history", "0005_trace_upload_token")]
    migrate_to = [("history", "0006_account_identity_client_session")]

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def test_existing_users_receive_distinct_uuid4_account_ids(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("auth", "User")
        first = User.objects.create(username="first-existing")
        second = User.objects.create(username="second-existing")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        Identity = apps.get_model("history", "AccountIdentity")
        rows = list(
            Identity.objects.order_by("user_id").values_list(
                "user_id", "account_id", "state"
            )
        )

        self.assertEqual([row[0] for row in rows], [first.pk, second.pk])
        self.assertEqual(len({row[1] for row in rows}), 2)
        self.assertTrue(all(row[1].version == 4 for row in rows))
        self.assertEqual([row[2] for row in rows], ["active", "active"])
