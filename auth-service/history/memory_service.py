from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from django.conf import settings

from .client_sessions import account_identity_for_user
from .importer import redact_text
from .models import AccountIdentity, HistoryMessage, HistorySession, MemoryIngestJob
from .presentation import is_hermes_context_artifact, is_hermes_control_event

logger = logging.getLogger(__name__)

REDACTION_VERSION = "v1"
# Keep individual extraction requests small enough for hosted reasoning models.
# A historical session can still produce many jobs; the outbox/worker provides
# retry and back-pressure across those smaller chunks.
MAX_CHUNK_MESSAGES = 10
MAX_CHUNK_CHARS = 4_000


class MemoryUnavailable(RuntimeError):
    """Mem0 is disabled, not installed, or its provider is unavailable."""


class MemoryIdentityError(RuntimeError):
    """The account cannot be mapped to a stable Mem0 user id."""


class MemoryNotFound(RuntimeError):
    """The requested memory is not owned by the current account."""


@dataclass(frozen=True)
class MemoryChunk:
    index: int
    message_ids: tuple[int, ...]
    messages: tuple[dict[str, str], ...]
    content_sha256: str


def memory_enabled() -> bool:
    return bool(getattr(settings, "MEMORY_ENABLED", False))


def account_memory_id(user) -> str:
    identity = account_identity_for_user(user)
    if getattr(identity, "state", "active") != "active":
        raise MemoryIdentityError("account_identity_revoked")
    return str(identity.account_id)


