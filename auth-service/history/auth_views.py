from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, redirect_to_login
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

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
            "username": request.user.get_username(),
            "server_time": timezone.now().isoformat(),
            "session_expires_at": expires_at.isoformat(),
        }
    )
    response["Cache-Control"] = "no-store"
    return response
