## Development Docker Services

This setup runs only infrastructure services:

- vLLM OpenAI-compatible API servers
- PostgreSQL
- pgAdmin
- Langfuse observability stack

Django and Next.js run outside Docker.

The base `docker-compose.yml` stays stable and contains shared infrastructure.
vLLM model containers live in `docker-compose.override.yml`, which Docker Compose
merges automatically when you run commands from this directory.

### Start

```bash
cd environments/dev
docker compose up -d
```

vLLM model servers:

- `gpt-oss-20b`: `http://127.0.0.1:8001/v1`

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

The Django backend routes by the OpenAI request `model` field, so more than one
model server can run at the same time when additional services are added.

The current GPT-OSS model is loaded from `models/OpenAI`. That directory is the
Hugging Face/vLLM-ready model root with `config.json`, tokenizer files, chat
template, the safetensors index, and safetensors shards. The nested
`models/OpenAI/original` files are not mounted as the served model path.

### Change The Model

vLLM does not serve multiple base models from one OpenAI-compatible server.
The supported pattern is multiple vLLM server instances plus a routing layer.
Here, Docker Compose starts one vLLM container per model and Django is the router.

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

You can also type the served model name in the chat UI model field for a single request.

### Containers And Data

This vLLM dev stack keeps separate containers and data folders from earlier local stacks:

- vLLM API container: `vllm-gpt-oss-20b-dev`
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
