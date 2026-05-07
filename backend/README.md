## Backend (Django + DRF + PostgreSQL + LLM Gateway)

This backend is set up for **development only**.

### What is implemented

- Custom user model in `accounts` using `phone_number` as identity.
- Django admin support for the custom user and chat data.
- Chat models in `api`:
  - `Conversation`
  - `Message`
- DRF endpoints:
  - `GET /api/conversations/`
  - `POST /api/conversations/`
  - `GET /api/conversations/<conversation_id>/`
  - `POST /api/conversations/<conversation_id>/messages/`
- LLM gateway integration:
  - `gpt-oss-20b` -> `POST http://127.0.0.1:8001/v1/chat/completions`
  - `qwen3-32b-awq` -> `POST http://127.0.0.1:8002/v1/chat/completions`
  - `qwq-32b-awq` -> `POST http://127.0.0.1:8003/v1/chat/completions`
  - `qwen3:14b` -> `POST http://127.0.0.1:11434/v1/chat/completions`
- Langfuse tracing for vLLM and Ollama OpenAI-compatible chat completions:
  - Langfuse UI -> `http://127.0.0.1:3001`
  - Default dev project keys are initialized by Docker Compose.
- Full conversation history is sent to the selected LLM as a **system message** before the latest user message.
- Phone + OTP authentication with SimpleJWT symmetric HS256 access/refresh tokens:
  - `POST /api/auth/request-otp/`
  - `POST /api/auth/verify-otp/`
  - `POST /api/auth/refresh/`
  - `GET /api/auth/me/`
- Conversation endpoints require `Authorization: Bearer <access_token>` and only return the authenticated user's data.

### Install dependencies (run yourself)

```bash
pip install -r requirements.txt
```

### Development settings

Development values are hard-coded in `config/settings.py`; no env file is used.

Current hard-coded LLM model routing:

```python
VLLM_MODEL = "gpt-oss-20b"
VLLM_API_KEY = "EMPTY"
VLLM_MODELS = {
    "gpt-oss-20b": "http://127.0.0.1:8001/v1",
    "qwen3-32b-awq": "http://127.0.0.1:8002/v1",
    "qwq-32b-awq": "http://127.0.0.1:8003/v1",
}
VLLM_MODEL_CONTEXT_TOKENS = {
    "gpt-oss-20b": 131072,
    "qwen3-32b-awq": 40960,
    "qwq-32b-awq": 40960,
}
VLLM_TIMEOUT_SECONDS = 120
VLLM_AUTO_SWITCH_ENABLED = True
VLLM_MODEL_CONTAINERS = {
    "gpt-oss-20b": {"service_name": "vllm-gpt-oss-20b", "container_name": "vllm-gpt-oss-20b-dev"},
    "qwen3-32b-awq": {"service_name": "vllm-qwen3-32b-awq", "container_name": "vllm-qwen3-32b-awq-dev"},
    "qwq-32b-awq": {"service_name": "vllm-qwq-32b-awq", "container_name": "vllm-qwq-32b-awq-dev"},
}
OLLAMA_MODEL = "qwen3:14b"
OLLAMA_API_KEY = "ollama"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_CONTEXT_LENGTH = 40960
OLLAMA_TIMEOUT_SECONDS = 300
LLM_MODEL = "gpt-oss-20b"
LLM_MAX_COMPLETION_TOKENS = 131072
LLM_MODELS = {
    "gpt-oss-20b": {"provider": "vllm", "base_url": "http://127.0.0.1:8001/v1", "max_context_tokens": 131072},
    "qwen3-32b-awq": {"provider": "vllm", "base_url": "http://127.0.0.1:8002/v1", "max_context_tokens": 40960},
    "qwq-32b-awq": {"provider": "vllm", "base_url": "http://127.0.0.1:8003/v1", "max_context_tokens": 40960},
    "qwen3:14b": {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1", "max_context_tokens": 40960},
}
LANGFUSE_BASE_URL = "http://127.0.0.1:3001"
LANGFUSE_PUBLIC_KEY = "pk-lf-dev-project-key"
LANGFUSE_SECRET_KEY = "sk-lf-dev-secret-key"
JWT_SECRET_KEY = "<env override, otherwise derived from SECRET_KEY for dev>"
JWT_ACCESS_TOKEN_SECONDS = 900
JWT_REFRESH_TOKEN_SECONDS = 604800
OTP_CODE_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
```

The Langfuse SDK reads those values from environment variables if set, otherwise
the dev defaults above match `environments/dev/docker-compose.yml`.
With `VLLM_AUTO_SWITCH_ENABLED`, each vLLM request owns one GPU slot: Django
stops other managed vLLM containers, starts the requested one, waits until the
OpenAI-compatible `/models` endpoint is ready, then leaves that container warm
for concurrent requests using the same model. A request for a different vLLM
model waits for current in-flight requests to finish before stopping the active
container and starting the new one.

### Migrations and superuser

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

When prompted, use your phone number as the username value.

### Run

```bash
python manage.py runserver
```

Admin: `http://127.0.0.1:8000/admin/`
