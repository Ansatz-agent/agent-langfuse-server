from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group

from .models import HistoryMessage, HistorySession, ImportBatch


class SuperuserAdminSite(admin.AdminSite):
    site_header = "Agent History 管理后台"
    site_title = "Agent History 管理后台"
    index_title = "账号与历史管理"

    def has_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)


admin_site = SuperuserAdminSite(name="admin")


class ReadOnlyAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HistorySession, site=admin_site)
class HistorySessionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "external_id",
        "owner",
        "uploader",
        "parent_session",
        "title",
        "source",
        "model",
        "started_at",
        "message_count",
        "input_tokens",
        "output_tokens",
    )
    list_filter = ("source", "model", "uploader")
    search_fields = (
        "external_id",
        "title",
        "owner__username",
        "uploader__username",
        "messages__content",
    )
    readonly_fields = [field.name for field in HistorySession._meta.fields]


@admin.register(HistoryMessage, site=admin_site)
class HistoryMessageAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("session", "role", "tool_name", "timestamp")
    list_filter = ("role", "tool_name")
    search_fields = ("content", "session__external_id", "session__owner__username")
    readonly_fields = [field.name for field in HistoryMessage._meta.fields]


@admin.register(ImportBatch, site=admin_site)
class ImportBatchAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "original_filename",
        "owner",
        "uploader",
        "status",
        "created_at",
        "imported_sessions",
    )
    list_filter = ("status",)
    search_fields = ("original_filename", "owner__username", "uploader__username", "sha256")
    readonly_fields = [field.name for field in ImportBatch._meta.fields]


admin_site.register(get_user_model(), UserAdmin)
admin_site.register(Group)
