import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import HistoryMessage, HistorySession, ImportBatch


class ImportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ImportResult:
    batch_id: int
    imported_sessions: int
    skipped_sessions: int
    imported_messages: int


_SESSION_METADATA_KEYS = {
    "parent_session_id",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "estimated_cost_usd",
    "actual_cost_usd",
    "billing_provider",
    "provider",
    "api_call_count",
}
_SESSION_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
_MESSAGE_METADATA_KEYS = {
    "display_kind",
    "display_metadata",
    "token_count",
    "finish_reason",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "effect_disposition",
}

_SECRET_PATTERNS = (
    re.compile(r"(?im)(authorization\s*[:=]\s*)([^\r\n,;]+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16})\b"),
)
_COOKIE_HEADER_PATTERN = re.compile(r"(?im)((?:set-cookie|cookie)\s*:\s*)([^\r\n]+)")
_PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_QUOTED_SECRET_PATTERN = re.compile(
    r"(?i)(?P<prefix>[\"']?[A-Za-z0-9_-]*(?:password|passwd|pwd|api[_-]?key|"
    r"api[_-]?token|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|private[_-]?key|signing[_-]?key|cookie|set[_-]?cookie|"
    r"session[_-]?(?:cookie|id)|authorization|secret)[A-Za-z0-9_-]*[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
)
_UNQUOTED_SECRET_PATTERN = re.compile(
    r"(?i)(?P<prefix>\b[A-Za-z0-9_-]*(?:password|passwd|pwd|api[_-]?key|"
    r"api[_-]?token|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|private[_-]?key|signing[_-]?key|cookie|set[_-]?cookie|"
    r"session[_-]?(?:cookie|id)|authorization|secret)[A-Za-z0-9_-]*\b\s*[:=]\s*)"
    r"(?P<value>[^\s,;}\]]+)"
)
_ENV_SECRET_PATTERN = re.compile(
    r"(?i)(?P<prefix>\b[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*)"
    r"(?P<value>[^\s,;]+)"
)
_CJK_SECRET_PATTERN = re.compile(
    r"(?i)(?P<prefix>(?:API\s*)?(?:密码|口令|密钥|令牌)(?:\s*[:：=]\s*|\s+))"
    r"(?P<value>[^\s,;]+)"
)


def _normalized_key(key: Any) -> str:
    raw_key = str(key)
    snake_key = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", raw_key)
    snake_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake_key)
    return re.sub(r"[^a-z0-9]+", "_", snake_key.lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    raw_key = str(key)
    if any(label in raw_key for label in ("密码", "口令", "密钥", "令牌")):
        return True
    normalized = _normalized_key(raw_key)
    if normalized in {
        "token",
        "authorization",
        "secret",
        "password",
        "passwd",
        "pwd",
        "private_key",
        "secret_key",
        "signing_key",
        "cookie",
        "cookies",
        "set_cookie",
        "session_cookie",
        "sessionid",
        "session_id",
    }:
        return True
    if normalized.startswith(("password", "passwd", "pwd")):
        return True
    return bool(
        re.search(
            r"(?:^|_)(?:api_key|api_token|access_token|refresh_token|auth_token|bearer_token|"
            r"client_secret|private_key|secret_key|signing_key|authorization|cookie|cookies|"
            r"set_cookie|session_cookie)(?:_|$)",
            normalized,
        )
    )


def _redact_quoted(match: re.Match) -> str:
    return f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}"


def _redact_unquoted(match: re.Match) -> str:
    return f"{match.group('prefix')}[REDACTED]"


def redact_text(value: str, *, _depth: int = 0) -> str:
    if _depth < 3 and value.lstrip()[:1] in {"{", "["}:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            cleaned = redact_value(parsed, _depth=_depth + 1)
            if cleaned != parsed:
                return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))

    redacted = _PRIVATE_KEY_BLOCK_PATTERN.sub("[REDACTED PRIVATE KEY]", value)
    redacted = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", redacted)
    redacted = _COOKIE_HEADER_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _QUOTED_SECRET_PATTERN.sub(_redact_quoted, redacted)
    redacted = _UNQUOTED_SECRET_PATTERN.sub(_redact_unquoted, redacted)
    redacted = _ENV_SECRET_PATTERN.sub(_redact_unquoted, redacted)
    redacted = _CJK_SECRET_PATTERN.sub(_redact_unquoted, redacted)
    redacted = _SECRET_PATTERNS[1].sub("[REDACTED]", redacted)
    return redacted


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    if isinstance(value, str):
        return redact_text(value, _depth=_depth)
    if isinstance(value, list):
        if len(value) == 2 and isinstance(value[0], str) and _is_sensitive_key(value[0]):
            return [redact_value(value[0], _depth=_depth + 1), "[REDACTED]"]
        return [redact_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, dict):
        label_fields = {"name", "key", "header", "header_name", "field"}
        sensitive_label = any(
            _normalized_key(key) in label_fields
            and isinstance(item, str)
            and _is_sensitive_key(item)
            for key, item in value.items()
        )
        result = {}
        for key, item in value.items():
            normalized = _normalized_key(key)
            contextual_value = sensitive_label and (
                normalized in {"value", "values", "data", "content", "credential", "credentials"}
                or normalized.endswith("_value")
            )
            result[str(key)] = (
                "[REDACTED]"
                if _is_sensitive_key(key) or contextual_value
                else redact_value(item, _depth=_depth + 1)
            )
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value), _depth=_depth)


