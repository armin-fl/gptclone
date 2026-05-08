"""Django settings for local development only."""

import os
import sys
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(BASE_DIR)


def env_bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


IS_TEST_COMMAND = any("pytest" in arg or arg == "test" for arg in sys.argv)

SECRET_KEY = "dev-only-secret-key-change-me"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "accounts",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "gptclone",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "127.0.0.1",
        "PORT": "5432",
    }
}

APP_REDIS_URL = os.environ.get("APP_REDIS_URL", "redis://127.0.0.1:6380/0")
if "test" in sys.argv:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "gptclone-tests",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": APP_REDIS_URL,
        }
    }

CHAT_PAGE_CACHE_SECONDS = int(os.environ.get("CHAT_PAGE_CACHE_SECONDS", "60"))
CHAT_CONVERSATION_PAGE_SIZE = int(os.environ.get("CHAT_CONVERSATION_PAGE_SIZE", "30"))
CHAT_MESSAGE_PAGE_SIZE = int(os.environ.get("CHAT_MESSAGE_PAGE_SIZE", "50"))
CHAT_PROMPT_HISTORY_MESSAGE_LIMIT = int(os.environ.get("CHAT_PROMPT_HISTORY_MESSAGE_LIMIT", "40"))

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
CSRF_TRUSTED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
}

JWT_SIGNING_KEY = os.environ.get("JWT_SECRET_KEY", f"{SECRET_KEY}-jwt-hs256-signing-key")
JWT_ACCESS_TOKEN_SECONDS = int(os.environ.get("JWT_ACCESS_TOKEN_SECONDS", "900"))
JWT_REFRESH_TOKEN_SECONDS = int(os.environ.get("JWT_REFRESH_TOKEN_SECONDS", "604800"))
OTP_CODE_TTL_SECONDS = int(os.environ.get("OTP_CODE_TTL_SECONDS", "300"))
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", "5"))

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(seconds=JWT_ACCESS_TOKEN_SECONDS),
    "REFRESH_TOKEN_LIFETIME": timedelta(seconds=JWT_REFRESH_TOKEN_SECONDS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": JWT_SIGNING_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

VLLM_MODEL = "gpt-oss-20b"
VLLM_API_KEY = os.environ.get("VLLM_API_KEY", "EMPTY")
VLLM_MODELS = {
    "gpt-oss-20b": "http://127.0.0.1:8001/v1",
    "qwen3-32b-awq": "http://127.0.0.1:8002/v1",
    "qwq-32b-awq": "http://127.0.0.1:8003/v1",
    "deepseek-r1-distill-qwen-32b-awq": "http://127.0.0.1:8004/v1",
}
VLLM_MODEL_CONTEXT_TOKENS = {
    "gpt-oss-20b": int(os.environ.get("VLLM_GPT_OSS_20B_CONTEXT_TOKENS", "32768")),
    "qwen3-32b-awq": int(os.environ.get("VLLM_QWEN3_32B_AWQ_CONTEXT_TOKENS", "32768")),
    "qwq-32b-awq": int(os.environ.get("VLLM_QWQ_32B_AWQ_CONTEXT_TOKENS", "32768")),
    "deepseek-r1-distill-qwen-32b-awq": int(
        os.environ.get("VLLM_DEEPSEEK_R1_DISTILL_QWEN_32B_AWQ_CONTEXT_TOKENS", "32768")
    ),
}
VLLM_TIMEOUT_SECONDS = 120
VLLM_MODEL_CONTAINERS = {
    "gpt-oss-20b": {
        "service_name": "vllm-gpt-oss-20b",
        "container_name": "vllm-gpt-oss-20b-dev",
    },
    "qwen3-32b-awq": {
        "service_name": "vllm-qwen3-32b-awq",
        "container_name": "vllm-qwen3-32b-awq-dev",
    },
    "qwq-32b-awq": {
        "service_name": "vllm-qwq-32b-awq",
        "container_name": "vllm-qwq-32b-awq-dev",
    },
    "deepseek-r1-distill-qwen-32b-awq": {
        "service_name": "vllm-deepseek-r1-distill-qwen-32b-awq",
        "container_name": "vllm-deepseek-r1-distill-qwen-32b-awq-dev",
    },
}
VLLM_AUTO_SWITCH_ENABLED = env_bool(
    "VLLM_AUTO_SWITCH_ENABLED",
    "0" if IS_TEST_COMMAND else "1",
)
VLLM_SLEEP_MODE_ENABLED = env_bool(
    "VLLM_SLEEP_MODE_ENABLED",
    "0" if IS_TEST_COMMAND else "1",
)
VLLM_SLEEP_LEVEL = int(os.environ.get("VLLM_SLEEP_LEVEL", "1"))
VLLM_SLEEP_ENDPOINT_TIMEOUT_SECONDS = float(os.environ.get("VLLM_SLEEP_ENDPOINT_TIMEOUT_SECONDS", "900"))
VLLM_DOCKER_COMPOSE_DIR = os.environ.get(
    "VLLM_DOCKER_COMPOSE_DIR",
    os.path.join(PROJECT_DIR, "environments", "dev"),
)
VLLM_DOCKER_COMPOSE_COMMAND = os.environ.get("VLLM_DOCKER_COMPOSE_COMMAND", "docker compose")
VLLM_DOCKER_COMMAND_TIMEOUT_SECONDS = float(os.environ.get("VLLM_DOCKER_COMMAND_TIMEOUT_SECONDS", "30"))
VLLM_DOCKER_START_TIMEOUT_SECONDS = float(os.environ.get("VLLM_DOCKER_START_TIMEOUT_SECONDS", "180"))
VLLM_CONTAINER_STOP_GRACE_SECONDS = int(os.environ.get("VLLM_CONTAINER_STOP_GRACE_SECONDS", "10"))
VLLM_CONTAINER_STOP_TIMEOUT_SECONDS = float(os.environ.get("VLLM_CONTAINER_STOP_TIMEOUT_SECONDS", "60"))
VLLM_MODEL_START_TIMEOUT_SECONDS = float(os.environ.get("VLLM_MODEL_START_TIMEOUT_SECONDS", "900"))
VLLM_GPU_LOCK_WAIT_TIMEOUT_SECONDS = float(os.environ.get("VLLM_GPU_LOCK_WAIT_TIMEOUT_SECONDS", "900"))
VLLM_GPU_LOCK_TTL_SECONDS = int(os.environ.get("VLLM_GPU_LOCK_TTL_SECONDS", "7200"))
VLLM_INFLIGHT_DRAIN_TIMEOUT_SECONDS = float(os.environ.get("VLLM_INFLIGHT_DRAIN_TIMEOUT_SECONDS", "7200"))
VLLM_RUNTIME_POLL_SECONDS = float(os.environ.get("VLLM_RUNTIME_POLL_SECONDS", "1.0"))
LLM_MODEL_HEALTH_TIMEOUT_SECONDS = float(os.environ.get("LLM_MODEL_HEALTH_TIMEOUT_SECONDS", "0.8"))
LLM_MODEL_HEALTH_CACHE_SECONDS = int(os.environ.get("LLM_MODEL_HEALTH_CACHE_SECONDS", "5"))

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "ollama")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_CONTEXT_LENGTH = int(os.environ.get("OLLAMA_CONTEXT_LENGTH", "32768"))
OLLAMA_MODELS = {
    OLLAMA_MODEL: OLLAMA_BASE_URL,
}
OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "300"))

