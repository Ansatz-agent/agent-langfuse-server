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
    sessions: dict[str, dict] = {}
    trace_ids: set[str] = set()
    active_days: set[str] = set()
    daily = defaultdict(lambda: {"tokens": 0, "cost": Decimal(0)})
    models = defaultdict(lambda: {"tokens": 0, "cost": Decimal(0), "observations": 0})
    total_tokens = 0
    total_cost = Decimal(0)

    for item in items:
        trace_id = str(item.get("traceId") or item.get("id") or "")
        if not trace_id:
            continue
        trace_ids.add(trace_id)
        session_id = str(item.get("sessionId") or f"trace-{trace_id}")
        started_at = _parsed_time(item.get("startTime"))
        day_key = started_at.date().isoformat()
        active_days.add(day_key)
        tokens = _usage_total(item)
        cost = _cost_total(item)
        total_tokens += tokens
        total_cost += cost
        daily[day_key]["tokens"] += tokens
        daily[day_key]["cost"] += cost

        model = item.get("providedModelName") or item.get("modelId")
        if model and (tokens or cost or item.get("type") == "GENERATION"):
            models[str(model)]["tokens"] += tokens
            models[str(model)]["cost"] += cost
            models[str(model)]["observations"] += 1

        session = sessions.setdefault(
            session_id,
            {
                "id": session_id,
                "trace_ids": set(),
                "trace_count": 0,
                "tokens": 0,
                "cost": Decimal(0),
                "first_seen": started_at,
                "last_seen": started_at,
                "latest_trace_id": trace_id,
                "trace_name": item.get("traceName") or item.get("name") or "对话",
            },
        )
        session["trace_ids"].add(trace_id)
        session["tokens"] += tokens
        session["cost"] += cost
        if started_at < session["first_seen"]:
            session["first_seen"] = started_at
        if started_at >= session["last_seen"]:
            session["last_seen"] = started_at
            session["latest_trace_id"] = trace_id
            session["trace_name"] = item.get("traceName") or item.get("name") or "对话"

    query_lower = query.casefold()
    session_rows = []
    for session in sessions.values():
        session["trace_count"] = len(session.pop("trace_ids"))
        session["cost_display"] = f"${session['cost']:.6f}"
        haystack = f"{session['id']} {session['latest_trace_id']} {session['trace_name']}".casefold()
        if query_lower and query_lower not in haystack:
            continue
        session_rows.append(session)
    session_rows.sort(key=lambda item: item["last_seen"], reverse=True)

    model_rows = [
        {
            "name": name,
            "tokens": values["tokens"],
            "cost": float(values["cost"]),
            "cost_display": f"${values['cost']:.6f}",
            "observations": values["observations"],
        }
        for name, values in models.items()
    ]
    model_rows.sort(key=lambda item: (item["tokens"], item["observations"]), reverse=True)
    max_tokens = max((row["tokens"] for row in model_rows), default=0)
    for row in model_rows:
        row["percent"] = max(3, round(row["tokens"] * 100 / max_tokens)) if max_tokens else 3

    daily_rows = [
        {
            "date": day,
            "tokens": values["tokens"],
            "cost": float(values["cost"]),
            "cost_display": f"${values['cost']:.6f}",
        }
        for day, values in sorted(daily.items())
    ]
    daily_max = max((row["tokens"] for row in daily_rows), default=0)
    for row in daily_rows:
        row["percent"] = max(4, round(row["tokens"] * 100 / daily_max)) if daily_max else 4

    return {
        "days": days,
        "query": query,
        "metrics": {
            "sessions": len(sessions),
            "traces": len(trace_ids),
            "tokens": total_tokens,
            "cost": float(total_cost),
            "cost_display": f"${total_cost:.6f}",
            "active_days": len(active_days),
        },
        "sessions": session_rows,
        "models": model_rows,
        "daily": daily_rows,
    }


def _range(request):
    try:
        days = int(request.GET.get("days", "30"))
    except (TypeError, ValueError):
        days = 30
    if days not in ALLOWED_DAY_RANGES:
        days = 30
    now = timezone.now()
    return days, (now - timedelta(days=days)).isoformat(), now.isoformat()


def _client_list(request, *, include_io=False, session_id=None, trace_id=None):
    days, from_time, to_time = _range(request)
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
    query = request.GET.get("q", "").strip()[:80]
    try:
        days, items = _client_list(request)
        context = aggregate_dashboard(items, days=days, query=query)
        context["unavailable"] = False
        return render(request, "traces/dashboard.html", context)
    except LangfuseUnavailable:
        days, _, _ = _range(request)
        return render(
            request,
            "traces/dashboard.html",
            {
                "days": days,
                "query": query,
                "unavailable": True,
                "metrics": {},
                "sessions": [],
                "models": [],
                "daily": [],
            },
            status=503,
        )


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