def _setting(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def _read_upload(uploaded_file) -> bytes:
    max_bytes = _setting("HISTORY_IMPORT_MAX_BYTES", 25 * 1024 * 1024)
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > max_bytes:
        raise ImportValidationError(f"Upload exceeds the {max_bytes}-byte limit")
    chunks = []
    total = 0
    for chunk in uploaded_file.chunks():
        total += len(chunk)
        if total > max_bytes:
            raise ImportValidationError(f"Upload exceeds the {max_bytes}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _flatten_nested_rows(rows: list[Any]) -> list[Any]:
    flattened = []

    def visit(
        row: Any,
        parent_external_id: str | None = None,
        depth: int = 0,
    ) -> None:
        if not isinstance(row, dict):
            flattened.append(row)
            return
        current = dict(row)
        children = current.pop("subagent_threads", [])
        if not isinstance(children, list):
            raise ImportValidationError("subagent_threads must be a list")
        if children and depth >= 1:
            raise ImportValidationError("Only one subagent level is supported")
        if parent_external_id is not None:
            declared_parent = current.get("parent_session_id")
            if declared_parent not in (None, "") and str(declared_parent) != parent_external_id:
                raise ImportValidationError("Nested subagent thread has a conflicting parent")
            current["parent_session_id"] = parent_external_id
        flattened.append(current)
        current_id = current.get("id")
        if children and current_id in (None, ""):
            raise ImportValidationError("A session with subagent_threads must have an id")
        for child in children:
            visit(child, str(current_id), depth + 1)

    for row in rows:
        visit(row)
    return flattened


def _parse_payload(raw: bytes) -> list[dict]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportValidationError("Upload must be UTF-8") from exc
    if not text.strip():
        raise ImportValidationError("Upload is empty")

    rows: list[Any]
    if text.lstrip().startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImportValidationError("Invalid JSON document") from exc
        rows = parsed if isinstance(parsed, list) else [parsed]
    else:
        rows = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ImportValidationError(f"Invalid JSON on line {line_number}") from exc

    if not rows:
        raise ImportValidationError("Upload contains no sessions")
    rows = _flatten_nested_rows(rows)
    max_sessions = _setting("HISTORY_IMPORT_MAX_SESSIONS", 2000)
    if len(rows) > max_sessions:
        raise ImportValidationError(f"Upload contains more than {max_sessions} sessions")
    if any(not isinstance(row, dict) for row in rows):
        raise ImportValidationError("Every session must be a JSON object")
    return rows


def _bounded_text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    text = text.strip() if field != "content" else text
    if required and not text:
        raise ImportValidationError(f"{field} is required")
    if len(text) > maximum:
        raise ImportValidationError(f"{field} exceeds {maximum} characters")
    return redact_text(text)


def _nonnegative_integer(value: Any, field: str) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ImportValidationError(f"{field} must be a non-negative integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isdecimal():
        parsed = int(value)
    else:
        raise ImportValidationError(f"{field} must be a non-negative integer")
    if parsed < 0 or parsed > 9_223_372_036_854_775_807:
        raise ImportValidationError(f"{field} must be a non-negative integer")
    return parsed


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ImportValidationError("Message content must be JSON serializable") from exc
    max_chars = _setting("HISTORY_IMPORT_MAX_MESSAGE_CHARS", 2_000_000)
    if len(text) > max_chars:
        raise ImportValidationError(f"Message content exceeds {max_chars} characters")
    return redact_text(text)


def _datetime_value(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ImportValidationError(f"Invalid {field}") from exc
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            try:
                parsed = datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
            except (OverflowError, OSError, ValueError) as exc:
                raise ImportValidationError(f"Invalid {field}") from exc
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, dt_timezone.utc)
        return parsed
    raise ImportValidationError(f"Invalid {field}")


def _safe_metadata(row: dict, keys: set[str]) -> dict:
    return {key: redact_value(row[key]) for key in keys if key in row}


def _prepare_rows(rows: list[dict]) -> list[dict]:
    max_total_messages = _setting("HISTORY_IMPORT_MAX_MESSAGES", 100_000)
    max_per_session = _setting("HISTORY_IMPORT_MAX_MESSAGES_PER_SESSION", 20_000)
    prepared = []
    total_messages = 0
    seen_ids: set[str] = set()

    for index, row in enumerate(rows, start=1):
        external_id = _bounded_text(row.get("id"), f"sessions[{index}].id", 255, required=True)
        if external_id in seen_ids:
            raise ImportValidationError(f"Duplicate session id in upload: {external_id}")
        seen_ids.add(external_id)
        messages = row.get("messages") or []
        if not isinstance(messages, list):
            raise ImportValidationError(f"sessions[{index}].messages must be a list")
        if len(messages) > max_per_session:
            raise ImportValidationError(
                f"sessions[{index}] contains more than {max_per_session} messages"
            )
        total_messages += len(messages)
        if total_messages > max_total_messages:
            raise ImportValidationError(
                f"Upload contains more than {max_total_messages} total messages"
            )

        prepared_messages = []
        for message_index, message in enumerate(messages, start=1):
            if not isinstance(message, dict):
                raise ImportValidationError(
                    f"sessions[{index}].messages[{message_index}] must be an object"
                )
            role = _bounded_text(
                message.get("role"),
                f"sessions[{index}].messages[{message_index}].role",
                50,
                required=True,
            )
            source_id = _bounded_text(
                message.get("id", ""),
                f"sessions[{index}].messages[{message_index}].id",
                255,
            )
            prepared_messages.append(
                {
                    "source_message_id": source_id,
                    "role": role,
                    "content": _content_text(message.get("content")),
                    "timestamp": _datetime_value(
                        message.get("timestamp"),
                        f"sessions[{index}].messages[{message_index}].timestamp",
                    ),
                    "tool_name": _bounded_text(message.get("tool_name"), "tool_name", 255),
                    "tool_call_id": _bounded_text(message.get("tool_call_id"), "tool_call_id", 255),
                    "tool_calls": redact_value(message.get("tool_calls") or []),
                    "raw_metadata": _safe_metadata(message, _MESSAGE_METADATA_KEYS),
                }
            )

        prepared.append(
            {
                "external_id": external_id,
                "title": _bounded_text(row.get("title"), "title", 500),
                "source": _bounded_text(row.get("source"), "source", 100),
                "model": _bounded_text(row.get("model"), "model", 255),
                "started_at": _datetime_value(row.get("started_at"), "started_at"),
                "ended_at": _datetime_value(row.get("ended_at"), "ended_at"),
                "end_reason": _bounded_text(row.get("end_reason"), "end_reason", 100),
                "messages": prepared_messages,
                **{key: _nonnegative_integer(row.get(key), key) for key in _SESSION_USAGE_KEYS},
                "raw_metadata": _safe_metadata(row, _SESSION_METADATA_KEYS),
            }
        )
    return prepared


def import_history(uploaded_file, *, owner, uploader) -> ImportResult:
    suffix = Path(getattr(uploaded_file, "name", "upload.jsonl")).suffix.lower()
    if suffix not in {".jsonl", ".json"}:
        raise ImportValidationError("Only .jsonl and .json files are accepted")

    raw = _read_upload(uploaded_file)
    digest = hashlib.sha256(raw).hexdigest()
    batch = ImportBatch.objects.create(
        owner=owner,
        uploader=uploader,
        original_filename=Path(uploaded_file.name).name[:255],
        sha256=digest,
    )

    try:
        rows = _parse_payload(raw)
        prepared_rows = _prepare_rows(rows)
        imported_sessions = 0
        skipped_sessions = 0
        imported_messages = 0
        created_sessions = []
        with transaction.atomic():
            imported_rows = []
            for row in prepared_rows:
                session = HistorySession.objects.filter(
                    owner=owner, external_id=row["external_id"]
                ).first()
                if session is not None:
                    skipped_sessions += 1
                    imported_rows.append((session, row, False))
                    continue
                messages = row["messages"]
                defaults = {
                    "uploader": uploader,
                    "message_count": len(messages),
                    "tool_call_count": sum(
                        len(message["tool_calls"])
                        if isinstance(message["tool_calls"], list)
                        else bool(message["tool_calls"])
                        for message in messages
                    ),
                    **{key: value for key, value in row.items() if key != "messages"},
                }
                session, created = HistorySession.objects.get_or_create(
                    owner=owner,
                    external_id=defaults.pop("external_id"),
                    defaults=defaults,
                )
                if not created:
                    skipped_sessions += 1
                    imported_rows.append((session, row, False))
                    continue
                HistoryMessage.objects.bulk_create(
                    [HistoryMessage(session=session, **message) for message in messages]
                )
                imported_rows.append((session, row, True))
                created_sessions.append(session)
                imported_sessions += 1
                imported_messages += len(messages)

            owner_sessions = list(
                HistorySession.objects.filter(owner=owner).only(
                    "pk", "external_id", "parent_session_id"
                )
            )
            parent_map = {
                session.pk: session.parent_session_id
                for session in owner_sessions
                if session.parent_session_id is not None
            }
            imported_by_external_id = {
                session.external_id: session for session, _, _ in imported_rows
            }
            parent_updates = {}
            for session, row, _ in imported_rows:
                raw_parent = row["raw_metadata"].get("parent_session_id")
                if raw_parent in (None, ""):
                    continue
                if not isinstance(raw_parent, str):
                    raise ImportValidationError(
                        f"Invalid parent session ID for {session.external_id}"
                    )
                parent_external_id = raw_parent.strip()
                if not parent_external_id or len(parent_external_id) > 255:
                    raise ImportValidationError(
                        f"Invalid parent session ID for {session.external_id}"
                    )
                parent_session = imported_by_external_id.get(parent_external_id)
                if parent_session is None:
                    parent_session = HistorySession.objects.filter(
                        owner=owner, external_id=parent_external_id
                    ).first()
                if parent_session is None:
                    raise ImportValidationError(
                        f"Parent session does not exist: {parent_external_id}"
                    )
                if session.parent_session_id not in (None, parent_session.pk):
                    raise ImportValidationError(
                        f"Session has a conflicting parent: {session.external_id}"
                    )
                parent_map[session.pk] = parent_session.pk
                parent_updates[session.pk] = parent_session.pk

            for child_pk, parent_pk in parent_map.items():
                seen = {child_pk}
                ancestor_pk = parent_pk
                depth = 1
                while ancestor_pk in parent_map:
                    if ancestor_pk in seen:
                        raise ImportValidationError(
                            f"Parent cycle detected for session: {child_pk}"
                        )
                    seen.add(ancestor_pk)
                    ancestor_pk = parent_map[ancestor_pk]
                    depth += 1
                if ancestor_pk in seen:
                    raise ImportValidationError(f"Parent cycle detected for session: {child_pk}")
                if depth > 1:
                    raise ImportValidationError(f"Session exceeds one subagent level: {child_pk}")

            for child_pk, parent_pk in parent_updates.items():
                HistorySession.objects.filter(pk=child_pk).update(parent_session_id=parent_pk)

            if getattr(settings, "MEMORY_OUTBOX_ENABLED", False):
                # Keep Mem0/LLM calls out of the upload request.  The job rows
                # are committed together with the source messages and can be
                # safely retried by the dedicated worker.
                from .memory_service import enqueue_session_memory_jobs

                for session in created_sessions:
                    enqueue_session_memory_jobs(session)
    except ImportValidationError as exc:
        batch.status = ImportBatch.Status.FAILED
        batch.error_summary = str(exc)[:1000]
        batch.finished_at = timezone.now()
        batch.save(update_fields=["status", "error_summary", "finished_at"])
        raise
    except Exception:
        batch.status = ImportBatch.Status.FAILED
        batch.error_summary = "Unexpected import failure"
        batch.finished_at = timezone.now()
        batch.save(update_fields=["status", "error_summary", "finished_at"])
        raise

    batch.status = ImportBatch.Status.SUCCEEDED
    batch.imported_sessions = imported_sessions
    batch.skipped_sessions = skipped_sessions
    batch.imported_messages = imported_messages
    batch.finished_at = timezone.now()
    batch.save(
        update_fields=[
            "status",
            "imported_sessions",
            "skipped_sessions",
            "imported_messages",
            "finished_at",
        ]
    )
    return ImportResult(
        batch_id=batch.pk,
        imported_sessions=imported_sessions,
        skipped_sessions=skipped_sessions,
        imported_messages=imported_messages,
    )
