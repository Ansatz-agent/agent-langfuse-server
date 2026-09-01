from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils.dateparse import parse_datetime


UNKNOWN_MODEL = "Unknown model"
SERIES_COLORS = ("#76b900", "#5f99eb", "#f4a259", "#9c82e9", "#39c1c9")
ALLOWED_DAYS = {7, 30, 90}
ALLOWED_METRICS = {"cost", "tokens", "unit_cost"}
ALLOWED_GRANULARITIES = {"day", "week"}
ALLOWED_DASHBOARD_CHARTS = {"bar", "line"}
ALLOWED_MODEL_CHARTS = {"distribution", "bar", "line"}


@dataclass(frozen=True)
class AnalyticsQuery:
    days: int
    metric: str
    granularity: str
    chart: str
    query: str = ""
    model: str = "all"


def parse_analytics_query(querydict, *, page: str) -> AnalyticsQuery:
    try:
        days = int(querydict.get("days", "30"))
    except (TypeError, ValueError):
        days = 30
    if days not in ALLOWED_DAYS:
        days = 30
    metric = str(querydict.get("metric", "cost"))
    if metric not in ALLOWED_METRICS:
        metric = "cost"
    granularity = str(querydict.get("granularity", "week"))
    if granularity not in ALLOWED_GRANULARITIES:
        granularity = "week"
    allowed_charts = ALLOWED_MODEL_CHARTS if page == "models" else ALLOWED_DASHBOARD_CHARTS
    default_chart = "distribution" if page == "models" else "bar"
    chart = str(querydict.get("chart", default_chart))
    if chart not in allowed_charts:
        chart = default_chart
    return AnalyticsQuery(
        days=days,
        metric=metric,
        granularity=granularity,
        chart=chart,
        query=str(querydict.get("q", "")).strip()[:80],
        model=str(querydict.get("model", "all")).strip()[:160] or "all",
    )


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _dict_number(mapping: Any, *keys: str) -> tuple[Decimal, bool]:
    if not isinstance(mapping, dict):
        return Decimal(0), False
    for key in keys:
        if key not in mapping or mapping[key] is None:
            continue
        parsed = _decimal(mapping[key])
        if parsed is not None:
            return parsed, True
    return Decimal(0), False


def _legacy_usage_number(item: dict, key: str) -> tuple[Decimal, bool]:
    parsed = _decimal(item.get(key))
    if parsed is None or parsed == 0:
        return Decimal(0), False
    return parsed, True


def observation_usage(item: dict) -> dict[str, int | bool]:
    """Return usage values without treating Langfuse's zero fallbacks as evidence."""
    details = item.get("usageDetails")
    input_value, has_input = _dict_number(details, "input", "input_tokens")
    if not has_input:
        input_value, has_input = _legacy_usage_number(item, "inputUsage")
    cached, has_cached_input = _dict_number(
        details,
        "input_cached_tokens",
        "cache_read_input_tokens",
        "cached_input_tokens",
    )
    output, has_output = _dict_number(details, "output", "output_tokens")
    if not has_output:
        output, has_output = _legacy_usage_number(item, "outputUsage")
    reasoning, has_reasoning_output = _dict_number(
        details,
        "output_reasoning_tokens",
        "reasoning_tokens",
    )
    total, has_total = _dict_number(details, "total", "total_tokens")
    if not has_total:
        total, has_total = _legacy_usage_number(item, "totalUsage")
    if not has_total and (has_input or has_output):
        total = input_value + output
        has_total = True
    return {
        "input": int(input_value),
        "has_input": has_input,
        "cached_input": int(cached),
        "has_cached_input": has_cached_input,
        "output": int(output),
        "has_output": has_output,
        "reasoning_output": int(reasoning),
        "has_reasoning_output": has_reasoning_output,
        "total": int(total),
        "has_total": has_total,
    }


def observation_cost(item: dict) -> tuple[Decimal, bool]:
    """Return cost while preserving missing-vs-explicit-zero evidence."""
    details = item.get("costDetails")
    total, has_total = _dict_number(details, "total")
    if has_total:
        return total, True
    if isinstance(details, dict):
        values = [_decimal(value) for value in details.values() if value is not None]
        valid = [value for value in values if value is not None]
        if valid:
            return sum(valid, Decimal(0)), True
    if "totalCost" in item and item.get("totalCost") is not None:
        parsed = _decimal(item.get("totalCost"))
        if parsed is not None and parsed != 0:
            return parsed, True
    return Decimal(0), False


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_datetime(value)
    else:
        parsed = None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latency(item: dict) -> float | None:
    explicit = _decimal(item.get("latency"))
    if explicit is not None:
        return float(explicit)
    start = _time(item.get("startTime"))
    end = _time(item.get("endTime"))
    if not start or not end or end < start:
        return None
    return (end - start).total_seconds()


