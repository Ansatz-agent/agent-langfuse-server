from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .auth_views import hermes_session_required
from .langfuse_client import LangfuseUnavailable, get_langfuse_client
from .trace_analytics import (
    build_dashboard,
    build_model_analytics,
    format_cost,
    format_tokens,
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


def _time_or_none(value):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _duration_seconds(item: dict) -> float | None:
    start = _time_or_none(item.get("startTime"))
    end = _time_or_none(item.get("endTime"))
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds()


def _trace_duration_seconds(items: list[dict]) -> float | None:
    starts = [
        parsed
        for item in items
        if (parsed := _time_or_none(item.get("startTime"))) is not None
    ]
    ends = [
        parsed
        for item in items
        if (parsed := _time_or_none(item.get("endTime"))) is not None
    ]
    if not starts or not ends:
        return None
    start = min(starts)
    end = max(ends)
    return (end - start).total_seconds() if end >= start else None


def _duration_display(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes)}m {remainder:.1f}s"


def _pretty(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _owned(items: list[dict], langfuse_user_id: str) -> list[dict]:
    return [
        item for item in items if str(item.get("userId")) == langfuse_user_id
    ]


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
    langfuse_user_id = request.user.get_username()
    items = get_langfuse_client().list_observations(
        user_id=langfuse_user_id,
        days=days,
        from_time=from_time,
        to_time=to_time,
        session_id=session_id,
        trace_id=trace_id,
        include_io=include_io,
    )
    return days, _owned(items, langfuse_user_id)


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


def _observation_kind(item: dict) -> str:
    observation_type = str(item.get("type") or "").upper()
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    scope_type = str(
        metadata.get("nemo_relay.scope_type")
        or metadata.get("scope_type")
        or ""
    ).lower()
    name = str(item.get("name") or "").lower()
    if observation_type == "GENERATION" or scope_type == "llm":
        return "llm"
    if observation_type == "TOOL" or scope_type == "tool":
        return "tool"
    if item.get("isRootObservation") or name == "hermes.turn":
        return "agent"
    if "logical_llm_call" in name:
        return "internal"
    return "span"


def _decorate_observations(items: list[dict]) -> list[dict]:
    ordered = sorted(items, key=lambda value: _parsed_time(value.get("startTime")))
    starts = [_time_or_none(item.get("startTime")) for item in ordered]
    ends = [_time_or_none(item.get("endTime")) for item in ordered]
    valid_starts = [value for value in starts if value is not None]
    valid_ends = [value for value in ends if value is not None]
    trace_start = min(valid_starts) if valid_starts else None
    trace_end = max(valid_ends) if valid_ends else trace_start
    trace_duration = _trace_duration_seconds(ordered) or 0
    by_id = {str(item.get("id")): item for item in ordered if item.get("id")}

    def depth(item: dict) -> int:
        current = item
        seen = set()
        result = 0
        while current.get("parentObservationId") and result < 4:
            parent_id = str(current.get("parentObservationId"))
            if parent_id in seen or parent_id not in by_id:
                break
            seen.add(parent_id)
            result += 1
            current = by_id[parent_id]
        return result

    decorated = []
    for index, item in enumerate(ordered):
        rendered = dict(item)
        rendered["input_display"] = _pretty(item.get("input"))
        rendered["output_display"] = _pretty(item.get("output"))
        rendered["metadata_display"] = _pretty(item.get("metadata"))
        rendered["tokens"] = _usage_total(item)
        rendered["cost_display"] = format_cost(_cost_total(item))
        rendered["kind"] = _observation_kind(item)
        rendered["kind_label"] = {
            "agent": "Agent",
            "llm": "LLM",
            "tool": "Tool",
            "internal": "Internal",
            "span": "Span",
        }[rendered["kind"]]
        rendered["depth"] = depth(item)
        rendered["sequence"] = index + 1
        rendered["duration_seconds"] = _duration_seconds(item)
        rendered["duration_display"] = _duration_display(rendered["duration_seconds"])
        item_start = _time_or_none(item.get("startTime"))
        if trace_start is not None and item_start is not None and trace_duration > 0:
            offset = max(0.0, (item_start - trace_start).total_seconds()) / trace_duration * 100
            width = max(0.8, (rendered["duration_seconds"] or 0) / trace_duration * 100)
            rendered["offset_percent"] = round(min(offset, 100.0), 3)
            rendered["width_percent"] = round(min(width, 100.0 - offset), 3)
        else:
            rendered["offset_percent"] = 0.0
            rendered["width_percent"] = 100.0 if item.get("isRootObservation") else 0.8
        rendered["is_error"] = (
            str(item.get("level") or "").upper() == "ERROR"
            or bool(item.get("statusMessage"))
        )
        rendered["model_display"] = (
            item.get("providedModelName") or item.get("model") or item.get("modelId") or ""
        )
        decorated.append(rendered)
    return decorated


def _content_blocks(observations: list[dict], root: dict) -> list[dict]:
    kind_labels = {
        "user_request": "User request",
        "model_exchange": "Model response",
        "tool_exchange": "Tool call",
        "final_response": "Final response",
        "event": "Event",
    }
    blocks = []
    if root.get("input_display"):
        blocks.append(
            {
                "kind": "user_request",
                "title": "User request",
                "input_display": "",
                "output_display": root["input_display"],
                "observation": root,
            }
        )

    last_model_output = ""
    for observation in observations:
        if observation is root:
            continue
        kind = observation["kind"]
        if kind == "llm":
            blocks.append(
                {
                    "kind": "model_exchange",
                    "title": "Model response",
                    "input_display": observation["input_display"],
                    "output_display": observation["output_display"],
                    "observation": observation,
                }
            )
            if observation["output_display"]:
                last_model_output = observation["output_display"]
        elif kind == "tool":
            blocks.append(
                {
                    "kind": "tool_exchange",
                    "title": f"Tool · {observation.get('name') or 'Unknown tool'}",
                    "input_display": observation["input_display"],
                    "output_display": observation["output_display"],
                    "observation": observation,
                }
            )
        elif observation["input_display"] or observation["output_display"]:
            blocks.append(
                {
                    "kind": "event",
                    "title": observation.get("name") or "Captured event",
                    "input_display": observation["input_display"],
                    "output_display": observation["output_display"],
                    "observation": observation,
                }
            )

    final_output = root.get("output_display") or ""
    if final_output and final_output != last_model_output:
        blocks.append(
            {
                "kind": "final_response",
                "title": "Final response",
                "input_display": "",
                "output_display": final_output,
                "observation": root,
            }
        )
    for sequence, block in enumerate(blocks, start=1):
        block["sequence"] = sequence
        block["anchor"] = f"content-step-{sequence}"
        block["kind_label"] = kind_labels[block["kind"]]
    return blocks


def _trace_row(trace_id: str, trace_items: list[dict]) -> dict:
    observations = _decorate_observations(trace_items)
    root = next(
        (item for item in observations if item.get("isRootObservation")),
        observations[0],
    )
    models = list(
        dict.fromkeys(item["model_display"] for item in observations if item["model_display"])
    )
    tools = list(
        dict.fromkeys(
            str(item.get("name") or "Unknown tool")
            for item in observations
            if item["kind"] == "tool"
        )
    )
    tokens = sum(item["tokens"] for item in observations)
    cost = sum((_cost_total(item) for item in trace_items), Decimal(0))
    return {
        "id": trace_id,
        "name": str(root.get("traceName") or root.get("name") or "Agent trace"),
        "session_id": str(root.get("sessionId") or ""),
        "started_at": root.get("startTime"),
        "input_preview": root["input_display"],
        "output_preview": root["output_display"],
        "status": "Error" if any(item["is_error"] for item in observations) else "Complete",
        "models": models,
        "tools": tools,
        "tool_count": sum(item["kind"] == "tool" for item in observations),
        "observation_count": len(observations),
        "tokens": tokens,
        "tokens_display": format_tokens(tokens),
        "cost": cost,
        "cost_display": format_cost(cost),
        "duration_display": _duration_display(_trace_duration_seconds(trace_items)),
        "errors": sum(item["is_error"] for item in observations),
    }


def _session_index_context(items: list[dict], *, days: int, query: str) -> dict:
    grouped = defaultdict(list)
    for item in items:
        session_key = str(item.get("sessionId") or "__unsessioned__")
        grouped[session_key].append(item)
    sessions = []
    needle = query.casefold()
    for session_key, session_items in grouped.items():
        trace_groups = defaultdict(list)
        for item in session_items:
            trace_id = str(item.get("traceId") or item.get("id") or "")
            if trace_id:
                trace_groups[trace_id].append(item)
        traces = [_trace_row(trace_id, trace_items) for trace_id, trace_items in trace_groups.items()]
        traces.sort(key=lambda row: _parsed_time(row["started_at"]))
        if not traces:
            continue
        all_observations = _decorate_observations(session_items)
        models = list(
            dict.fromkeys(item["model_display"] for item in all_observations if item["model_display"])
        )
        tools = list(
            dict.fromkeys(
                str(item.get("name") or "Unknown tool")
                for item in all_observations
                if item["kind"] == "tool"
            )
        )
        cost = sum((trace["cost"] for trace in traces), Decimal(0))
        latest = traces[-1]
        row = {
            "id": session_key,
            "name": "Unsessioned traces" if session_key == "__unsessioned__" else latest["name"],
            "first_activity": traces[0]["started_at"],
            "last_activity": latest["started_at"],
            "first_request_preview": next(
                (trace["input_preview"] for trace in traces if trace["input_preview"]), ""
            ),
            "latest_response_preview": next(
                (trace["output_preview"] for trace in reversed(traces) if trace["output_preview"]), ""
            ),
            "trace_count": len(traces),
            "step_count": len(all_observations),
            "tokens": sum(trace["tokens"] for trace in traces),
            "tokens_display": format_tokens(sum(trace["tokens"] for trace in traces)),
            "cost_display": format_cost(cost),
            "errors": sum(trace["errors"] for trace in traces),
            "models": models,
            "tools": tools,
            "trace_ids": [trace["id"] for trace in traces],
        }
        haystack = " ".join(
            [
                row["id"],
                row["name"],
                row["first_request_preview"],
                row["latest_response_preview"],
                *row["trace_ids"],
                *models,
                *tools,
            ]
        ).casefold()
        if not needle or needle in haystack:
            sessions.append(row)
    sessions.sort(key=lambda row: _parsed_time(row["last_activity"]), reverse=True)
    now = timezone.now()
    return {
        "days": days,
        "date_start": now - timedelta(days=days),
        "date_end": now,
        "query": query,
        "sessions": sessions,
    }


@hermes_session_required
def trace_index(request):
    days, _, _ = _range(request)
    query = str(request.GET.get("q") or "").strip()[:80]
    try:
        _, items = _client_list(request, include_io=True, days_override=days)
    except LangfuseUnavailable:
        return render(request, "traces/unavailable.html", status=503)
    return render(
        request,
        "traces/trace_index.html",
        _session_index_context(items, days=days, query=query),
    )


@hermes_session_required
def trace_runs_legacy(request):
    days, _, _ = _range(request)
    query = str(request.GET.get("q") or "").strip()[:80]
    params = {"days": days}
    if query:
        params["q"] = query
    return redirect(f"{reverse('trace-index')}?{urlencode(params)}")


def _inspector_steps(observations: list[dict], root: dict, blocks: list[dict]) -> list[dict]:
    steps = [
        {
            "id": "overview",
            "sequence": 0,
            "kind": "overview",
            "kind_label": "Overview",
            "title": "Run overview",
            "input_display": root.get("input_display") or "",
            "output_display": root.get("output_display") or "",
            "metadata_display": root.get("metadata_display") or "",
            "observation": root,
        }
    ]
    for block in blocks:
        if block["kind"] in {"user_request", "final_response"}:
            continue
        observation = block["observation"]
        observation_id = str(observation.get("id") or f"step-{len(steps)}")
        steps.append(
            {
                **block,
                "id": observation_id,
                "sequence": len(steps),
                "metadata_display": observation.get("metadata_display") or "",
            }
        )
    return steps


def _trace_detail_context(
    trace_id: str,
    items: list[dict],
    *,
    days: int,
    selected_step_id: str = "overview",
) -> dict:
    observations = _decorate_observations(items)
    duration = _trace_duration_seconds(observations)
    root = next(
        (item for item in observations if item.get("isRootObservation")),
        observations[0],
    )
    summary = {
        "status": "Error" if any(item["is_error"] for item in observations) else "Complete",
        "duration_seconds": duration,
        "duration_display": _duration_display(duration),
        "llm_calls": sum(item["kind"] == "llm" for item in observations),
        "tool_calls": sum(item["kind"] == "tool" for item in observations),
        "tokens": sum(item["tokens"] for item in observations),
        "cost": sum((_cost_total(item) for item in items), Decimal(0)),
        "errors": sum(item["is_error"] for item in observations),
        "started_at": root.get("startTime"),
    }
    summary["cost_display"] = format_cost(summary["cost"])
    summary["tokens_display"] = format_tokens(summary["tokens"])
    content_blocks = _content_blocks(observations, root)
    steps = _inspector_steps(observations, root, content_blocks)
    selected_step = next(
        (step for step in steps if step["id"] == selected_step_id),
        steps[0],
    )
    return {
        "trace_id": trace_id,
        "days": days,
        "session_id": str(root.get("sessionId") or "__unsessioned__"),
        "observations": observations,
        "conversation_observations": [
            item for item in observations if item["kind"] in {"llm", "tool"}
        ],
        "root_observation": root,
        "content_blocks": content_blocks,
        "steps": steps,
        "selected_step": selected_step,
        "summary": summary,
    }


@hermes_session_required
def trace_detail(request, trace_id):
    try:
        days, items = _client_list(request, include_io=True, trace_id=trace_id)
    except LangfuseUnavailable:
        return render(request, "traces/unavailable.html", status=503)
    items = [item for item in items if str(item.get("traceId")) == trace_id]
    if not items:
        raise Http404
    return render(
        request,
        "traces/trace_detail.html",
        _trace_detail_context(
            trace_id,
            items,
            days=days,
            selected_step_id=str(request.GET.get("step") or "overview")[:200],
        ),
    )


@hermes_session_required
def trace_step_fragment(request, trace_id, observation_id):
    try:
        days, items = _client_list(request, include_io=True, trace_id=trace_id)
    except LangfuseUnavailable:
        return render(request, "traces/unavailable.html", status=503)
    items = [item for item in items if str(item.get("traceId")) == trace_id]
    if not items:
        raise Http404
    context = _trace_detail_context(
        trace_id,
        items,
        days=days,
        selected_step_id=str(observation_id)[:200],
    )
    return render(request, "traces/_trace_step_panel.html", context)


@hermes_session_required
def session_detail(request, session_id):
    unsessioned = session_id == "__unsessioned__"
    try:
        days, items = _client_list(
            request,
            include_io=True,
            session_id=None if unsessioned else session_id,
        )
    except LangfuseUnavailable:
        return render(request, "traces/unavailable.html", status=503)
    if unsessioned:
        items = [item for item in items if not item.get("sessionId")]
    else:
        items = [item for item in items if str(item.get("sessionId")) == session_id]
    if not items:
        raise Http404
    traces = defaultdict(list)
    for item in items:
        traces[str(item.get("traceId") or item.get("id"))].append(item)
    rows = []
    for trace_id, trace_items in traces.items():
        rows.append(_trace_row(trace_id, trace_items))
    rows.sort(key=lambda item: _parsed_time(item["started_at"]))
    return render(
        request,
        "traces/session_detail.html",
        {
            "days": days,
            "session_id": session_id,
            "session_name": "Unsessioned traces" if unsessioned else session_id,
            "traces": rows,
        },
    )
