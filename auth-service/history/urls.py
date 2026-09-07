from django.urls import path

from . import views

app_name = "history"
urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("internal/memory/catalog/", views.memory_catalog_internal, name="memory-catalog-internal"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("history/usage/", views.usage_dashboard, name="usage-dashboard"),
    path(
        "features/history-synthesis/",
        views.history_synthesis,
        name="history-synthesis",
    ),
    path(
        "api/v1/features/history-synthesis/",
        views.history_synthesis_status,
        name="history-synthesis-status",
    ),
    path(
        "api/v1/features/history-synthesis/runs/",
        views.history_synthesis_runs,
        name="history-synthesis-runs",
    ),
    path(
        "features/api-credits/",
        views.api_credits,
        name="api-credits",
    ),
    path(
        "api/v1/features/api-credits/",
        views.api_credits_status,
        name="api-credits-status",
    ),
    path(
        "api/v1/features/api-credits/orders/",
        views.api_credit_orders,
        name="api-credit-orders",
    ),
    path("history/", views.session_list, name="session-list"),
    path("history/session/<int:pk>/", views.session_detail, name="session-detail"),
    path("history/export/", views.session_export, name="session-export"),
    path("history/import/", views.session_import, name="session-import"),
    path("history/memory/", views.memory_pool, name="memory-pool"),
    path("history/api/v1/memory/search/", views.memory_search_api, name="memory-search-api"),
    path("history/api/v1/memory/", views.memory_list_api, name="memory-list-api"),
    path(
        "history/api/v1/memory/delete-all/",
        views.memory_delete_all_api,
        name="memory-delete-all-api",
    ),
    path(
        "history/api/v1/memory/<str:memory_id>/",
        views.memory_delete_api,
        name="memory-delete-api",
    ),
]