def _is_error(item: dict) -> bool:
    return str(item.get("level", "")).upper() == "ERROR"


def canonical_model(item_or_name: dict | str | None) -> str:
    if isinstance(item_or_name, dict):
        value = (
            item_or_name.get("providedModelName")
            or item_or_name.get("model")
            or item_or_name.get("modelId")
        )
    else:
        value = item_or_name
    name = str(value or "").strip()
    if not name:
        return UNKNOWN_MODEL
    parts = name.split("/")
    if len(parts) >= 3 and parts[0].casefold() == parts[1].casefold():
        parts.pop(0)
    return "/".join(parts)


def format_cost(value: float | int) -> str:
    number = float(value)
    absolute = abs(number)
    if absolute == 0:
        return "$0"
    if absolute < 0.000001:
        return "<$0.000001"
    if absolute < 0.01:
        decimal_places = max(2, -math.floor(math.log10(absolute)) + 1)
        rendered = f"{number:,.{decimal_places}f}".rstrip("0").rstrip(".")
        return f"${rendered}"
    return f"${number:,.2f}"


def _compact(value: float | int, *, money: bool = False) -> str:
    number = float(value)
    absolute = abs(number)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if absolute >= threshold:
            rendered = f"{number / threshold:.2f}".rstrip("0").rstrip(".") + suffix
            return f"${rendered}" if money else rendered
    if money:
        return format_cost(number)
    return f"{number:,.0f}"


def format_tokens(value: float | int) -> str:
    return _compact(value)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _ring_segments(values: list[float | int]) -> list[dict]:
    total = sum(max(0, float(value)) for value in values)
    if total <= 0:
        return []
    cursor = 0.0
    segments = []
    for index, value in enumerate(values):
        percent = max(0, float(value)) / total * 100
        if percent:
            segments.append(
                {
                    "color": SERIES_COLORS[index % len(SERIES_COLORS)],
                    "dash": f"{percent:.4f} {100 - percent:.4f}",
                    "offset": f"{-cursor:.4f}",
                }
            )
        cursor += percent
    return segments


def _generation(item: dict) -> bool:
    return str(item.get("type", "")).upper() == "GENERATION"


