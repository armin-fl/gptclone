## Development Docker Services

This setup runs infrastructure services by default:

- vLLM OpenAI-compatible API servers, started on demand by Django
- Ollama local model servers
- PostgreSQL
- pgAdmin
- Langfuse observability stack

Django and Next.js run outside Docker.

The base `docker-compose.yml` stays stable and contains shared infrastructure.
Model containers live in `docker-compose.override.yml`, which Docker Compose
merges automatically when you run commands from this directory.

Nginx is included as a no-cache development reverse proxy using the official
full Debian stable image (`nginx:1.30.0-trixie`). It listens on port 80 by
default and forwards app traffic to the local Next.js dev server on port 3000.
The `/api` routes intentionally stay on Next.js so the BFF/auth cookie flow keeps
working. Django-only paths (`/admin/`, `/static/`, and `/media/`) are proxied to
the local Django dev server on port 8000.

### Start

```bash
cd environments/dev
docker compose up -d
```

vLLM model servers, warmed automatically during `docker compose up -d` and then
put to sleep to free VRAM:

- `gpt-oss-20b`: `http://127.0.0.1:8001/v1`
- `qwen3-32b-awq`: `http://127.0.0.1:8002/v1`
- `qwq-32b-awq`: `http://127.0.0.1:8003/v1`
- `deepseek-r1-distill-qwen-32b-awq`: `http://127.0.0.1:8004/v1`

Ollama model servers:

- `qwen3:14b`: `http://127.0.0.1:11434/v1`

Langfuse:

- UI: `http://127.0.0.1:3001`
- Login: `admin@example.com` / `admin12345`
- Dev public key: `pk-lf-dev-project-key`
- Dev secret key: `sk-lf-dev-secret-key`

Nginx dev proxy:

- App: `http://127.0.0.1` or `http://46.100.12.235`
- Django admin through proxy: `http://127.0.0.1/admin/` or `http://46.100.12.235/admin/`
- Override the host port with `NGINX_HTTP_PORT=8080 docker compose up -d nginx`

The vLLM services use the local `vllm:gptclone` image. When Docker shows
`vllm-gpt-oss-20b Pulling`, it is pulling the vLLM container image layers. The
model is loaded from the local bind mount because the command uses
`--model /models/gpt-oss-20b`, not `--model openai/gpt-oss-20b`.

vLLM exports OpenTelemetry traces to Langfuse at
`http://langfuse-web:3000/api/public/otel/v1/traces`. The Django backend also
wraps each OpenAI-compatible chat completion with Langfuse so the trace includes
the request, response, model, conversation session id, and user id.
Model containers do not hard-depend on the `langfuse-web` service, so the
override file can still be rendered or used for model-only commands.

The Django backend routes by the request `model` field. For vLLM models it also
owns a single GPU slot. Before a request, Django waits for other in-flight vLLM
requests to finish, starts the requested container if needed, wakes the
requested server, waits for `/models`, and then sends the completion. Same-model
requests can run concurrently on the already-awake server. Django does not sleep
a model just because the request finished. It sleeps the current active model
only when a new request needs a different model, then wakes the requested model.
The default switch sleep level is `1` for the fastest model-to-model reuse.

The dev Compose override also starts each vLLM container sequentially, waits for
`/v1/models`, calls `/sleep?level=${GPTCLONE_WARM_SLEEP_LEVEL:-1}`, and only
then starts the next model.

You can also run the same backend warmup manually:

```bash
cd backend
python manage.py warmup_vllm_models
```

The current GPT-OSS model is loaded from `models/Vllm/OpenAI`. That directory is the
Hugging Face/vLLM-ready model root with `config.json`, tokenizer files, chat
template, the safetensors index, and safetensors shards. The nested
`models/Vllm/OpenAI/original` files are not mounted as the served model path.
The GPT-OSS Harmony parser also needs the public `o200k_base.tiktoken` vocab.
The container caches it in `data/tiktoken-rs-cache` via `TIKTOKEN_RS_CACHE_DIR`
so the file can be pre-seeded and reused across container recreates.