def _eligible_messages(session: HistorySession):
    queryset = session.messages.order_by("timestamp", "id")
    for message in queryset.iterator(chunk_size=500):
        role = (message.role or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(message.content, str) or not message.content.strip():
            continue
        if is_hermes_control_event(message) or is_hermes_context_artifact(message):
            continue
        content = redact_text(message.content)
        if content.strip():
            yield message, content


def _message_parts(message: HistoryMessage, content: str):
    if len(content) <= MAX_CHUNK_CHARS:
        yield content
        return
    part_size = MAX_CHUNK_CHARS - 64
    parts = [content[offset : offset + part_size] for offset in range(0, len(content), part_size)]
    for index, part in enumerate(parts, start=1):
        yield f"[message part {index}/{len(parts)}]\n{part}"


def _chunk_digest(message_ids: tuple[int, ...], messages: tuple[dict[str, str], ...]) -> str:
    payload = json.dumps(
        {"message_ids": message_ids, "messages": messages, "redaction_version": REDACTION_VERSION},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def memory_chunks(session: HistorySession) -> list[MemoryChunk]:
    chunks: list[MemoryChunk] = []
    current_ids: list[int] = []
    current_messages: list[dict[str, str]] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current_ids, current_messages, current_chars
        if not current_messages:
            return
        ids = tuple(current_ids)
        messages = tuple(current_messages)
        chunks.append(
            MemoryChunk(
                index=len(chunks),
                message_ids=ids,
                messages=messages,
                content_sha256=_chunk_digest(ids, messages),
            )
        )
        current_ids = []
        current_messages = []
        current_chars = 0

    for message, content in _eligible_messages(session):
        for part in _message_parts(message, content):
            if current_messages and (
                len(current_messages) >= MAX_CHUNK_MESSAGES
                or current_chars + len(part) > MAX_CHUNK_CHARS
            ):
                flush()
            current_ids.append(message.pk)
            current_messages.append({"role": message.role.strip().lower(), "content": part})
            current_chars += len(part)
    flush()
    return chunks


def enqueue_session_memory_jobs(session: HistorySession) -> int:
    chunks = memory_chunks(session)
    total = len(chunks)
    created = 0
    for chunk in chunks:
        source_key = hashlib.sha256(
            f"{session.owner_id}:{session.pk}:{chunk.index}:{chunk.content_sha256}".encode(
                "ascii"
            )
        ).hexdigest()
        defaults = {
            "owner_id": session.owner_id,
            "session_id": session.pk,
            "message_ids": list(chunk.message_ids),
            "chunk_index": chunk.index,
            "chunk_count": total,
            "content_sha256": chunk.content_sha256,
            "redaction_version": REDACTION_VERSION,
        }
        job = MemoryIngestJob.objects.filter(source_key=source_key).first()
        if job is None:
            MemoryIngestJob.objects.create(source_key=source_key, **defaults)
            created += 1
        elif job.status == MemoryIngestJob.Status.DELETED and not job.mem0_memory_ids:
            # A deleted, never-processed job may be intentionally re-enqueued
            # after rechunking or an operator retry.  Keep the audit row but
            # reset its delivery state instead of silently skipping it.
            for field, value in defaults.items():
                setattr(job, field, value)
            job.status = MemoryIngestJob.Status.PENDING
            job.attempts = 0
            job.next_attempt_at = None
            job.last_error = ""
            job.processed_at = None
            job.save(
                update_fields=[
                    *defaults.keys(),
                    "status",
                    "attempts",
                    "next_attempt_at",
                    "last_error",
                    "processed_at",
                ]
            )
            created += 1
    return created


def _provider_config(
    provider: str,
    model: str,
    *,
    base_url_env: str = "MEMORY_OPENAI_BASE_URL",
    api_key_env: str = "MEMORY_PROVIDER_API_KEY",
    reasoning_effort: str | None = None,
    is_reasoning_model: bool | None = None,
) -> dict:
    config = {"model": model}
    if provider == "ollama":
        config["ollama_base_url"] = getattr(settings, "MEMORY_OLLAMA_BASE_URL", "http://ollama:11434")
    else:
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key and api_key_env != "MEMORY_PROVIDER_API_KEY":
            api_key = os.getenv("MEMORY_PROVIDER_API_KEY", "").strip()
        if api_key:
            config["api_key"] = api_key
        if provider == "openai":
            base_url = os.getenv(base_url_env, "").strip()
            if not base_url and base_url_env != "MEMORY_OPENAI_BASE_URL":
                base_url = os.getenv("MEMORY_OPENAI_BASE_URL", "").strip()
            config["openai_base_url"] = base_url or getattr(
                settings, "MEMORY_OPENAI_BASE_URL", "https://api.openai.com/v1"
            )
            if reasoning_effort:
                config["reasoning_effort"] = reasoning_effort
            if is_reasoning_model is not None:
                config["is_reasoning_model"] = is_reasoning_model
    return {"provider": provider, "config": config}


@lru_cache(maxsize=1)
def get_memory():
    if not memory_enabled():
        raise MemoryUnavailable("memory_disabled")
    # Mem0 creates a local config directory on import.  Keep that state on the
    # mounted data volume and disable anonymous telemetry unless explicitly
    # opted in by the operator.
    os.environ.setdefault("MEM0_DIR", getattr(settings, "MEMORY_MEM0_DIR", "/data/mem0"))
    os.environ.setdefault(
        "MEM0_TELEMETRY",
        "true" if getattr(settings, "MEMORY_TELEMETRY", False) else "false",
    )
    try:
        from mem0 import Memory
    except ImportError as exc:
        raise MemoryUnavailable("mem0ai_not_installed") from exc

    database_url = os.getenv("MEMORY_DATABASE_URL", "").strip()
    collection_name = os.getenv("MEMORY_COLLECTION", "ansatz_memory_v1").strip()
    if not database_url:
        raise MemoryUnavailable("memory_database_not_configured")
    # nomic-embed-text (the bundled Ollama default) emits 768 dimensions.
    # Operators switching to another embedder must set this explicitly before
    # the collection is created because pgvector dimensions are immutable.
    embedding_dims = int(os.getenv("MEMORY_EMBEDDING_DIMS", "768"))
    llm_config = _provider_config(
        os.getenv("MEMORY_LLM_PROVIDER", "ollama").strip(),
        os.getenv("MEMORY_LLM_MODEL", "llama3.1:8b").strip(),
        base_url_env="MEMORY_LLM_OPENAI_BASE_URL",
        api_key_env="MEMORY_LLM_API_KEY",
        reasoning_effort=os.getenv("MEMORY_REASONING_EFFORT", "high").strip() or "high",
        is_reasoning_model=True,
    )
    # Keep extraction responses bounded for large historical chunks.  The
    # JSON extraction normally needs far fewer tokens than the SDK default;
    # operators can raise this through the environment when required.
    llm_config["config"]["max_tokens"] = max(
        256, min(int(os.getenv("MEMORY_LLM_MAX_TOKENS", "1000")), 4000)
    )
    config = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "connection_string": database_url,
                "collection_name": collection_name,
                "embedding_model_dims": embedding_dims,
                "diskann": False,
                "hnsw": True,
            },
        },
        "llm": llm_config,
        "embedder": _provider_config(
            os.getenv("MEMORY_EMBEDDER_PROVIDER", "ollama").strip(),
            os.getenv("MEMORY_EMBEDDER_MODEL", "nomic-embed-text").strip(),
            base_url_env="MEMORY_EMBEDDER_OPENAI_BASE_URL",
            api_key_env="MEMORY_EMBEDDER_API_KEY",
        ),
    }
    if getattr(settings, "MEMORY_RERANK_ENABLED", False):
        judge_model = os.getenv("MEMORY_JUDGE_MODEL", "").strip()
        if not judge_model:
            raise MemoryUnavailable("memory_judge_model_not_configured")
        judge_provider = os.getenv("MEMORY_JUDGE_PROVIDER", "openai").strip()
        judge_llm = _provider_config(
            judge_provider,
            judge_model,
            base_url_env="MEMORY_JUDGE_OPENAI_BASE_URL",
            api_key_env="MEMORY_JUDGE_API_KEY",
            reasoning_effort=os.getenv("MEMORY_REASONING_EFFORT", "high").strip() or "high",
            is_reasoning_model=True,
        )
        config["reranker"] = {
            "provider": "llm_reranker",
            "config": {
                "model": judge_model,
                "provider": judge_provider,
                "api_key": judge_llm["config"].get("api_key"),
                "top_k": max(1, min(int(getattr(settings, "MEMORY_RERANK_TOP_K", 5)), 20)),
                "temperature": 0.0,
                "max_tokens": 50,
                "llm": {"provider": judge_provider, "config": judge_llm["config"]},
            },
        }
    try:
        return Memory.from_config(config)
    except Exception as exc:  # provider-specific exceptions vary by SDK version
        raise MemoryUnavailable("memory_provider_initialization_failed") from exc


