from django.urls import include, path

from history.admin import admin_site
from history.auth_views import HermesLoginView, HermesLogoutView, client_session

urlpatterns = [
    path("admin/", admin_site.urls),
    path("accounts/login/", HermesLoginView.as_view(), name="login"),
    path("accounts/logout/", HermesLogoutView.as_view(), name="logout"),
    path("api/session/", client_session, name="client-session"),
    path("", include("history.urls")),
]
