from __future__ import annotations

import base64
import json
import socket
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


class LangfuseUnavailable(RuntimeError):
    pass


class LangfusePayloadTooLarge(LangfuseUnavailable):
    pass


COLLECTION_PAGE_SIZE = 100
MAX_COLLECTION_OBSERVATIONS = 2_000


def _default_transport(request: Request, timeout: int) -> tuple[int, bytes]:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - private configured origin
        payload = response.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise LangfusePayloadTooLarge("Langfuse response exceeded the size limit")
        return response.status, payload


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LangfuseClient:
    def __init__(self, *, transport=None):
        self.base_url = settings.LANGFUSE_INTERNAL_BASE_URL.rstrip("/")
        self.public_key = settings.LANGFUSE_PROJECT_PUBLIC_KEY
        self.secret_key = settings.LANGFUSE_PROJECT_SECRET_KEY
        self.timeout = settings.LANGFUSE_API_TIMEOUT_SECONDS
        self.max_pages = settings.LANGFUSE_API_MAX_PAGES
        self.transport = transport or _default_transport

    def _get(self, params: dict[str, str]) -> dict:
        encoded_credentials = base64.b64encode(
            f"{self.public_key}:{self.secret_key}".encode()
        ).decode("ascii")
        url = f"{self.base_url}/v2/observations?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {encoded_credentials}",
                "User-Agent": "ansatz-traces-dashboard/1",
            },
        )
        try:
            status, payload = self.transport(request, self.timeout)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise LangfuseUnavailable("Langfuse request failed") from error
        if status != 200:
            raise LangfuseUnavailable("Langfuse returned an unavailable status")
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError, UnicodeDecodeError) as error:
            raise LangfuseUnavailable("Langfuse returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise LangfuseUnavailable("Langfuse returned an invalid response")
        return decoded

    def list_observations(
        self,
        *,
        user_id: str,
        days: int | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        include_io: bool = False,
    ) -> list[dict]:
        if not user_id:
            raise ValueError("user_id is required")
        if days is not None and (from_time is None or to_time is None):
            now = datetime.now(timezone.utc)
            to_time = _iso_z(now)
            from_time = _iso_z(now - timedelta(days=days))

        fields = "basic,time,model,usage,metrics,trace_context"
        if include_io:
            raise ValueError("bulk observation queries cannot include IO")
        base_params = {
            "fields": fields,
            "limit": str(COLLECTION_PAGE_SIZE),
            "userId": user_id,
        }
        if from_time:
            base_params["fromStartTime"] = from_time
        if to_time:
            base_params["toStartTime"] = to_time
        if session_id:
            base_params["sessionId"] = session_id
        if trace_id:
            base_params["traceId"] = trace_id

        observations: list[dict] = []
        cursor = None
        seen_cursors: set[str] = set()
        for _ in range(self.max_pages):
            params = dict(base_params)
            if cursor:
                params["cursor"] = cursor
            response = self._get(params)
            data = response.get("data")
            meta = response.get("meta")
            if not isinstance(data, list) or not isinstance(meta, dict):
                raise LangfuseUnavailable("Langfuse returned an invalid page")
            if not all(isinstance(item, dict) for item in data):
                raise LangfuseUnavailable("Langfuse returned an invalid observation")
            if len(observations) + len(data) > MAX_COLLECTION_OBSERVATIONS:
                raise LangfuseUnavailable("Langfuse observation limit exceeded")
            observations.extend(data)
            cursor = meta.get("cursor")
            if cursor is None:
                return observations
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise LangfuseUnavailable("Langfuse returned an invalid cursor")
            seen_cursors.add(cursor)
        raise LangfuseUnavailable("Langfuse pagination exceeded the safety bound")

    def get_observation(
        self, *, user_id: str, trace_id: str, observation_id: str
    ) -> dict | None:
        if not user_id or not trace_id or not observation_id:
            raise ValueError("user_id, trace_id, and observation_id are required")
        filters = [
            {
                "type": "string",
                "column": column,
                "operator": "=",
                "value": value,
            }
            for column, value in (
                ("userId", user_id),
                ("traceId", trace_id),
                ("id", observation_id),
            )
        ]
        response = self._get(
            {
                "fields": "basic,time,model,usage,metrics,trace_context,io,metadata",
                "filter": json.dumps(filters, separators=(",", ":")),
                "limit": "1",
            }
        )
        data = response.get("data")
        meta = response.get("meta")
        if not isinstance(data, list) or not isinstance(meta, dict):
            raise LangfuseUnavailable("Langfuse returned an invalid detail page")
        if not data:
            return None
        if len(data) != 1 or not isinstance(data[0], dict):
            raise LangfuseUnavailable("Langfuse returned an invalid observation detail")
        item = data[0]
        if (
            str(item.get("userId")) != user_id
            or str(item.get("traceId")) != trace_id
            or str(item.get("id")) != observation_id
        ):
            raise LangfuseUnavailable("Langfuse returned an invalid observation detail")
        return item


def get_langfuse_client() -> LangfuseClient:
    return LangfuseClient()
