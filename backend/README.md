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
  - `GET /api/knowledge/documents/`
  - `POST /api/knowledge/documents/`
  - `DELETE /api/knowledge/documents/<document_id>/`
  - `POST /api/knowledge/search/`
- LLM gateway integration:
  - `gpt-oss-20b` -> `POST http://127.0.0.1:8001/v1/chat/completions`
  - `qwen3-32b-awq` -> `POST http://127.0.0.1:8002/v1/chat/completions`
  - `qwq-32b-awq` -> `POST http://127.0.0.1:8003/v1/chat/completions`
  - `deepseek-r1-distill-qwen-32b-awq` -> `POST http://127.0.0.1:8004/v1/chat/completions`
  - `qwen3:14b` -> `POST http://127.0.0.1:11434/v1/chat/completions`
- Langfuse tracing for vLLM and Ollama OpenAI-compatible chat completions:
  - Langfuse UI -> `http://127.0.0.1:3001`
  - Default dev project keys are initialized by Docker Compose.
- Full conversation history is sent to the selected LLM as a **system message** before the latest user message.
- Optional RAG uses Milvus for vector search, Qwen3-VL Embedding 2B at `http://127.0.0.1:8011/v1`, and Qwen3-VL Reranker 2B at `http://127.0.0.1:8012/v1`.
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
    "deepseek-r1-distill-qwen-32b-awq": "http://127.0.0.1:8004/v1",
}
VLLM_MODEL_CONTEXT_TOKENS = {
    "gpt-oss-20b": 32768,
    "qwen3-32b-awq": 32768,
    "qwq-32b-awq": 32768,
    "deepseek-r1-distill-qwen-32b-awq": 32768,
}
VLLM_TIMEOUT_SECONDS = 120
VLLM_AUTO_SWITCH_ENABLED = True
VLLM_SLEEP_MODE_ENABLED = True
VLLM_SLEEP_LEVEL = 1
VLLM_SWITCH_SLEEP_LEVEL = 1
VLLM_WARMUP_SLEEP_LEVEL = 1
VLLM_MODEL_CONTAINERS = {
    "gpt-oss-20b": {"service_name": "vllm-gpt-oss-20b", "container_name": "vllm-gpt-oss-20b-dev"},
    "qwen3-32b-awq": {"service_name": "vllm-qwen3-32b-awq", "container_name": "vllm-qwen3-32b-awq-dev"},
    "qwq-32b-awq": {"service_name": "vllm-qwq-32b-awq", "container_name": "vllm-qwq-32b-awq-dev"},
    "deepseek-r1-distill-qwen-32b-awq": {"service_name": "vllm-deepseek-r1-distill-qwen-32b-awq", "container_name": "vllm-deepseek-r1-distill-qwen-32b-awq-dev"},
}
OLLAMA_MODEL = "qwen3:14b"
OLLAMA_API_KEY = "ollama"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_CONTEXT_LENGTH = 32768
OLLAMA_TIMEOUT_SECONDS = 300
LLM_MODEL = "gpt-oss-20b"
LLM_MAX_COMPLETION_TOKENS = 32768
LLM_MODELS = {
    "gpt-oss-20b": {"provider": "vllm", "base_url": "http://127.0.0.1:8001/v1", "max_context_tokens": 32768},
    "qwen3-32b-awq": {"provider": "vllm", "base_url": "http://127.0.0.1:8002/v1", "max_context_tokens": 32768},
    "qwq-32b-awq": {"provider": "vllm", "base_url": "http://127.0.0.1:8003/v1", "max_context_tokens": 32768},
    "deepseek-r1-distill-qwen-32b-awq": {"provider": "vllm", "base_url": "http://127.0.0.1:8004/v1", "max_context_tokens": 32768},
    "qwen3:14b": {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1", "max_context_tokens": 32768},
}
RAG_ENABLED = True
RAG_MILVUS_URI = "http://127.0.0.1:19530"
RAG_MILVUS_COLLECTION = "gptclone_knowledge_chunks_qwen3_vl_2b"
RAG_EMBEDDING_MODEL = "qwen3-vl-embedding-2b"
RAG_EMBEDDING_BASE_URL = "http://127.0.0.1:8011/v1"
RAG_EMBEDDING_DIM = 2048
RAG_RERANK_PROVIDER = "vllm-rerank"
RAG_RERANK_MODEL = "qwen3-vl-reranker-2b"
RAG_RERANK_BASE_URL = "http://127.0.0.1:8012/v1"
RAG_LANGFUSE_CAPTURE_CONTENT = False
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
starts the requested model if needed, wakes it if it is sleeping, waits until
the OpenAI-compatible `/models` endpoint is ready, then leaves that server awake
for concurrent requests using the same model. It does not sleep a model just
because the request finished. A request for a different vLLM model waits for
current in-flight requests to finish, sleeps the active server at level `1`, and
wakes the requested one.

RAG embedding and reranker containers are the exception: Django only ensures
they are running and ready. It does not send sleep or wake calls to RAG models,
and text/image model switches do not sleep them.

To pre-create and warm all managed vLLM containers from the backend, run:

```bash
python manage.py warmup_vllm_models
```

### RAG

Milvus runs from the dev Docker Compose stack on `127.0.0.1:19530`. Add a text
document to the authenticated user's knowledge base:

```bash
POST /api/knowledge/documents/
{"title": "Handbook", "source_name": "handbook.md", "content": "..."}
```

Chat requests use RAG by default when `RAG_ENABLED=1`; pass
`"rag_enabled": false` on send, regenerate, or edit requests to skip retrieval
for a single turn. Search can be tested directly with:

```bash
POST /api/knowledge/search/
{"query": "What does the handbook say?", "top_k": 10}
```

RAG writes Langfuse traces for indexing, direct search, and chat-context
retrieval. The traces include nested observations for embedding, Milvus
collection/load, vector insert/delete/search, reranking, and final prompt
context assembly. By default, RAG-specific observations store document/query
hashes, ids, counts, scores, and character lengths rather than raw document
content. Set `RAG_LANGFUSE_CAPTURE_CONTENT=1` if you want those RAG spans to
also capture raw queries and document snippets.

### Migrations and superuser

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

When prompted, use your phone number as the username value.

### Run

```bash
python manage.py runserver 0.0.0.0:8000
```

Admin: `http://127.0.0.1:8000/admin/` or `http://46.100.12.235:8000/admin/`

Set `PUBLIC_DEV_HOST` before starting the frontend and backend if the public IP changes.
With the dev Nginx service running, the proxied admin URL is `http://127.0.0.1/admin/` or `http://46.100.12.235/admin/`.
