from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group

from .client_sessions import (
    disable_account,
    revoke_account,
    revoke_client_session,
)
from .models import AccountIdentity, ClientSession, HistoryMessage, HistorySession, ImportBatch


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


@admin.action(description="Revoke selected native Sessions")
def revoke_sessions(modeladmin, request, queryset):
    for session in queryset.select_related("account"):
        revoke_client_session(
            session=session,
            reason=ClientSession.RevocationReason.SESSION_REVOKED,
        )


@admin.action(description="Disable selected accounts")
def disable_accounts(modeladmin, request, queryset):
    for user in queryset:
        disable_account(user=user)


@admin.action(description="Revoke selected accounts")
def revoke_accounts(modeladmin, request, queryset):
    for account in queryset:
        revoke_account(account=account)


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


@admin.register(ClientSession, site=admin_site)
class ClientSessionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    actions = (revoke_sessions,)
    list_display = (
        "session_id",
        "account",
        "installation_id",
        "client_version",
        "created_at",
        "last_seen_at",
        "revoked_at",
        "revocation_reason",
    )
    list_filter = ("revocation_reason",)
    search_fields = ("session_id", "account__account_id", "account__user__username")
    readonly_fields = [field.name for field in ClientSession._meta.fields]


@admin.register(AccountIdentity, site=admin_site)
class AccountIdentityAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    actions = (revoke_accounts,)
    list_display = (
        "account_id",
        "user",
        "state",
        "revoked_at",
        "revocation_reason",
        "created_at",
    )
    list_filter = ("state",)
    search_fields = ("account_id", "user__username")
    readonly_fields = [field.name for field in AccountIdentity._meta.fields]


class HermesUserAdmin(UserAdmin):
    actions = (disable_accounts,)


admin_site.register(get_user_model(), HermesUserAdmin)
admin_site.register(Group)