def _base_projection(items: list[dict], *, days: int, now: datetime) -> dict:
    generations = [item for item in items if _generation(item)]
    model_values = defaultdict(
        lambda: {
            "calls": 0,
            "input": 0,
            "cached_input": 0,
            "output": 0,
            "reasoning_output": 0,
            "tokens": 0,
            "usage_calls": 0,
            "input_calls": 0,
            "cached_input_calls": 0,
            "output_calls": 0,
            "reasoning_output_calls": 0,
            "cost": Decimal(0),
            "priced_calls": 0,
            "latencies": [],
            "errors": 0,
            "providers": set(),
        }
    )
    daily_values = defaultdict(
        lambda: {
            "tokens": 0,
            "usage_calls": 0,
            "cost": Decimal(0),
            "priced_calls": 0,
            "calls": 0,
            "errors": 0,
        }
    )
    total_cost = Decimal(0)
    has_cost = False
    total_tokens = 0
    token_keys = ("input", "cached_input", "output", "reasoning_output")
    token_mix = {
        "input": 0,
        "cached_input": 0,
        "output": 0,
        "reasoning_output": 0,
        "input_calls": 0,
        "cached_input_calls": 0,
        "output_calls": 0,
        "reasoning_output_calls": 0,
    }
    active_days: set[str] = set()
    errors = 0

    for item in generations:
        usage = observation_usage(item)
        cost, priced = observation_cost(item)
        model_name = canonical_model(item)
        model = model_values[model_name]
        model["calls"] += 1
        for key in token_keys:
            evidence_key = f"has_{key}"
            if usage[evidence_key]:
                model[key] += usage[key]
                model[f"{key}_calls"] += 1
                token_mix[key] += usage[key]
                token_mix[f"{key}_calls"] += 1
        if usage["has_total"]:
            model["tokens"] += usage["total"]
            model["usage_calls"] += 1
            total_tokens += usage["total"]
        if priced:
            model["cost"] += cost
            model["priced_calls"] += 1
            total_cost += cost
            has_cost = True
        latency = _latency(item)
        if latency is not None:
            model["latencies"].append(latency)
        if _is_error(item):
            model["errors"] += 1
            errors += 1
        model["providers"].add(str(item.get("name") or "Unknown provider"))
        started = _time(item.get("startTime"))
        if started:
            day = started.date().isoformat()
            active_days.add(day)
            daily = daily_values[day]
            if usage["has_total"]:
                daily["tokens"] += usage["total"]
                daily["usage_calls"] += 1
            daily["calls"] += 1
            daily["errors"] += int(_is_error(item))
            if priced:
                daily["cost"] += cost
                daily["priced_calls"] += 1

    models = []
    for name, values in model_values.items():
        tokens = values["tokens"]
        cost = float(values["cost"])
        has_model_cost = values["priced_calls"] > 0
        has_model_tokens = values["usage_calls"] > 0
        latencies = values.pop("latencies")
        providers = sorted(values.pop("providers"))
        models.append(
            {
                "name": name,
                **values,
                "cost": cost,
                "has_cost": has_model_cost,
                "has_tokens": has_model_tokens,
                "cost_display": _compact(cost, money=True) if has_model_cost else "—",
                "tokens_display": _compact(tokens) if has_model_tokens else "—",
                "input_display": (
                    _compact(values["input"]) if values["input_calls"] else "—"
                ),
                "cached_input_display": (
                    _compact(values["cached_input"])
                    if values["cached_input_calls"]
                    else "—"
                ),
                "output_display": (
                    _compact(values["output"]) if values["output_calls"] else "—"
                ),
                "reasoning_output_display": (
                    _compact(values["reasoning_output"])
                    if values["reasoning_output_calls"]
                    else "—"
                ),
                "unit_cost": (
                    cost / tokens * 1_000_000
                    if has_model_cost
                    and values["priced_calls"] == values["calls"]
                    and values["usage_calls"] == values["calls"]
                    and tokens
                    else None
                ),
                "cache_hit_rate": (
                    values["cached_input"] / values["input"] if values["input"] else None
                ),
                "avg_latency": sum(latencies) / len(latencies) if latencies else None,
                "p95_latency": _percentile(latencies, 0.95),
                "providers": providers,
            }
        )
    models.sort(
        key=lambda row: (row["cost"] if row["has_cost"] else -1, row["tokens"]),
        reverse=True,
    )
    for row in models:
        row["cost_share"] = row["cost"] / float(total_cost) if total_cost > 0 else None
        row["token_share"] = row["tokens"] / total_tokens if total_tokens else None

    first_day = now.date() - timedelta(days=days - 1)
    daily = []
    for offset in range(days):
        day = (first_day + timedelta(days=offset)).isoformat()
        value = daily_values[day]
        row_cost = float(value["cost"])
        daily.append(
            {
                "date": day,
                **value,
                "cost": row_cost,
                "has_cost": value["priced_calls"] > 0,
                "has_tokens": value["usage_calls"] > 0 or value["calls"] == 0,
                "tokens_display": (
                    _compact(value["tokens"])
                    if value["usage_calls"] > 0 or value["calls"] == 0
                    else "—"
                ),
                "cost_display": (
                    _compact(row_cost, money=True) if value["priced_calls"] > 0 else "—"
                ),
            }
        )
    maximum_tokens = max((row["tokens"] for row in daily), default=0)
    maximum_cost = max((row["cost"] for row in daily if row["has_cost"]), default=0)
    for row in daily:
        row["token_percent"] = row["tokens"] / maximum_tokens if maximum_tokens else 0
        row["cost_percent"] = row["cost"] / maximum_cost if maximum_cost else 0
        row["activity_level"] = (
            min(4, max(1, math.ceil(row["token_percent"] * 4))) if row["tokens"] else 0
        )

    token_mix["cache_hit_rate"] = (
        token_mix["cached_input"] / token_mix["input"]
        if token_mix["input_calls"]
        and token_mix["cached_input_calls"]
        and token_mix["input"]
        else None
    )
    token_mix["uncached_input"] = max(0, token_mix["input"] - token_mix["cached_input"])
    token_mix["uncached_input_calls"] = min(
        token_mix["input_calls"], token_mix["cached_input_calls"]
    )
    token_mix["regular_output"] = max(
        0, token_mix["output"] - token_mix["reasoning_output"]
    )
    token_mix["regular_output_calls"] = min(
        token_mix["output_calls"], token_mix["reasoning_output_calls"]
    )
    token_mix["segments"] = _ring_segments(
        [
            token_mix["uncached_input"],
            token_mix["cached_input"],
            token_mix["regular_output"],
            token_mix["reasoning_output"],
        ]
    )
    for key in (
        "input",
        "cached_input",
        "uncached_input",
        "output",
        "regular_output",
        "reasoning_output",
    ):
        evidence_key = {
            "input": "input_calls",
            "cached_input": "cached_input_calls",
            "uncached_input": "uncached_input_calls",
            "output": "output_calls",
            "regular_output": "regular_output_calls",
            "reasoning_output": "reasoning_output_calls",
        }[key]
        token_mix[f"{key}_display"] = (
            format_tokens(token_mix[key]) if token_mix[evidence_key] else "—"
        )
    return {
        "generations": generations,
        "models": models,
        "daily": daily,
        "daily_values": daily_values,
        "total_tokens": total_tokens,
        "has_tokens": not generations or any(row["usage_calls"] for row in models),
        "total_cost": float(total_cost),
        "has_cost": has_cost,
        "token_mix": token_mix,
        "active_days": active_days,
        "errors": errors,
        "generation_count": len(generations),
        "priced_generation_count": sum(row["priced_calls"] for row in models),
    }


