## Development Docker Services

This setup runs only infrastructure services:

- vLLM OpenAI-compatible API servers
- Ollama local model servers
- PostgreSQL
- pgAdmin
- Langfuse observability stack

Django and Next.js run outside Docker.

The base `docker-compose.yml` stays stable and contains shared infrastructure.
Model containers live in `docker-compose.override.yml`, which Docker Compose
merges automatically when you run commands from this directory.

### Start

```bash
cd environments/dev
docker compose up -d
```

vLLM model servers:

- `gpt-oss-20b`: `http://127.0.0.1:8001/v1`

Ollama model servers:

- `qwen3:14b`: `http://127.0.0.1:11434/v1`

Langfuse:

- UI: `http://127.0.0.1:3001`
- Login: `admin@example.com` / `admin12345`
- Dev public key: `pk-lf-dev-project-key`
- Dev secret key: `sk-lf-dev-secret-key`

The vLLM service uses the pinned release image `vllm/vllm-openai:v0.19.1`.
When Docker shows `vllm-gpt-oss-20b Pulling`, it is pulling the vLLM container
image layers. The model is loaded from the local bind mount because the command
uses `--model /models/gpt-oss-20b`, not `--model openai/gpt-oss-20b`.

vLLM exports OpenTelemetry traces to Langfuse at
`http://langfuse-web:3000/api/public/otel/v1/traces`. The Django backend also
wraps each OpenAI-compatible chat completion with Langfuse so the trace includes
the request, response, model, conversation session id, and user id.

The Django backend routes by the request `model` field. vLLM and Ollama models
both use OpenAI-compatible chat completions, and the Django backend wraps both
providers with Langfuse's OpenAI client so application-level traces include the
request, response, model, conversation session id, and user id.

The current GPT-OSS model is loaded from `models/OpenAI`. That directory is the
Hugging Face/vLLM-ready model root with `config.json`, tokenizer files, chat
template, the safetensors index, and safetensors shards. The nested
`models/OpenAI/original` files are not mounted as the served model path.

The current Qwen model is loaded from `models/Ollama/Qwen3-14b`. That directory
is mounted as Ollama's `/root/.ollama/models` store, so the existing
`blobs/` and `manifests/registry.ollama.ai/library/qwen3/14b` files are used
directly by the official `ollama/ollama` image.

### Change The Model

vLLM does not serve multiple base models from one OpenAI-compatible server.
The supported pattern is one model server per model plus a routing layer.
Here, Docker Compose starts vLLM and Ollama containers, and Django is the router.

Model settings are hard-coded. To add a model, leave `docker-compose.yml` alone
and add one service to `docker-compose.override.yml`:

```bash
services:
  vllm-new-model:
    image: vllm/vllm-openai:v0.19.1
    container_name: vllm-new-model-dev
    restart: unless-stopped
    ipc: host
    ports:
      - "8003:8000"
    volumes:
      - ../../data/huggingface:/root/.cache/huggingface
    command:
      - --model
      - HuggingFace/Model-Name
      - --served-model-name
      - new-model-name
      - --dtype
      - auto
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

Then set the same served model name and base URL in `backend/config/settings.py`:

```python
VLLM_MODELS = {
    "gpt-oss-20b": "http://127.0.0.1:8001/v1",
    "new-model-name": "http://127.0.0.1:8003/v1",
}
```

To add another Ollama model already stored on disk, add another Ollama service
that mounts the model store directory as `/root/.ollama/models`, then add it to
`LLM_MODELS` with provider `ollama` and a base URL ending in `/v1`.

If you add another vLLM container and want server-side traces, also set:

```yaml
environment:
  OTEL_EXPORTER_OTLP_TRACES_PROTOCOL: http/protobuf
  OTEL_EXPORTER_OTLP_TRACES_HEADERS: Authorization=Basic cGstbGYtZGV2LXByb2plY3Qta2V5OnNrLWxmLWRldi1zZWNyZXQta2V5,x-langfuse-ingestion-version=4
  OTEL_SERVICE_NAME: vllm-new-model
command:
  - --otlp-traces-endpoint
  - http://langfuse-web:3000/api/public/otel/v1/traces
```

Start or recreate only that model container:

```bash
docker compose up -d --force-recreate vllm-new-model
```

You can choose any enabled backend model from the chat UI model selector.

### Containers And Data

This dev stack keeps separate containers and data folders from earlier local stacks:

- vLLM API container: `vllm-gpt-oss-20b-dev`
- Ollama API container: `ollama-qwen3-14b-dev`
- PostgreSQL container: `vllm-postgres-dev`
- pgAdmin container: `vllm-pgadmin-dev`
- Langfuse UI container: `vllm-langfuse-web-dev`
- Langfuse worker container: `vllm-langfuse-worker-dev`
- PostgreSQL data: `data/vllm-postgres`
- pgAdmin data: `data/vllm-pgadmin`

The PostgreSQL and pgAdmin images are still `postgres:gptclone` and `pgadmin:gptclone`.
Langfuse uses separate Docker named volumes for its Postgres, ClickHouse, Redis,
and MinIO data.

### Stop

```bash
cd environments/dev
docker compose down
```

### pgAdmin

Open `http://127.0.0.1:5050` and sign in with:

- Email: `f.armin8090@gmail.com`
- Password: `admin123`

To connect pgAdmin to PostgreSQL from inside Docker, create a server with:

- Host: `postgres`
- Port: `5432`
- Username: `postgres`
- Password: `postgres`
- Database: `gptclone`
