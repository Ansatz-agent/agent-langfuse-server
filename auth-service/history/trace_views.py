from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .auth_views import hermes_session_required
from .langfuse_client import LangfuseUnavailable, get_langfuse_client
from .trace_analytics import (
    build_dashboard,
    build_model_analytics,
    parse_analytics_query,
)


ALLOWED_DAY_RANGES = {7, 30, 90}


def _number(value) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)
    return parsed if parsed.is_finite() and parsed >= 0 else Decimal(0)


def _usage_total(item: dict) -> int:
    details = item.get("usageDetails")
    if not isinstance(details, dict):
        return 0
    if "total" in details:
        return int(_number(details.get("total")))
    return int(sum((_number(value) for value in details.values()), Decimal(0)))


def _cost_total(item: dict) -> Decimal:
    if item.get("totalCost") is not None:
        return _number(item.get("totalCost"))
    details = item.get("costDetails")
    if not isinstance(details, dict):
        return Decimal(0)
    if "total" in details:
        return _number(details.get("total"))
    return sum((_number(value) for value in details.values()), Decimal(0))


def _parsed_time(value):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    return parsed or timezone.now() - timedelta(days=36500)


def _pretty(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _owned(items: list[dict], user_id: str) -> list[dict]:
    return [item for item in items if str(item.get("userId")) == user_id]


def aggregate_dashboard(items: list[dict], *, days: int, query: str) -> dict:
    """Compatibility wrapper for the original aggregation contract."""
    return build_dashboard(
        items,
        days=days,
        now=timezone.now(),
        query=query,
        metric="tokens",
        granularity="day",
        chart="bar",
    )


def _range(request):
    try:
        days = int(request.GET.get("days", "30"))
    except (TypeError, ValueError):
        days = 30
    if days not in ALLOWED_DAY_RANGES:
        days = 30
    now = timezone.now()
    return days, (now - timedelta(days=days)).isoformat(), now.isoformat()


def _client_list(
    request, *, include_io=False, session_id=None, trace_id=None, days_override=None
):
    days, from_time, to_time = _range(request)
    if days_override is not None:
        days = days_override
        now = timezone.now()
        from_time = (now - timedelta(days=days)).isoformat()
        to_time = now.isoformat()
    items = get_langfuse_client().list_observations(
        user_id=str(request.user.pk),
        days=days,
        from_time=from_time,
        to_time=to_time,
        session_id=session_id,
        trace_id=trace_id,
        include_io=include_io,
    )
    return days, _owned(items, str(request.user.pk))


@hermes_session_required
def dashboard(request):
    state = parse_analytics_query(request.GET, page="dashboard")
    try:
        _, items = _client_list(request, days_override=state.days)
        context = build_dashboard(
            items,
            days=state.days,
            now=timezone.now(),
            query=state.query,
            metric=state.metric,
            granularity=state.granularity,
            chart=state.chart,
        )
        context["unavailable"] = False
        return render(request, "traces/dashboard.html", context)
    except LangfuseUnavailable:
        context = build_dashboard(
            [],
            days=state.days,
            now=timezone.now(),
            query=state.query,
            metric=state.metric,
            granularity=state.granularity,
            chart=state.chart,
        )
        context["unavailable"] = True
        return render(
            request,
            "traces/dashboard.html",
            context,
            status=503,
        )


@hermes_session_required
def model_analytics(request):
    state = parse_analytics_query(request.GET, page="models")
    try:
        _, items = _client_list(request, days_override=state.days)
        context = build_model_analytics(
            items,
            days=state.days,
            now=timezone.now(),
            model=state.model,
            metric=state.metric,
            granularity=state.granularity,
            chart=state.chart,
        )
        context["unavailable"] = False
        return render(request, "traces/model_analytics.html", context)
    except LangfuseUnavailable:
        context = build_model_analytics(
            [],
            days=state.days,
            now=timezone.now(),
            model="all",
            metric=state.metric,
            granularity=state.granularity,
            chart=state.chart,
        )
        context["unavailable"] = True
        return render(request, "traces/model_analytics.html", context, status=503)


def _decorate_observations(items: list[dict]) -> list[dict]:
    decorated = []
    for item in sorted(items, key=lambda value: _parsed_time(value.get("startTime"))):
        rendered = dict(item)
        rendered["input_display"] = _pretty(item.get("input"))
        rendered["output_display"] = _pretty(item.get("output"))
        rendered["metadata_display"] = _pretty(item.get("metadata"))
        rendered["tokens"] = _usage_total(item)
        rendered["cost_display"] = f"${_cost_total(item):.6f}"
        decorated.append(rendered)
    return decorated


@hermes_session_required
def trace_detail(request, trace_id):
    try:
        _, items = _client_list(request, include_io=True, trace_id=trace_id)
    except LangfuseUnavailable:
        return render(request, "traces/unavailable.html", status=503)
    items = [item for item in items if str(item.get("traceId")) == trace_id]
    if not items:
        raise Http404
    return render(
        request,
        "traces/trace_detail.html",
        {"trace_id": trace_id, "observations": _decorate_observations(items)},
    )


@hermes_session_required
def session_detail(request, session_id):
    try:
        _, items = _client_list(request, include_io=True, session_id=session_id)
    except LangfuseUnavailable:
        return render(request, "traces/unavailable.html", status=503)
    items = [item for item in items if str(item.get("sessionId")) == session_id]
    if not items:
        raise Http404
    traces = defaultdict(list)
    for item in items:
        traces[str(item.get("traceId") or item.get("id"))].append(item)
    rows = []
    for trace_id, trace_items in traces.items():
        decorated = _decorate_observations(trace_items)
        root = next((item for item in decorated if item.get("isRootObservation")), decorated[0])
        rows.append(
            {
                "id": trace_id,
                "start_time": decorated[0].get("startTime"),
                "input_display": root["input_display"],
                "output_display": root["output_display"],
                "observation_count": len(decorated),
                "tokens": sum(item["tokens"] for item in decorated),
                "cost_display": f"${sum((_cost_total(item) for item in trace_items), Decimal(0)):.6f}",
            }
        )
    rows.sort(key=lambda item: _parsed_time(item["start_time"]), reverse=True)
    return render(
        request,
        "traces/session_detail.html",
        {"session_id": session_id, "traces": rows},
    )
