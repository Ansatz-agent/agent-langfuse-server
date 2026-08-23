import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from history.models import HistoryMessage, HistorySession


class SessionThreadDisplayTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="history-owner", password="safe-test-pass-1")
        self.uploader = User.objects.create_superuser(
            username="upload-admin", password="safe-test-pass-2", email="admin@example.test"
        )
        self.parent = HistorySession.objects.create(
            owner=self.owner,
            uploader=self.uploader,
            external_id="parent-session",
            title="Parent session title",
            message_count=1,
        )
        self.child = HistorySession.objects.create(
            owner=self.owner,
            uploader=self.uploader,
            parent_session=self.parent,
            external_id="child-thread",
            title="Subagent thread title",
            message_count=1,
        )
        HistoryMessage.objects.create(
            session=self.parent,
            role="user",
            content="parent message",
        )
        HistoryMessage.objects.create(
            session=self.child,
            role="assistant",
            content="subagent message",
        )

    def login_as_owner(self):
        response = self.client.post(
            reverse("login"),
            {"username": "history-owner", "password": "safe-test-pass-1"},
        )
        self.assertEqual(response.status_code, 302)

    def test_list_shows_parent_session_but_not_child_as_independent_history(self):
        self.login_as_owner()

        response = self.client.get(reverse("history:session-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["sessions"]), [self.parent])
        self.assertContains(response, "Parent session title")
        self.assertNotContains(response, "Subagent thread title")

    def test_parent_detail_embeds_subagent_thread_and_messages(self):
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.parent.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parent session title")
        self.assertContains(response, "parent message")
        self.assertContains(response, "Subagent thread title")
        self.assertContains(response, "subagent message")

    def test_child_detail_redirects_to_parent_thread_anchor(self):
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.child.pk]))

        expected = reverse("history:session-detail", args=[self.parent.pk])
        self.assertRedirects(
            response,
            f"{expected}#thread-{self.child.pk}",
            fetch_redirect_response=False,
        )

    def test_session_card_displays_uploader_tag(self):
        self.login_as_owner()

        response = self.client.get(reverse("history:session-list"))

        self.assertContains(response, "上传者：upload-admin")

    def test_session_card_aggregates_thread_and_message_counts(self):
        self.login_as_owner()

        response = self.client.get(reverse("history:session-list"))

        self.assertContains(response, "2 条消息")
        self.assertContains(response, "1 个 subagent thread")

    def test_detail_displays_uploader_tag_for_parent_and_thread(self):
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[self.parent.pk]))

        self.assertContains(response, "上传者：upload-admin", count=2)

    def test_searching_subagent_message_returns_parent_session(self):
        self.login_as_owner()

        response = self.client.get(reverse("history:session-list"), {"q": "subagent message"})

        self.assertEqual(list(response.context["sessions"]), [self.parent])
        self.assertContains(response, "Parent session title")
        self.assertNotContains(response, "Subagent thread title")

    def test_export_nests_subagent_threads_under_parent_session(self):
        self.parent.input_tokens = 123_456
        self.parent.output_tokens = 7_890
        self.parent.reasoning_tokens = 456
        self.parent.save(update_fields=["input_tokens", "output_tokens", "reasoning_tokens"])
        self.login_as_owner()

        response = self.client.get(reverse("history:session-export"))
        rows = [
            json.loads(line)
            for line in b"".join(response.streaming_content).decode().splitlines()
            if line
        ]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "parent-session")
        self.assertEqual(rows[0]["uploaded_by"], "upload-admin")
        self.assertEqual(rows[0]["input_tokens"], 123_456)
        self.assertEqual(rows[0]["output_tokens"], 7_890)
        self.assertEqual(rows[0]["reasoning_tokens"], 456)
        self.assertEqual([row["id"] for row in rows[0]["subagent_threads"]], ["child-thread"])
        self.assertEqual(rows[0]["subagent_threads"][0]["uploaded_by"], "upload-admin")

    def test_uploader_sidebar_supports_multi_select_filtering(self):
        User = get_user_model()
        second_uploader = User.objects.create_user(username="second-uploader")
        third_uploader = User.objects.create_user(username="third-uploader")
        second = HistorySession.objects.create(
            owner=self.owner,
            uploader=second_uploader,
            external_id="second-session",
            title="Second uploader session",
        )
        HistorySession.objects.create(
            owner=self.owner,
            uploader=third_uploader,
            external_id="third-session",
            title="Third uploader session",
        )
        self.login_as_owner()

        response = self.client.get(
            reverse("history:session-list"),
            {"uploader": [self.uploader.pk, second_uploader.pk]},
        )

        self.assertEqual(
            {session.pk for session in response.context["sessions"]},
            {self.parent.pk, second.pk},
        )
        self.assertEqual(
            {user.username for user in response.context["uploader_options"]},
            {"upload-admin", "second-uploader", "third-uploader"},
        )
        self.assertEqual(
            response.context["selected_uploader_ids"],
            {self.uploader.pk, second_uploader.pk},
        )
        self.assertContains(response, 'type="checkbox" name="uploader"', count=3)

    def test_filter_by_thread_uploader_returns_parent_session(self):
        User = get_user_model()
        thread_uploader = User.objects.create_user(username="thread-uploader")
        self.child.uploader = thread_uploader
        self.child.save(update_fields=["uploader"])
        self.login_as_owner()

        response = self.client.get(
            reverse("history:session-list"), {"uploader": thread_uploader.pk}
        )

        self.assertEqual(list(response.context["sessions"]), [self.parent])
        self.assertContains(response, "thread-uploader")

    def test_pagination_preserves_search_and_repeated_uploader_parameters(self):
        User = get_user_model()
        second_uploader = User.objects.create_user(username="page-uploader")
        for index in range(30):
            HistorySession.objects.create(
                owner=self.owner,
                uploader=second_uploader,
                external_id=f"paged-{index:02d}",
                title=f"Paged title {index:02d}",
            )
        self.login_as_owner()

        response = self.client.get(
            reverse("history:session-list"),
            {
                "q": "Paged",
                "uploader": [self.uploader.pk, second_uploader.pk],
                "page": 2,
            },
        )

        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertContains(
            response,
            f"q=Paged&amp;uploader={self.uploader.pk}&amp;uploader={second_uploader.pk}&amp;page=1",
            html=False,
        )

    def test_foreign_owner_thread_is_excluded_from_every_parent_surface(self):
        User = get_user_model()
        foreign_owner = User.objects.create_user(username="foreign-owner")
        foreign_uploader = User.objects.create_user(username="foreign-uploader")
        foreign_thread = HistorySession.objects.create(
            owner=foreign_owner,
            uploader=foreign_uploader,
            parent_session=self.parent,
            external_id="foreign-thread",
            title="Foreign thread title",
        )
        HistoryMessage.objects.create(
            session=foreign_thread,
            role="assistant",
            content="foreign-thread-secret",
        )
        self.login_as_owner()

        detail = self.client.get(reverse("history:session-detail", args=[self.parent.pk]))
        search = self.client.get(reverse("history:session-list"), {"q": "foreign-thread-secret"})
        listing = self.client.get(reverse("history:session-list"))
        forged_filter = self.client.get(
            reverse("history:session-list"), {"uploader": foreign_uploader.pk}
        )
        export = self.client.get(reverse("history:session-export"))
        exported = json.loads(b"".join(export.streaming_content).decode().strip())

        self.assertNotContains(detail, "Foreign thread title")
        self.assertEqual(search.context["sessions"].count(), 0)
        self.assertNotContains(listing, "foreign-uploader")
        self.assertEqual(forged_filter.context["sessions"].count(), 0)
        self.assertEqual(
            [thread["id"] for thread in exported["subagent_threads"]],
            ["child-thread"],
        )

    def test_child_with_foreign_parent_returns_not_found_without_redirect(self):
        User = get_user_model()
        foreign_owner = User.objects.create_user(username="foreign-parent-owner")
        foreign_parent = HistorySession.objects.create(
            owner=foreign_owner,
            uploader=self.uploader,
            external_id="foreign-parent",
        )
        child = HistorySession.objects.create(
            owner=self.owner,
            uploader=self.uploader,
            parent_session=foreign_parent,
            external_id="cross-owner-child",
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[child.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Location", response)

    def test_grandchild_detail_is_not_redirected_as_a_valid_thread(self):
        child = HistorySession.objects.create(
            owner=self.owner,
            uploader=self.uploader,
            parent_session=self.parent,
            external_id="second-child",
        )
        grandchild = HistorySession.objects.create(
            owner=self.owner,
            uploader=self.uploader,
            parent_session=child,
            external_id="grandchild",
        )
        self.login_as_owner()

        response = self.client.get(reverse("history:session-detail", args=[grandchild.pk]))

        self.assertEqual(response.status_code, 404)