def _trend(daily: list[dict], *, granularity: str, metric: str) -> list[dict]:
    buckets = defaultdict(
        lambda: {
            "tokens": 0,
            "usage_calls": 0,
            "cost": 0.0,
            "has_cost": False,
            "calls": 0,
            "priced_calls": 0,
        }
    )
    for row in daily:
        day = datetime.fromisoformat(row["date"]).date()
        key = (
            row["date"]
            if granularity == "day"
            else (day - timedelta(days=day.weekday())).isoformat()
        )
        bucket = buckets[key]
        bucket["tokens"] += row["tokens"]
        bucket["usage_calls"] += row["usage_calls"]
        bucket["cost"] += row["cost"]
        bucket["has_cost"] = bucket["has_cost"] or row["has_cost"]
        bucket["calls"] += row["calls"]
        bucket["priced_calls"] += row["priced_calls"]
    rows = []
    for key, value in sorted(buckets.items()):
        unit_cost = (
            value["cost"] / value["tokens"] * 1_000_000
            if value["has_cost"]
            and value["tokens"]
            and value["calls"] > 0
            and value["priced_calls"] == value["calls"]
            and value["usage_calls"] == value["calls"]
            else None
        )
        has_tokens = value["usage_calls"] > 0 or value["calls"] == 0
        chart_value = {
            "cost": value["cost"] if value["has_cost"] else 0,
            "tokens": value["tokens"] if has_tokens else 0,
            "unit_cost": unit_cost or 0,
        }[metric]
        rows.append(
            {
                "label": key,
                **value,
                "has_tokens": has_tokens,
                "tokens_display": format_tokens(value["tokens"]) if has_tokens else "—",
                "unit_cost": unit_cost,
                "value": chart_value,
            }
        )
    maximum = max((row["value"] for row in rows), default=0)
    for row in rows:
        row["percent"] = row["value"] / maximum if maximum else 0
        row["svg_height"] = max(1, row["percent"] * 88) if row["value"] else 0
        row["svg_y"] = 100 - row["svg_height"]
    return rows