def add_chunk(
    *, user, session: HistorySession, job: MemoryIngestJob, chunk: MemoryChunk
) -> list[str]:
    if chunk.content_sha256 != job.content_sha256:
        raise MemoryUnavailable("memory_source_changed")
    memory = get_memory()
    memory_user_id = account_memory_id(user)

    # A worker can crash after Mem0 accepts a request but before the SQLite
    # ledger is marked succeeded.  Persisting source_key in Mem0 metadata and
    # checking it first makes retries idempotent instead of creating additive
    # duplicate memories.
    existing = memory.get_all(
        filters={"user_id": memory_user_id, "source_key": job.source_key},
        top_k=1000,
    )
    existing_rows = (
        existing.get("results", existing.get("memories", []))
        if isinstance(existing, dict)
        else existing
    )
    if isinstance(existing_rows, list):
        existing_ids = [
            row.get("id") or row.get("memory_id")
            for row in existing_rows
            if isinstance(row, dict) and isinstance(row.get("id") or row.get("memory_id"), str)
        ]
        if existing_ids:
            return existing_ids

    result = memory.add(
        list(chunk.messages),
        user_id=memory_user_id,
        metadata={
            "source": "ansatz_history",
            "source_key": job.source_key,
            "history_session_id": session.external_id,
            "history_session_pk": str(session.pk),
            "parent_session_id": str(session.parent_session_id or ""),
            "model": session.model,
            "started_at": session.started_at.isoformat() if session.started_at else "",
            "redaction_version": job.redaction_version,
            "schema_version": "1",
        },
    )
    if isinstance(result, dict):
        rows = result.get("results", result.get("memories", []))
    else:
        rows = result
    if not isinstance(rows, list):
        return []
    ids = []
    for row in rows:
        if isinstance(row, dict):
            memory_id = row.get("id") or row.get("memory_id")
            if isinstance(memory_id, str) and memory_id:
                ids.append(memory_id)
    return ids


