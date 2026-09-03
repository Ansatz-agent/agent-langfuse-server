import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def weak_production_secret(value: str) -> bool:
    lowered = value.lower()
    forbidden_fragments = (
        "django-insecure-",
        "replace-with-",
        "change-me",
        "changeme",
        "your-secret",
        "default-secret",
        "secret-key",
        "placeholder",
        "example",
        "password",
    )
    if any(fragment in lowered for fragment in forbidden_fragments):
        return True
    max_period = min(16, len(value) // 2)
    return any(
        value == (value[:period] * ((len(value) // period) + 1))[: len(value)]
        for period in range(1, max_period + 1)
    )


ENVIRONMENT = os.getenv("DJANGO_ENV", "production").strip().lower()
if ENVIRONMENT not in {"production", "development", "test"}:
    raise ImproperlyConfigured("DJANGO_ENV must be production, development, or test")

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if ENVIRONMENT == "production":
    if DEBUG:
        raise ImproperlyConfigured("DJANGO_DEBUG must be disabled in production")
    if not SECRET_KEY:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production")
    if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or weak_production_secret(SECRET_KEY):
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be a unique, high-entropy production secret"
        )
elif not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
ALLOWED_HOSTS = [
    item.strip()
    for item in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    if item.strip()
]
CSRF_TRUSTED_ORIGINS = [
    item.strip() for item in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if item.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "axes",
    "history.apps.HistoryConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "history.security_headers.SecurityHeadersMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        # SQLite ignores SELECT ... FOR UPDATE, so every write transaction
        # must take the database write lock at BEGIN (IMMEDIATE) to make
        # check-then-write service transactions safe across processes.  WAL
        # keeps readers (status polling) unblocked while a writer commits.
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
        # Concurrency tests need real SQLite file locking, which the default
        # in-memory test database cannot exercise.
        "TEST": {
            "NAME": os.getenv("DJANGO_TEST_DB_PATH", str(BASE_DIR / "test_db.sqlite3"))
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/auth/static/"
STATIC_ROOT = Path(os.getenv("DJANGO_STATIC_ROOT", str(BASE_DIR / "staticfiles")))
STATIC_BACKEND = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG or ENVIRONMENT == "test"
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": STATIC_BACKEND},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/traces/"
LOGOUT_REDIRECT_URL = "login"

AXES_FAILURE_LIMIT = 10
AXES_COOLOFF_TIME = 1
AXES_RESET_ON_SUCCESS = True
AXES_LOCK_OUT_AT_FAILURE = True
AXES_LOCKOUT_CALLABLE = "history.security.lockout_response"
AXES_IPWARE_PROXY_COUNT = 1
AXES_IPWARE_META_PRECEDENCE_ORDER = ("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR")

HISTORY_IMPORT_MAX_BYTES = int(os.getenv("HISTORY_IMPORT_MAX_BYTES", 25 * 1024 * 1024))
HISTORY_IMPORT_MAX_SESSIONS = int(os.getenv("HISTORY_IMPORT_MAX_SESSIONS", 2000))
HISTORY_IMPORT_MAX_MESSAGES = int(os.getenv("HISTORY_IMPORT_MAX_MESSAGES", 100000))
HISTORY_IMPORT_MAX_MESSAGES_PER_SESSION = int(
    os.getenv("HISTORY_IMPORT_MAX_MESSAGES_PER_SESSION", 20000)
)
HISTORY_IMPORT_MAX_MESSAGE_CHARS = int(os.getenv("HISTORY_IMPORT_MAX_MESSAGE_CHARS", 2000000))

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_NAME = "__Host-ansatz_sessionid"
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_NAME = "__Host-ansatz_csrftoken"
CSRF_COOKIE_PATH = "/"
CSRF_COOKIE_DOMAIN = None
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
HERMES_SESSION_ABSOLUTE_AGE_SECONDS = int(
    os.getenv("HERMES_SESSION_ABSOLUTE_AGE_SECONDS", str(14 * 24 * 60 * 60))
)
if HERMES_SESSION_ABSOLUTE_AGE_SECONDS < 300:
    raise ImproperlyConfigured(
        "HERMES_SESSION_ABSOLUTE_AGE_SECONDS must be at least 300"
    )
CLIENT_SESSION_LAST_SEEN_MIN_INTERVAL_SECONDS = int(
    os.getenv("CLIENT_SESSION_LAST_SEEN_MIN_INTERVAL_SECONDS", "60")
)
CLIENT_SESSION_ISSUANCE_RATE_LIMIT = int(
    os.getenv("CLIENT_SESSION_ISSUANCE_RATE_LIMIT", "10")
)
CLIENT_SESSION_ISSUANCE_RATE_WINDOW_SECONDS = int(
    os.getenv("CLIENT_SESSION_ISSUANCE_RATE_WINDOW_SECONDS", "3600")
)
TRACE_UPLOAD_TOKEN_TTL_SECONDS = 900
TRACE_UPLOAD_TOKEN_SCOPE = "trace:write"
TRACE_UPLOAD_TOKEN_AUDIENCE = "ansatz-trace-gateway"
TRACE_GATEWAY_INTERNAL_SECRET = os.getenv("TRACE_GATEWAY_INTERNAL_SECRET", "").strip()
if ENVIRONMENT == "production":
    if (
        len(TRACE_GATEWAY_INTERNAL_SECRET) < 32
        or len(set(TRACE_GATEWAY_INTERNAL_SECRET)) < 5
        or weak_production_secret(TRACE_GATEWAY_INTERNAL_SECRET)
    ):
        raise ImproperlyConfigured(
            "TRACE_GATEWAY_INTERNAL_SECRET must be a unique, high-entropy "
            "production secret"
        )
elif not TRACE_GATEWAY_INTERNAL_SECRET:
    TRACE_GATEWAY_INTERNAL_SECRET = secrets.token_urlsafe(32)
LANGFUSE_INTERNAL_BASE_URL = os.getenv(
    "LANGFUSE_INTERNAL_BASE_URL",
    "http://langfuse-web:3000/langfuse/api/public",
).rstrip("/")
LANGFUSE_PROJECT_PUBLIC_KEY = os.getenv("LANGFUSE_PROJECT_PUBLIC_KEY", "").strip()
LANGFUSE_PROJECT_SECRET_KEY = os.getenv("LANGFUSE_PROJECT_SECRET_KEY", "").strip()
LANGFUSE_API_TIMEOUT_SECONDS = 5
LANGFUSE_API_MAX_PAGES = 20
if ENVIRONMENT == "production" and (
    not LANGFUSE_PROJECT_PUBLIC_KEY or not LANGFUSE_PROJECT_SECRET_KEY
):
    raise ImproperlyConfigured(
        "LANGFUSE_PROJECT_PUBLIC_KEY and LANGFUSE_PROJECT_SECRET_KEY are required in production"
    )

# Mem0 is deliberately opt-in.  The outbox can be enabled independently so
# operators can enqueue a backfill before enabling provider calls.
MEMORY_ENABLED = env_bool("MEMORY_ENABLED", False)
MEMORY_OUTBOX_ENABLED = env_bool("MEMORY_OUTBOX_ENABLED", MEMORY_ENABLED)
MEMORY_OLLAMA_BASE_URL = os.getenv("MEMORY_OLLAMA_BASE_URL", "http://ollama:11434").strip()
MEMORY_MEM0_DIR = os.getenv("MEMORY_MEM0_DIR", "/data/mem0").strip()
MEMORY_TELEMETRY = env_bool("MEMORY_TELEMETRY", False)
MEMORY_OPENAI_BASE_URL = os.getenv("MEMORY_OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
MEMORY_RERANK_ENABLED = env_bool("MEMORY_RERANK_ENABLED", False)
MEMORY_RERANK_TOP_K = int(os.getenv("MEMORY_RERANK_TOP_K", "5"))
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

DATA_UPLOAD_MAX_MEMORY_SIZE = 26 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