def _sessions(items: list[dict], *, query: str) -> list[dict]:
    sessions: dict[str, dict] = {}
    for item in items:
        trace_id = str(item.get("traceId") or item.get("id") or "")
        if not trace_id:
            continue
        session_id = str(item.get("sessionId") or f"trace-{trace_id}")
        started = _time(item.get("startTime"))
        row = sessions.setdefault(
            session_id,
            {
                "id": session_id,
                "name": str(item.get("traceName") or item.get("name") or "Ansatz session"),
                "trace_ids": set(),
                "tokens": 0,
                "generation_calls": 0,
                "usage_calls": 0,
                "cost": Decimal(0),
                "has_cost": False,
                "errors": 0,
                "last_seen": started,
                "latest_trace_id": trace_id,
            },
        )
        row["trace_ids"].add(trace_id)
        if _generation(item):
            row["generation_calls"] += 1
            usage = observation_usage(item)
            if usage["has_total"]:
                row["tokens"] += usage["total"]
                row["usage_calls"] += 1
            cost, priced = observation_cost(item)
            if priced:
                row["cost"] += cost
                row["has_cost"] = True
        row["errors"] += int(_is_error(item))
        if started and (row["last_seen"] is None or started >= row["last_seen"]):
            row["last_seen"] = started
            row["latest_trace_id"] = trace_id
            row["name"] = str(item.get("traceName") or item.get("name") or row["name"])
    result = []
    needle = query.casefold()
    for row in sessions.values():
        trace_count = len(row.pop("trace_ids"))
        cost = float(row["cost"])
        row["cost"] = cost
        row["trace_count"] = trace_count
        row["has_tokens"] = row["generation_calls"] == 0 or row["usage_calls"] > 0
        row["tokens_display"] = (
            format_tokens(row["tokens"]) if row["has_tokens"] else "—"
        )
        row["cost_display"] = _compact(cost, money=True) if row["has_cost"] else "—"
        haystack = f"{row['id']} {row['name']} {row['latest_trace_id']}".casefold()
        if not needle or needle in haystack:
            result.append(row)
    result.sort(
        key=lambda row: row["last_seen"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return result


def build_dashboard(
    items: list[dict],
    *,
    days: int,
    now: datetime,
    query: str,
    metric: str,
    granularity: str,
    chart: str,
) -> dict:
    base = _base_projection(items, days=days, now=now)
    session_rows = _sessions(items, query=query)
    trace_ids = {str(item.get("traceId")) for item in items if item.get("traceId")}
    sessions_all = {
        str(item.get("sessionId") or f"trace-{item.get('traceId')}")
        for item in items
        if item.get("traceId")
    }
    daily_average = base["total_cost"] / days if base["has_cost"] else None
    unit_cost = (
        base["total_cost"] / base["total_tokens"] * 1_000_000
        if base["has_cost"]
        and base["priced_generation_count"] == base["generation_count"]
        and base["total_tokens"]
        else None
    )
    insights = []
    if base["models"]:
        top = base["models"][0]
        share = top["cost_share"] if base["total_cost"] > 0 else top["token_share"]
        if share is not None:
            insights.append(
                {
                    "title": "Highest concentration",
                    "text": f"{top['name']} accounts for {share:.1%} of the selected usage.",
                }
            )
    if base["errors"]:
        insights.append(
            {
                "title": "Errors detected",
                "text": f"{base['errors']} model call(s) reported an error in this range.",
            }
        )
    rank_values = []
    for row in base["models"]:
        value = {
            "cost": row["cost"] if row["has_cost"] else 0,
            "tokens": row["tokens"],
            "unit_cost": row["unit_cost"] or 0,
        }[metric]
        rank_values.append(value)
        row["rank_value"] = value
    rank_max = max(rank_values, default=0)
    for row in base["models"]:
        row["rank_percent"] = row["rank_value"] / rank_max if rank_max else 0
    return {
        "days": days,
        "date_start": now.date() - timedelta(days=days - 1),
        "date_end": now.date(),
        "query": query,
        "metric": metric,
        "granularity": granularity,
        "chart": chart,
        "metrics": {
            "sessions": len(sessions_all),
            "traces": len(trace_ids),
            "tokens": base["total_tokens"],
            "has_tokens": base["has_tokens"],
            "tokens_display": (
                format_tokens(base["total_tokens"]) if base["has_tokens"] else "—"
            ),
            "cost": base["total_cost"],
            "has_cost": base["has_cost"],
            "cost_display": (
                _compact(base["total_cost"], money=True) if base["has_cost"] else "—"
            ),
            "daily_average": daily_average,
            "daily_average_display": (
                _compact(daily_average, money=True) if daily_average is not None else "—"
            ),
            "unit_cost": unit_cost,
            "cost_coverage": (
                base["priced_generation_count"] / base["generation_count"]
                if base["generation_count"]
                else None
            ),
            "active_days": len(base["active_days"]),
            "errors": base["errors"],
        },
        "models": base["models"],
        "daily": base["daily"],
        "trend": _trend(base["daily"], granularity=granularity, metric=metric),
        "token_mix": base["token_mix"],
        "cost_mix_segments": _ring_segments(
            [row["cost"] if row["has_cost"] else 0 for row in base["models"]]
        ),
        "sessions": session_rows,
        "insights": insights,
    }


def build_model_analytics(
    items: list[dict],
    *,
    days: int,
    now: datetime,
    model: str,
    metric: str,
    granularity: str,
    chart: str,
) -> dict:
    full = _base_projection(items, days=days, now=now)
    known_models = [row["name"] for row in full["models"]]
    selected = canonical_model(model) if model != "all" else "all"
    if selected != "all" and selected not in known_models:
        selected = "all"
    scoped_items = [
        item
        for item in items
        if selected == "all" or (not _generation(item)) or canonical_model(item) == selected
    ]
    scoped = _base_projection(scoped_items, days=days, now=now)
    top = scoped["models"][0] if scoped["models"] else None
    top_share = None
    if top:
        top_share = top["cost_share"] if scoped["total_cost"] > 0 else top["token_share"]
    recent_calls = []
    for item in scoped["generations"]:
        usage = observation_usage(item)
        cost, priced = observation_cost(item)
        recent_calls.append(
            {
                "id": str(item.get("id") or ""),
                "trace_id": str(item.get("traceId") or ""),
                "session_id": str(item.get("sessionId") or ""),
                "started_at": _time(item.get("startTime")),
                "provider": str(item.get("name") or "Unknown provider"),
                "model": canonical_model(item),
                "tokens": usage["total"],
                "has_tokens": usage["has_total"],
                "tokens_display": (
                    format_tokens(usage["total"]) if usage["has_total"] else "—"
                ),
                "cost": float(cost),
                "has_cost": priced,
                "cost_display": _compact(float(cost), money=True) if priced else "—",
                "latency": _latency(item),
                "error": _is_error(item),
            }
        )
    recent_calls.sort(
        key=lambda row: row["started_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    insights = []
    if top and top_share is not None and len(scoped["models"]) > 1:
        insights.append(
            {
                "title": "Usage concentration",
                "text": f"{top['name']} represents {top_share:.1%} of selected usage.",
            }
        )
    if scoped["token_mix"]["cache_hit_rate"] is not None:
        insights.append(
            {
                "title": "Cache efficiency",
                "text": (
                    f"{scoped['token_mix']['cache_hit_rate']:.1%} of input tokens were served "
                    "from cache."
                ),
            }
        )
    if scoped["errors"]:
        insights.append(
            {
                "title": "Model errors",
                "text": f"{scoped['errors']} model call(s) reported an error.",
            }
        )
    distribution_values = []
    for row in full["models"]:
        value = {
            "cost": row["cost"] if row["has_cost"] else 0,
            "tokens": row["tokens"],
            "unit_cost": row["unit_cost"] or 0,
        }[metric]
        row["distribution_value"] = value
        distribution_values.append(value)
    distribution_max = max(distribution_values, default=0)
    for row in full["models"]:
        row["distribution_percent"] = (
            row["distribution_value"] / distribution_max if distribution_max else 0
        )

    scatter_candidates = [
        row for row in scoped["models"] if row["unit_cost"] is not None and row["tokens"] > 0
    ]
    max_scatter_tokens = max((row["tokens"] for row in scatter_candidates), default=0)
    max_scatter_unit = max((row["unit_cost"] for row in scatter_candidates), default=0)
    max_scatter_cost = max((row["cost"] for row in scatter_candidates), default=0)
    scatter = []
    for row in scatter_candidates:
        scatter.append(
            {
                **row,
                "x_percent": 8 + (row["tokens"] / max_scatter_tokens * 84),
                "y_percent": 88 - (row["unit_cost"] / max_scatter_unit * 76)
                if max_scatter_unit
                else 50,
                "radius": 2 + math.sqrt(row["cost"] / max_scatter_cost) * 4
                if max_scatter_cost
                else 3,
            }
        )
    return {
        "days": days,
        "date_start": now.date() - timedelta(days=days - 1),
        "date_end": now.date(),
        "metric": metric,
        "granularity": granularity,
        "chart": chart,
        "selected_model": selected,
        "known_models": known_models,
        "summary": {
            "cost": scoped["total_cost"],
            "has_cost": scoped["has_cost"],
            "cost_display": (
                _compact(scoped["total_cost"], money=True) if scoped["has_cost"] else "—"
            ),
            "tokens": scoped["total_tokens"],
            "has_tokens": scoped["has_tokens"],
            "tokens_display": (
                format_tokens(scoped["total_tokens"]) if scoped["has_tokens"] else "—"
            ),
            "cache_hit_rate": scoped["token_mix"]["cache_hit_rate"],
            "top_model_share": top_share,
            "top_model": top["name"] if top else None,
        },
        "models": scoped["models"],
        "distribution_models": full["models"],
        "token_mix": scoped["token_mix"],
        "daily": scoped["daily"],
        "trend": _trend(scoped["daily"], granularity=granularity, metric=metric),
        "recent_calls": recent_calls[:100],
        "scatter": scatter,
        "insights": insights,
    }