The Qwen3 32B AWQ, QwQ 32B AWQ, and DeepSeek R1 Distill Qwen 32B AWQ vLLM
models are loaded from `models/Vllm/Qwen3-32B-AWQ`,
`models/Vllm/QwQ-32B-AWQ`, and
`models/Vllm/deepseek-r1-distill-qwen-32b-awq`.
Their vLLM containers use the model context window: `--max-model-len 32768`,
`--kv-cache-dtype fp8`, `--max-num-seqs ${VLLM_32B_MAX_NUM_SEQS:-2}`, `--enforce-eager`, and
`--gpu-memory-utilization ${VLLM_32B_GPU_MEMORY_UTILIZATION:-0.66}`. CPU model offload is explicitly disabled with
`--cpu-offload-gb 0` and `--offload-group-size 0`; vLLM still uses CPU for
normal orchestration, tokenization, networking, and process scheduling. vLLM
services are started in a dependency chain so `docker compose up -d` warms and
sleeps one model before loading the next one. They also use `restart: "no"` so
Docker does not resurrect old model containers and make them compete for the
same GPU after a crash or daemon restart. The DeepSeek service also uses
`--generation-config vllm` so its
local `generation_config.json` does not disable cache behavior, and
`--reasoning-parser deepseek_r1` so vLLM can expose reasoning metadata in the
OpenAI-compatible response.
The GPT-OSS container uses
`--max-model-len 32768` and
`--gpu-memory-utilization ${VLLM_GPT_OSS_20B_GPU_MEMORY_UTILIZATION:-0.60}`.
The default `VLLM_SLEEP_LEVEL=1` keeps switching and same-model reuse as fast as
possible. Raising GPU memory utilization can improve KV-cache capacity, but can
also make warmup or model switches fail with CUDA OOM if another server has not
finished sleeping yet.
Set `VLLM_32B_MAX_NUM_SEQS` before starting a 32B service to tune same-model
parallelism. The default is `2`; two full 32k prompts may still exceed KV-cache
capacity, but smaller concurrent prompts should work. Use `1` if the GPU runs
out of memory, or a higher value if the model and context length fit
comfortably.
Existing running containers must be recreated before changed `max-num-seqs` or
`gpu-memory-utilization` values take effect.
vLLM sleep mode is enabled with `VLLM_SERVER_DEV_MODE=1` and
`--enable-sleep-mode`. Because those development endpoints expose operational
controls, vLLM ports are bound to `127.0.0.1` only.

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
    restart: "no"
    ipc: host
    entrypoint:
      - /bin/sh
      - /usr/local/bin/vllm-warm-sleep-entrypoint
    environment:
      VLLM_SERVER_DEV_MODE: "1"
      GPTCLONE_WARM_SLEEP_ON_START: "1"
      GPTCLONE_WARM_SLEEP_LEVEL: "1"
    ports:
      - "127.0.0.1:8010:8000"
    volumes:
      - ./vllm/warm-sleep-entrypoint.sh:/usr/local/bin/vllm-warm-sleep-entrypoint:ro
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
VLLM_MODEL_CONTAINERS = {
    "gpt-oss-20b": {
        "service_name": "vllm-gpt-oss-20b",
        "container_name": "vllm-gpt-oss-20b-dev",
    },
    "new-model-name": {
        "service_name": "vllm-new-model",
        "container_name": "vllm-new-model-dev",
    },
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

Start or recreate only that model container manually when troubleshooting:

```bash
docker compose up -d --force-recreate vllm-new-model
```

You can choose any enabled backend model from the chat UI model selector.
Managed sleeping vLLM containers remain selectable and wake automatically.
Backend chat completions request the maximum remaining context for each model,
capped by `LLM_MAX_COMPLETION_TOKENS`, which defaults to `32768`.

Manual one-at-a-time switching still works:

```bash
docker compose up -d vllm-qwen3-32b-awq
docker compose stop vllm-qwen3-32b-awq
docker compose up -d vllm-qwq-32b-awq
```

### Containers And Data

This dev stack keeps separate containers and data folders from earlier local stacks:

- vLLM API container: `vllm-gpt-oss-20b-dev`
- vLLM Qwen3 32B AWQ API container: `vllm-qwen3-32b-awq-dev`
- vLLM QwQ 32B AWQ API container: `vllm-qwq-32b-awq-dev`
- vLLM DeepSeek R1 Distill Qwen 32B AWQ API container: `vllm-deepseek-r1-distill-qwen-32b-awq-dev`
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