def search_memories(*, user, query: str, limit: int = 5):
    if not query.strip():
        raise ValueError("query_required")
    limit = max(1, min(int(limit), 20))
    result = get_memory().search(
        query.strip(),
        filters={"user_id": account_memory_id(user)},
        top_k=limit,
        rerank=bool(getattr(settings, "MEMORY_RERANK_ENABLED", False)),
    )
    return result.get("results", result.get("memories", [])) if isinstance(result, dict) else result


def list_memories(*, user):
    result = get_memory().get_all(filters={"user_id": account_memory_id(user)})
    return result.get("results", result.get("memories", [])) if isinstance(result, dict) else result


def list_all_memories(*, requester) -> list[dict]:
    """Return every extracted memory with its local session and owner context."""
    if not getattr(requester, "is_superuser", False):
        raise MemoryNotFound("memory_not_found")

    memory = get_memory()
    identities = AccountIdentity.objects.select_related("user").all()
    jobs = list(
        MemoryIngestJob.objects.filter(mem0_memory_ids__isnull=False)
        .select_related("owner", "session")
        .order_by("created_at", "id")
    )
    by_memory_id: dict[str, MemoryIngestJob] = {}
    for job in jobs:
        if isinstance(job.mem0_memory_ids, list):
            for memory_id in job.mem0_memory_ids:
                if isinstance(memory_id, str) and memory_id:
                    by_memory_id.setdefault(memory_id, job)

    rows: list[dict] = []
    for identity in identities:
        result = memory.get_all(filters={"user_id": str(identity.account_id)}, top_k=1000)
        values = (
            result.get("results", result.get("memories", []))
            if isinstance(result, dict)
            else result
        )
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            memory_id = value.get("id") or value.get("memory_id")
            if not isinstance(memory_id, str) or not memory_id:
                continue
            job = by_memory_id.get(memory_id)
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            session = job.session if job else None
            owner = job.owner if job else identity.user
            source = (
                metadata.get("source")
                or (session.source if session else "")
                or "ansatz_history"
            )
            created_at = (
                value.get("created_at")
                or value.get("createdAt")
                or metadata.get("created_at")
            )
            started_at = (
                session.started_at
                if session and session.started_at
                else metadata.get("started_at") or created_at
            )
            tags = [{"label": "来源", "value": _memory_source_label(source), "kind": "source"}]
            formatted_time = _format_memory_time(started_at)
            if formatted_time:
                tags.append({"label": "时间", "value": formatted_time, "kind": "time"})
            model = metadata.get("model") or (session.model if session else "")
            if model:
                tags.append({"label": "模型", "value": model, "kind": "model"})
            rows.append(
                {
                    "id": memory_id,
                    "memory": (
                        value.get("memory")
                        or value.get("text")
                        or value.get("content")
                        or ""
                    ),
                    "created_at": created_at,
                    "user": owner.username,
                    "user_id": owner.pk,
                    "session": session,
                    "metadata": metadata,
                    "tags": tags,
                }
            )
    return rows


def _memory_source_label(source) -> str:
    labels = {
        "ansatz_history": "会话历史",
        "history_import": "历史导入",
    }
    value = str(source or "").strip()
    return labels.get(value, value or "未知来源")


def _format_memory_time(value) -> str:
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%d %H:%M")


def owned_memory_ids(*, user) -> set[str]:
    """Return Mem0 ids previously recorded for this owner in the outbox ledger."""
    ids: set[str] = set()
    for values in MemoryIngestJob.objects.filter(owner=user).values_list(
        "mem0_memory_ids", flat=True
    ):
        if not isinstance(values, list):
            continue
        ids.update(value for value in values if isinstance(value, str) and value)
    return ids


def delete_memory(*, user, memory_id: str) -> None:
    if not memory_id or len(memory_id) > 200:
        raise ValueError("memory_id_required")
    if memory_id not in owned_memory_ids(user=user):
        raise MemoryNotFound("memory_not_found")
    get_memory().delete(memory_id=memory_id)


def delete_all_memories(*, user) -> None:
    get_memory().delete_all(user_id=account_memory_id(user))
