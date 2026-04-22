## Development Docker Services

This setup runs only infrastructure services:

- vLLM OpenAI-compatible API servers
- PostgreSQL
- pgAdmin

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

The vLLM service uses the pinned release image `vllm/vllm-openai:v0.19.1`.
When Docker shows `vllm-gpt-oss-20b Pulling`, it is pulling the vLLM container
image layers. The model is loaded from the local bind mount because the command
uses `--model /models/gpt-oss-20b`, not `--model openai/gpt-oss-20b`.

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
- PostgreSQL data: `data/vllm-postgres`
- pgAdmin data: `data/vllm-pgadmin`

The PostgreSQL and pgAdmin images are still `postgres:gptclone` and `pgadmin:gptclone`.

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