LLM_MODEL = os.environ.get("LLM_MODEL", VLLM_MODEL)
LLM_MAX_COMPLETION_TOKENS = int(os.environ.get("LLM_MAX_COMPLETION_TOKENS", "32768"))
LLM_MODELS = {
    **{
        model: {
            "provider": "vllm",
            "base_url": base_url,
            "label": model,
            "max_context_tokens": VLLM_MODEL_CONTEXT_TOKENS[model],
            **(
                {"thinking_toggle": "vllm_chat_template"}
                if model == "qwen3-32b-awq"
                else {}
            ),
        }
        for model, base_url in VLLM_MODELS.items()
    },
    **{
        model: {
            "provider": "ollama",
            "base_url": base_url,
            "label": "Qwen3 14B",
            "max_context_tokens": OLLAMA_CONTEXT_LENGTH,
            **(
                {"thinking_toggle": "ollama_reasoning_effort"}
                if model == "qwen3:14b"
                else {}
            ),
        }
        for model, base_url in OLLAMA_MODELS.items()
    },
}

LANGFUSE_BASE_URL = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3001")
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-dev-project-key")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-dev-secret-key")

os.environ.setdefault("LANGFUSE_BASE_URL", LANGFUSE_BASE_URL)
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", LANGFUSE_PUBLIC_KEY)
os.environ.setdefault("LANGFUSE_SECRET_KEY", LANGFUSE_SECRET_KEY)
