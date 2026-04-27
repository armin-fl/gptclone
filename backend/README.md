## Backend (Django + DRF + PostgreSQL + vLLM)

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
- vLLM integration via OpenAI-compatible chat-completions endpoints:
  - `gpt-oss-20b` -> `POST http://127.0.0.1:8001/v1/chat/completions`
- Langfuse tracing for vLLM chat completions:
  - Langfuse UI -> `http://127.0.0.1:3001`
  - Default dev project keys are initialized by Docker Compose.
- Full conversation history is sent to vLLM as a **system message** before the latest user message.

### Install dependencies (run yourself)

```bash
pip install -r requirements.txt
```

### Development settings

Development values are hard-coded in `config/settings.py`; no env file is used.

Current hard-coded vLLM model routing:

```python
VLLM_MODEL = "gpt-oss-20b"
VLLM_API_KEY = "EMPTY"
VLLM_MODELS = {
    "gpt-oss-20b": "http://127.0.0.1:8001/v1",
}
VLLM_TIMEOUT_SECONDS = 120
LANGFUSE_BASE_URL = "http://127.0.0.1:3001"
LANGFUSE_PUBLIC_KEY = "pk-lf-dev-project-key"
LANGFUSE_SECRET_KEY = "sk-lf-dev-secret-key"
```

The Langfuse SDK reads those values from environment variables if set, otherwise
the dev defaults above match `environments/dev/docker-compose.yml`.

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
