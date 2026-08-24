import json
import re
import secrets
from datetime import timedelta
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView, redirect_to_login
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_GET, require_POST

from .client_sessions import (
    ClientSessionResolution,
    issue_client_session,
    resolve_client_session,
    revoke_client_session,
)
from .models import ClientSession
from .trace_tokens import (
    introspect_trace_token,
    issue_trace_token,
    revoke_device_trace_tokens,
    revoke_session_trace_tokens,
)

ABSOLUTE_EXPIRY_KEY = "hermes_absolute_session_expires_at"


class HermesLoginView(LoginView):
    def form_valid(self, form):
        response = super().form_valid(form)
        expires_at = timezone.now() + timedelta(
            seconds=settings.HERMES_SESSION_ABSOLUTE_AGE_SECONDS
        )
        self.request.session[ABSOLUTE_EXPIRY_KEY] = expires_at.isoformat()
        self.request.session.set_expiry(expires_at)
        return response


class HermesLogoutView(LogoutView):
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            revoke_session_trace_tokens(
                user=request.user,
                session_key=request.session.session_key or "",
            )
        return super().post(request, *args, **kwargs)


def _absolute_expiry(request):
    value = request.session.get(ABSOLUTE_EXPIRY_KEY)
    return parse_datetime(value) if isinstance(value, str) else None


def has_valid_absolute_session(request):
    expires_at = _absolute_expiry(request)
    return expires_at is not None and expires_at > timezone.now()


def hermes_session_required(view):
    @wraps(view)
    def absolute_checked(request, *args, **kwargs):
        if not has_valid_absolute_session(request):
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        return view(request, *args, **kwargs)

    return login_required(absolute_checked)


def _reject():
    response = JsonResponse({"authenticated": False}, status=401)
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def client_session(request):
    if not request.user.is_authenticated:
        return _reject()
    expires_at = _absolute_expiry(request)
    if expires_at is None or expires_at <= timezone.now():
        return _reject()
    response = JsonResponse(
        {
            "authenticated": True,
            "sub": str(request.user.pk),
            "username": request.user.get_username(),
            "role": "admin"
            if request.user.is_staff or request.user.is_superuser
            else "user",
            "server_time": timezone.now().isoformat(),
            "session_expires_at": expires_at.isoformat(),
            "trace_dashboard_url": "/traces/",
        }
    )
    response["Cache-Control"] = "no-store"
    return response


