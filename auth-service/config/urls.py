from django.urls import include, path

from history import trace_views
from history.admin import admin_site
from history.auth_views import (
    HermesLoginView,
    HermesLogoutView,
    client_session,
    trace_token,
    trace_token_introspect,
    trace_token_revoke_device,
)

urlpatterns = [
    path("admin/", admin_site.urls),
    path("auth/login/", HermesLoginView.as_view(), name="login"),
    path("auth/logout/", HermesLogoutView.as_view(), name="logout"),
    path("auth/api/session/", client_session, name="client-session"),
    path("auth/api/trace-token/", trace_token, name="trace-token"),
    path(
        "auth/api/trace-token/revoke-device/",
        trace_token_revoke_device,
        name="trace-token-revoke-device",
    ),
    path(
        "internal/trace-token/introspect/",
        trace_token_introspect,
        name="trace-token-introspect",
    ),
    path("traces/", trace_views.dashboard, name="trace-dashboard"),
    path(
        "traces/models/",
        trace_views.model_analytics,
        name="trace-model-analytics",
    ),
    path(
        "traces/session/<str:session_id>/",
        trace_views.session_detail,
        name="trace-session-detail",
    ),
    path(
        "traces/trace/<str:trace_id>/",
        trace_views.trace_detail,
        name="trace-detail",
    ),
    path("", include("history.urls")),
]
