from django.urls import include, path

from history import trace_views
from history.admin import admin_site
from history.auth_views import (
    HermesLoginView,
    HermesLogoutView,
    client_session,
    native_client_session,
    native_client_session_current,
    native_trace_token,
    trace_token,
    trace_token_introspect,
    trace_token_revoke_device,
)

urlpatterns = [
    path("admin/", admin_site.urls),
    path("auth/login/", HermesLoginView.as_view(), name="login"),
    path("auth/logout/", HermesLogoutView.as_view(), name="logout"),
    path("auth/api/session/", client_session, name="client-session"),
    path(
        "auth/api/client-session/",
        native_client_session,
        name="native-client-session",
    ),
    path(
        "auth/api/client-session/current/",
        native_client_session_current,
        name="native-client-session-current",
    ),
    path(
        "auth/api/client-session/trace-token/",
        native_trace_token,
        name="native-trace-token",
    ),
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
        "traces/sessions/",
        trace_views.trace_index,
        name="trace-index",
    ),
    path(
        "traces/runs/",
        trace_views.trace_runs_legacy,
        name="trace-runs-legacy",
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
    path(
        "traces/trace/<str:trace_id>/step/<str:observation_id>/",
        trace_views.trace_step_fragment,
        name="trace-step-fragment",
    ),
    path("", include("history.urls")),
]
