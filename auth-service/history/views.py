import json
import logging
import secrets
from datetime import datetime, timedelta
from itertools import chain

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import (
    BigIntegerField,
    Count,
    F,
    IntegerField,
    Max,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import is_aware, make_naive
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .auth_views import _json_payload, _json_response, hermes_session_required
from .forms import HistoryImportForm, MemoryPoolForm
from .importer import ImportValidationError, import_history
from .memory_service import (
    MemoryIdentityError,
    MemoryNotFound,
    MemoryUnavailable,
    delete_all_memories,
    delete_memory,
    list_all_memories,
    list_memories,
    search_memories,
)
from .models import HistoryMessage, HistorySession, MemoryIngestJob, UserMemoryPool
from .presentation import build_history_presentation, render_message_markdown
from .usage import build_context_allocation

PAGE_SIZE = 25
logger = logging.getLogger(__name__)
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def healthz(request):
    response = JsonResponse({"status": "ok"})
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def memory_catalog_internal(request):
    """Serve the Langfuse UI without exposing the Mem0 database publicly."""
    expected = getattr(settings, "MEMORY_INTERNAL_TOKEN", "")
    supplied = request.headers.get("X-Memory-Internal-Token", "")
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        return JsonResponse({"detail": "forbidden"}, status=403)

    admin = (
        get_user_model()
        .objects.filter(is_active=True, is_superuser=True)
        .order_by("pk")
        .first()
    )
    if admin is None:
        return JsonResponse({"detail": "memory_catalog_unavailable"}, status=503)
    try:
        memories = list_all_memories(requester=admin)
    except Exception as exc:
        logger.exception("Internal memory catalog request failed")
        return JsonResponse({"detail": str(exc)}, status=503)

    results = []
    for item in memories:
        session = item.get("session")
        results.append(
            {
                "id": item.get("id"),
                "memory": item.get("memory", ""),
                "user": item.get("user", ""),
                "created_at": item.get("created_at"),
                "tags": item.get("tags", []),
                "session": (
                    {
                        "id": session.external_id,
                        "title": session.title or session.external_id,
                        "started_at": _iso(session.started_at),
                    }
                    if session
                    else None
                ),
            }
        )
    return JsonResponse({"results": results})


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if is_aware(value):
        return value.isoformat()
    return make_naive(value).isoformat()


def _session_row(session: HistorySession, *, include_messages: bool = True) -> dict:
    row = {
        "id": session.external_id,
        "title": session.title,
        "source": session.source,
        "model": session.model,
        "started_at": _iso(session.started_at),
        "ended_at": _iso(session.ended_at),
        "end_reason": session.end_reason,
        "message_count": session.message_count,
        "tool_call_count": session.tool_call_count,
        **{field: getattr(session, field) for field in USAGE_FIELDS},
        "uploaded_by": session.uploader.username if session.uploader_id else None,
        "metadata": session.raw_metadata,
    }
    if include_messages:
        row["messages"] = [_message_row(message) for message in session.messages.all()]
        row["subagent_threads"] = [
            _session_row(thread, include_messages=True)
            for thread in getattr(session, "visible_subagent_threads", [])
        ]
    return row


def _message_row(message: HistoryMessage) -> dict:
    return {
        "id": message.source_message_id or str(message.pk),
        "role": message.role,
        "content": message.content,
        "timestamp": _iso(message.timestamp),
        "tool_name": message.tool_name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": message.tool_calls,
        "metadata": message.raw_metadata,
    }


def _stream_jsonl(sessions):
    for session in sessions.iterator(chunk_size=25):
        yield json.dumps(_session_row(session), ensure_ascii=False, separators=(",", ":")) + "\n"


def _visible_subagent_threads(user):
    return (
        HistorySession.objects.visible_to(user)
        .filter(
            parent_session__isnull=False,
            owner_id=F("parent_session__owner_id"),
        )
        .select_related("uploader")
    )


def _with_subagent_totals(sessions):
    child_stats = (
        HistorySession.objects.filter(
            parent_session_id=OuterRef("pk"),
            owner_id=OuterRef("owner_id"),
        )
        .values("parent_session_id")
        .annotate(
            thread_count=Count("pk"),
            message_count=Sum("message_count"),
            tool_call_count=Sum("tool_call_count"),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
            cache_read_tokens=Sum("cache_read_tokens"),
            cache_write_tokens=Sum("cache_write_tokens"),
            reasoning_tokens=Sum("reasoning_tokens"),
        )
    )
    return sessions.annotate(
        subagent_thread_count=Coalesce(
            Subquery(child_stats.values("thread_count")[:1], output_field=IntegerField()),
            Value(0),
        ),
        subagent_message_count=Coalesce(
            Subquery(child_stats.values("message_count")[:1], output_field=IntegerField()),
            Value(0),
        ),
        subagent_tool_call_count=Coalesce(
            Subquery(child_stats.values("tool_call_count")[:1], output_field=IntegerField()),
            Value(0),
        ),
        subagent_input_tokens=Coalesce(
            Subquery(child_stats.values("input_tokens")[:1], output_field=BigIntegerField()),
            Value(0),
        ),
        subagent_output_tokens=Coalesce(
            Subquery(child_stats.values("output_tokens")[:1], output_field=BigIntegerField()),
            Value(0),
        ),
        subagent_cache_read_tokens=Coalesce(
            Subquery(child_stats.values("cache_read_tokens")[:1], output_field=BigIntegerField()),
            Value(0),
        ),
        subagent_cache_write_tokens=Coalesce(
            Subquery(child_stats.values("cache_write_tokens")[:1], output_field=BigIntegerField()),
            Value(0),
        ),
        subagent_reasoning_tokens=Coalesce(
            Subquery(child_stats.values("reasoning_tokens")[:1], output_field=BigIntegerField()),
            Value(0),
        ),
    ).annotate(
        total_message_count=F("message_count") + F("subagent_message_count"),
        total_tool_call_count=F("tool_call_count") + F("subagent_tool_call_count"),
        total_input_tokens=F("input_tokens") + F("subagent_input_tokens"),
        total_output_tokens=F("output_tokens") + F("subagent_output_tokens"),
        total_cache_read_tokens=F("cache_read_tokens") + F("subagent_cache_read_tokens"),
        total_cache_write_tokens=F("cache_write_tokens") + F("subagent_cache_write_tokens"),
        total_reasoning_tokens=F("reasoning_tokens") + F("subagent_reasoning_tokens"),
    )


def _memory_call_count(sessions) -> int:
    total = 0
    tool_call_rows = HistoryMessage.objects.filter(
        session__in=sessions,
        role__iexact="assistant",
    ).values_list("tool_calls", flat=True)
    for tool_calls in tool_call_rows.iterator(chunk_size=500):
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            name = tool_call.get("name")
            function = tool_call.get("function")
            if not isinstance(name, str) and isinstance(function, dict):
                name = function.get("name")
            if isinstance(name, str) and name.strip().lower() == "memory":
                total += 1
    return total


@hermes_session_required
def dashboard(request):
    visible_sessions = HistorySession.objects.visible_to(request.user)
    root_sessions = visible_sessions.filter(parent_session__isnull=True)
    visible_threads = _visible_subagent_threads(request.user)
    dashboard_sessions = visible_sessions.filter(
        Q(parent_session__isnull=True) | Q(pk__in=visible_threads.values("pk"))
    )
    totals = dashboard_sessions.aggregate(
        messages=Coalesce(Sum("message_count"), Value(0)),
        tool_calls=Coalesce(Sum("tool_call_count"), Value(0)),
    )
    recent_sessions = list(
        _with_subagent_totals(root_sessions.select_related("uploader")).order_by(
            "-started_at", "-imported_at", "external_id", "pk"
        )[:5]
    )

    today = timezone.localdate()
    first_day = today - timedelta(days=6)
    activity = {first_day + timedelta(days=offset): 0 for offset in range(7)}
    for started_at, imported_at in root_sessions.values_list("started_at", "imported_at"):
        point = started_at or imported_at
        if point is None:
            continue
        point_day = timezone.localtime(point).date() if is_aware(point) else point.date()
        if point_day in activity:
            activity[point_day] += 1
    activity_peak = max([*activity.values(), 1])
    activity_series = [
        {
            "date": day,
            "count": count,
            "height": max(8, round(count / activity_peak * 100)),
        }
        for day, count in activity.items()
    ]

    dashboard_stats = {
        "sessions": root_sessions.count(),
        "threads": visible_threads.count(),
        "messages": totals["messages"],
        "tool_calls": totals["tool_calls"],
        "memory_calls": _memory_call_count(dashboard_sessions),
    }
    return render(
        request,
        "history/dashboard.html",
        {
            "dashboard_stats": dashboard_stats,
            "recent_sessions": recent_sessions,
            "activity_series": activity_series,
        },
    )


@hermes_session_required
def usage_dashboard(request):
    visible_sessions = HistorySession.objects.visible_to(request.user)
    root_sessions = visible_sessions.filter(parent_session__isnull=True)
    visible_threads = _visible_subagent_threads(request.user)
    usage_sessions = visible_sessions.filter(
        Q(parent_session__isnull=True) | Q(pk__in=visible_threads.values("pk"))
    )
    usage_stats = usage_sessions.aggregate(
        input_tokens=Coalesce(Sum("input_tokens"), Value(0)),
        output_tokens=Coalesce(Sum("output_tokens"), Value(0)),
        cache_read_tokens=Coalesce(Sum("cache_read_tokens"), Value(0)),
        cache_write_tokens=Coalesce(Sum("cache_write_tokens"), Value(0)),
        reasoning_tokens=Coalesce(Sum("reasoning_tokens"), Value(0)),
        latest_imported_at=Max("imported_at"),
    )
    usage_stats.update(
        sessions=root_sessions.count(),
        threads=visible_threads.count(),
    )
    sessions = _with_subagent_totals(root_sessions.select_related("uploader")).order_by(
        "-started_at", "-imported_at", "external_id", "pk"
    )
    paginator = Paginator(sessions, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", "1"))
    return render(
        request,
        "history/usage_dashboard.html",
        {"usage_stats": usage_stats, "sessions": page_obj.object_list, "page_obj": page_obj},
    )


@hermes_session_required
def history_synthesis(request):
    return render(request, "history/history_synthesis.html")


@hermes_session_required
@require_GET
def history_synthesis_status(request):
    response = JsonResponse(
        {
            "schema_version": "v1",
            "feature": "history_synthesis",
            "status": "reserved",
            "available": False,
            "accepting_requests": False,
            "planned_stages": [
                "candidate_selection",
                "critic_eligibility_review",
                "common_process_synthesis",
                "evidence_linking",
                "human_review",
            ],
            "create_endpoint": reverse("history:history-synthesis-runs"),
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@hermes_session_required
@require_POST
def history_synthesis_runs(request):
    response = JsonResponse(
        {
            "schema_version": "v1",
            "feature": "history_synthesis",
            "error": "feature_not_available",
            "status": "reserved",
            "writes_performed": False,
        },
        status=503,
    )
    response["Cache-Control"] = "no-store"
    return response


@hermes_session_required
def api_credits(request):
    return render(request, "history/api_credits.html")


@hermes_session_required
@require_GET
def api_credits_status(request):
    response = JsonResponse(
        {
            "schema_version": "v1",
            "feature": "api_credits",
            "status": "reserved",
            "available": False,
            "accepting_orders": False,
            "planned_providers": ["deepseek", "qwen"],
            "planned_delivery": "desktop_secure_activation",
            "create_endpoint": reverse("history:api-credit-orders"),
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@hermes_session_required
@require_POST
def api_credit_orders(request):
    response = JsonResponse(
        {
            "schema_version": "v1",
            "feature": "api_credits",
            "error": "feature_not_available",
            "status": "reserved",
            "writes_performed": False,
        },
        status=503,
    )
    response["Cache-Control"] = "no-store"
    return response


@hermes_session_required
def session_list(request):
    query = request.GET.get("q", "").strip()
    visible_sessions = HistorySession.objects.visible_to(request.user).filter(
        parent_session__isnull=True
    )
    visible_threads = _visible_subagent_threads(request.user).filter(
        parent_session__in=visible_sessions
    )
    uploader_options = list(
        get_user_model()
        .objects.filter(
            Q(uploaded_history_sessions__in=visible_sessions)
            | Q(uploaded_history_sessions__in=visible_threads)
        )
        .distinct()
        .order_by("username")
    )
    available_uploader_ids = {uploader.pk for uploader in uploader_options}
    requested_uploader_values = [
        value.strip() for value in request.GET.getlist("uploader") if value.strip()
    ]
    requested_uploader_ids = {
        int(value) for value in requested_uploader_values if value.isdecimal()
    }
    uploader_filter_invalid = any(
        not value.isdecimal() for value in requested_uploader_values
    ) or not requested_uploader_ids.issubset(available_uploader_ids)
    selected_uploader_ids = requested_uploader_ids & available_uploader_ids
    sessions = _with_subagent_totals(visible_sessions.select_related("uploader"))
    if uploader_filter_invalid:
        sessions = sessions.none()
    elif selected_uploader_ids:
        sessions = sessions.filter(
            Q(uploader_id__in=selected_uploader_ids)
            | Q(
                subagent_threads__in=visible_threads,
                subagent_threads__uploader_id__in=selected_uploader_ids,
            )
        ).distinct()
    if query:
        subagent_match = Q(subagent_threads__in=visible_threads) & (
            Q(subagent_threads__external_id__icontains=query)
            | Q(subagent_threads__title__icontains=query)
            | Q(subagent_threads__source__icontains=query)
            | Q(subagent_threads__model__icontains=query)
            | Q(subagent_threads__messages__content__icontains=query)
        )
        sessions = sessions.filter(
            Q(external_id__icontains=query)
            | Q(title__icontains=query)
            | Q(source__icontains=query)
            | Q(model__icontains=query)
            | Q(messages__content__icontains=query)
            | subagent_match
        ).distinct()
    sessions = sessions.order_by("-started_at", "-imported_at", "external_id", "pk")
    paginator = Paginator(sessions, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", "1"))
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    pagination_params.setlist("uploader", [str(value) for value in sorted(selected_uploader_ids)])
    if query:
        pagination_params["q"] = query
    else:
        pagination_params.pop("q", None)
    pagination_query = pagination_params.urlencode()
    return render(
        request,
        "history/session_list.html",
        {
            "sessions": page_obj.object_list,
            "page_obj": page_obj,
            "query": query,
            "uploader_options": uploader_options,
            "selected_uploader_ids": selected_uploader_ids,
            "pagination_query": pagination_query,
        },
    )


@hermes_session_required
def session_detail(request, pk: int):
    thread_prefetch = Prefetch(
        "subagent_threads",
        queryset=_visible_subagent_threads(request.user).prefetch_related("messages"),
        to_attr="visible_subagent_threads",
    )
    session = get_object_or_404(
        HistorySession.objects.visible_to(request.user)
        .select_related("uploader", "parent_session")
        .prefetch_related("messages", thread_prefetch),
        pk=pk,
    )
    if session.parent_session_id:
        parent = session.parent_session
        if (
            parent is None
            or parent.owner_id != session.owner_id
            or parent.parent_session_id is not None
        ):
            raise Http404("Invalid subagent thread relationship")
        target = reverse("history:session-detail", args=[parent.pk])
        return redirect(f"{target}#thread-{session.pk}")
    root_messages = list(session.messages.all())
    history_presentation = build_history_presentation(root_messages)
    all_message_groups = [root_messages]
    for thread in session.visible_subagent_threads:
        thread_messages = list(thread.messages.all())
        thread.history_presentation = build_history_presentation(thread_messages)
        thread.context_allocation = build_context_allocation(thread_messages)
        all_message_groups.append(thread_messages)
    related_sessions = [session, *session.visible_subagent_threads]
    session_usage = {
        field: sum(getattr(item, field) for item in related_sessions) for field in USAGE_FIELDS
    }
    return render(
        request,
        "history/session_detail.html",
        {
            "session": session,
            "history_presentation": history_presentation,
            "session_usage": session_usage,
            "context_allocation": build_context_allocation(chain.from_iterable(all_message_groups)),
        },
    )


@hermes_session_required
@require_GET
def session_export(request):
    thread_prefetch = Prefetch(
        "subagent_threads",
        queryset=_visible_subagent_threads(request.user).prefetch_related("messages"),
        to_attr="visible_subagent_threads",
    )
    sessions = (
        HistorySession.objects.visible_to(request.user)
        .filter(parent_session__isnull=True)
        .select_related("uploader")
        .prefetch_related("messages", thread_prefetch)
        .order_by("owner_id", "started_at", "external_id")
    )
    response = StreamingHttpResponse(_stream_jsonl(sessions), content_type="application/x-ndjson")
    response["Content-Disposition"] = 'attachment; filename="my-agent-history.jsonl"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@hermes_session_required
def session_import(request):
    form = HistoryImportForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        owner = request.user
        if request.user.is_superuser:
            owner = form.cleaned_data.get("owner_id") or request.user
        try:
            result = import_history(
                form.cleaned_data["history_file"], owner=owner, uploader=request.user
            )
        except ImportValidationError as exc:
            form.add_error(None, str(exc))
            return render(request, "history/import.html", {"form": form}, status=400)
        messages.success(
            request,
            "导入完成：新增 %s 个会话、%s 条消息，跳过 %s 个重复会话。"
            % (result.imported_sessions, result.imported_messages, result.skipped_sessions),
        )
        return redirect("history:session-list")
    return render(request, "history/import.html", {"form": form})


@hermes_session_required
def memory_pool(request):
    pool, _ = UserMemoryPool.objects.get_or_create(owner=request.user)
    form = MemoryPoolForm(
        request.POST or None,
        request.FILES or None,
        initial={
            "memory_markdown": pool.memory_markdown,
            "user_markdown": pool.user_markdown,
        },
    )

    if request.method == "POST" and form.is_valid():
        update_fields = []
        for field_name in ("memory_markdown", "user_markdown"):
            value = form.cleaned_data[field_name]
            if value is not None:
                setattr(pool, field_name, value)
                update_fields.append(field_name)
        if update_fields:
            pool.save(update_fields=[*update_fields, "updated_at"])
        messages.success(request, "Memory pool 已保存到当前账号。")
        return redirect("history:memory-pool")
    return render(
        request,
        "history/memory_pool.html",
        {
            "form": form,
            "pool": pool,
            "memory_html": render_message_markdown(pool.memory_markdown),
            "user_html": render_message_markdown(pool.user_markdown),
        },
    )


def _memory_api_error(error: Exception):
    if isinstance(error, MemoryIdentityError):
        return _json_response({"detail": str(error)}, status=409)
    if isinstance(error, MemoryNotFound):
        return _json_response({"detail": "memory_not_found"}, status=404)
    if isinstance(error, MemoryUnavailable):
        return _json_response({"detail": "memory_unavailable"}, status=503)
    if isinstance(error, ValueError):
        return _json_response({"detail": str(error)}, status=400)
    logger.exception("Unexpected Mem0 request failure")
    return _json_response({"detail": "memory_unavailable"}, status=503)


@hermes_session_required
@require_POST
def memory_search_api(request):
    payload, error = _json_payload(request)
    if error:
        return error
    query = payload.get("query")
    if not isinstance(query, str):
        return _json_response({"detail": "query_required"}, status=400)
    try:
        limit = payload.get("limit", 5)
        results = search_memories(user=request.user, query=query, limit=int(limit))
    except Exception as exc:
        return _memory_api_error(exc)
    return _json_response({"results": results})


@hermes_session_required
@require_GET
def memory_list_api(request):
    try:
        results = list_memories(user=request.user)
    except Exception as exc:
        return _memory_api_error(exc)
    return _json_response({"results": results})


@hermes_session_required
@require_http_methods(["DELETE"])
def memory_delete_api(request, memory_id: str):
    try:
        delete_memory(user=request.user, memory_id=memory_id)
    except Exception as exc:
        return _memory_api_error(exc)
    return _json_response({"deleted": True})


@hermes_session_required
@require_http_methods(["DELETE"])
def memory_delete_all_api(request):
    try:
        delete_all_memories(user=request.user)
        MemoryIngestJob.objects.filter(owner=request.user).update(
            status=MemoryIngestJob.Status.DELETED,
            mem0_memory_ids=[],
        )
    except Exception as exc:
        return _memory_api_error(exc)
    return _json_response({"deleted": True, "scope": "user"})