def _json_response(payload, *, status=200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _no_store(view):
    @wraps(view)
    def add_no_store(*args, **kwargs):
        response = view(*args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    return add_no_store


def _json_payload(request):
    if request.content_type != "application/json":
        return None, _json_response({"detail": "unsupported_media_type"}, status=415)
    try:
        payload = json.loads(request.body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None, _json_response({"detail": "invalid_request"}, status=400)
    if not isinstance(payload, dict):
        return None, _json_response({"detail": "invalid_request"}, status=400)
    return payload, None


def _installation_id(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    if parsed.version != 4 or str(parsed) != value.lower():
        return None
    return parsed


def _native_installation_id(value):
    installation_id = _installation_id(value)
    if installation_id is None or str(installation_id) != value:
        return None
    return installation_id


def _valid_issue_payload(payload):
    if set(payload) != {
        "installation_id",
        "client_version",
        "telemetry_schema_version",
    }:
        return None
    installation_id = _installation_id(payload.get("installation_id"))
    client_version = payload.get("client_version")
    schema_version = payload.get("telemetry_schema_version")
    if (
        installation_id is None
        or not isinstance(client_version, str)
        or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}", client_version)
        or schema_version != "1"
    ):
        return None
    return installation_id


def _native_session_issue_payload(payload):
    if set(payload) != {"installation_id", "client_version"}:
        return None
    installation_id = _native_installation_id(payload.get("installation_id"))
    client_version = payload.get("client_version")
    if (
        installation_id is None
        or not isinstance(client_version, str)
        or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}", client_version)
    ):
        return None
    return installation_id


def _strict_json_object(pairs):
    payload = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("Duplicate JSON object key")
        payload[key] = value
    return payload


def _native_json_payload(request):
    if request.content_type != "application/json":
        return None, _json_response({"detail": "unsupported_media_type"}, status=415)
    try:
        payload = json.loads(request.body, object_pairs_hook=_strict_json_object)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None, _json_response({"detail": "invalid_request"}, status=400)
    if not isinstance(payload, dict):
        return None, _json_response({"detail": "invalid_request"}, status=400)
    return payload, None


def _native_session_unavailable():
    return _json_response(
        {
            "state": "unavailable",
            "code": "invalid_session_credential",
            "retryable": True,
        },
        status=401,
    )


def _native_bearer_token(request):
    match = re.fullmatch(
        r"Bearer ([A-Za-z0-9_-]{32,128})",
        request.headers.get("Authorization", ""),
    )
    return match.group(1) if match else None


def _native_session_resolution(request):
    token = _native_bearer_token(request)
    installation_id = _native_installation_id(
        request.headers.get("X-Ansatz-Installation-ID", "")
    )
    if token is None or installation_id is None:
        return ClientSessionResolution(
            None,
            "invalid_session_credential",
            False,
        )
    return resolve_client_session(token=token, installation_id=installation_id)


def _native_session_resolution_response(resolution):
    if resolution.explicit_revocation:
        record = resolution.record
        if record.revoked_at is None:
            record = revoke_client_session(session=record, reason=resolution.code)
        return _json_response(
            {
                "state": "revoked",
                "code": resolution.code,
                "account_id": str(record.account.account_id),
                "session_id": str(record.session_id),
                "revoked_at": record.revoked_at.isoformat(),
                "retryable": False,
            },
            status=403,
        )
    if resolution.record is None:
        return _native_session_unavailable()
    return None


@_no_store
@csrf_protect
def _issue_native_client_session(request):
    if not request.user.is_authenticated or not has_valid_absolute_session(request):
        return _json_response({"detail": "authentication_required"}, status=401)
    payload, error = _native_json_payload(request)
    if error is not None:
        return error
    installation_id = _native_session_issue_payload(payload)
    if installation_id is None:
        return _json_response({"detail": "invalid_request"}, status=400)
    issued = issue_client_session(
        user=request.user,
        installation_id=installation_id,
        client_version=payload["client_version"],
    )
    record = issued.record
    return _json_response(
        {
            "account_id": str(record.account.account_id),
            "session_id": str(record.session_id),
            "session_token": issued.access_token,
            "installation_id": str(record.installation_id),
            "username": request.user.get_username(),
            "issued_at": record.created_at.isoformat(),
        },
        status=201,
    )


@csrf_exempt
def native_client_session(request):
    if request.method == "POST":
        return _issue_native_client_session(request)
    if request.method == "GET":
        resolution = _native_session_resolution(request)
        error = _native_session_resolution_response(resolution)
        if error is not None:
            return error
        record = resolution.record
        return _json_response(
            {
                "state": "active",
                "account_id": str(record.account.account_id),
                "session_id": str(record.session_id),
                "installation_id": str(record.installation_id),
                "username": record.account.user.get_username(),
                "server_time": timezone.now().isoformat(),
            }
        )
    response = _json_response({"detail": "method_not_allowed"}, status=405)
    response["Allow"] = "GET, POST"
    return response


@csrf_exempt
def native_client_session_current(request):
    if request.method != "DELETE":
        response = _json_response({"detail": "method_not_allowed"}, status=405)
        response["Allow"] = "DELETE"
        return response
    resolution = _native_session_resolution(request)
    error = _native_session_resolution_response(resolution)
    if error is not None:
        return error
    revoke_client_session(
        session=resolution.record,
        reason=ClientSession.RevocationReason.SIGNED_OUT,
    )
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def trace_token(request):
    if not request.user.is_authenticated or not has_valid_absolute_session(request):
        return _json_response({"detail": "authentication_required"}, status=401)
    payload, error = _json_payload(request)
    if error is not None:
        return error
    installation_id = _valid_issue_payload(payload)
    if installation_id is None or not request.session.session_key:
        return _json_response({"detail": "invalid_request"}, status=400)
    issued = issue_trace_token(
        user=request.user,
        session_key=request.session.session_key,
        installation_id=installation_id,
    )
    record = issued.record
    return _json_response(
        {
            "access_token": issued.access_token,
            "expires_at": record.expires_at.isoformat(),
            "expires_in": settings.TRACE_UPLOAD_TOKEN_TTL_SECONDS,
            "installation_id": str(record.installation_id),
        },
        status=200 if issued.rotated else 201,
    )


@csrf_exempt
@require_POST
def trace_token_introspect(request):
    supplied = request.headers.get("X-Ansatz-Internal-Token", "")
    expected = settings.TRACE_GATEWAY_INTERNAL_SECRET
    if not supplied or not secrets.compare_digest(supplied, expected):
        return _json_response({"active": False}, status=403)
    payload, error = _json_payload(request)
    if error is not None:
        return _json_response({"active": False}, status=error.status_code)
    record = introspect_trace_token(payload.get("token"))
    if record is None:
        return _json_response({"active": False})
    return _json_response(
        {
            "active": True,
            "token_id": str(record.token_id),
            "platform_user_id": str(record.user_id),
            "platform_username": record.user.get_username(),
            "installation_id": str(record.installation_id),
            "expires_at": record.expires_at.isoformat(),
            "scope": record.scope,
            "audience": record.audience,
        }
    )


@require_POST
def trace_token_revoke_device(request):
    if not request.user.is_authenticated or not has_valid_absolute_session(request):
        return _json_response({"detail": "authentication_required"}, status=401)
    payload, error = _json_payload(request)
    if error is not None:
        return error
    if set(payload) != {"installation_id"}:
        return _json_response({"detail": "invalid_request"}, status=400)
    installation_id = _installation_id(payload.get("installation_id"))
    if installation_id is None:
        return _json_response({"detail": "invalid_request"}, status=400)
    revoked = revoke_device_trace_tokens(
        user=request.user,
        installation_id=installation_id,
    )
    return _json_response(
        {"installation_id": str(installation_id), "revoked": revoked}
    )
