import shlex
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache
from requests import RequestException


GPU_SLOT_LOCK_KEY = "llm:vllm-gpu-slot-lock"
ACTIVE_VLLM_MODEL_KEY = "llm:active-vllm-model"
VLLM_INFLIGHT_KEY_PREFIX = "llm:vllm-inflight:"


@dataclass(frozen=True)
class VllmContainerConfig:
    service_name: str
    container_name: str


class VllmRuntimeError(RuntimeError):
    pass


class _CacheLock:
    def __init__(self, key: str, *, wait_timeout: float, ttl: int, poll_seconds: float):
        self.key = key
        self.wait_timeout = wait_timeout
        self.ttl = ttl
        self.poll_seconds = poll_seconds
        self.token = uuid.uuid4().hex

    def __enter__(self):
        deadline = time.monotonic() + self.wait_timeout
        while time.monotonic() < deadline:
            if cache.add(self.key, self.token, timeout=self.ttl):
                return self
            time.sleep(self.poll_seconds)
        raise VllmRuntimeError(
            "Timed out waiting for the vLLM GPU slot. Another model request is still running."
        )

    def __exit__(self, exc_type, exc, traceback):
        try:
            if cache.get(self.key) == self.token:
                cache.delete(self.key)
        except Exception:
            pass


def _bool_setting(name: str, default: bool = False) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def vllm_auto_switch_enabled() -> bool:
    return _bool_setting("VLLM_AUTO_SWITCH_ENABLED", False)


def get_vllm_container_config(model: str) -> VllmContainerConfig | None:
    raw_config = getattr(settings, "VLLM_MODEL_CONTAINERS", {}).get(model)
    if not raw_config:
        return None

    if isinstance(raw_config, str):
        return VllmContainerConfig(
            service_name=raw_config,
            container_name=raw_config,
        )

    service_name = raw_config.get("service_name") or raw_config.get("service")
    container_name = raw_config.get("container_name") or raw_config.get("container")
    if not service_name or not container_name:
        raise VllmRuntimeError(
            f"VLLM_MODEL_CONTAINERS['{model}'] must define service_name and container_name."
        )
    return VllmContainerConfig(
        service_name=str(service_name),
        container_name=str(container_name),
    )


def is_managed_vllm_model(model: str) -> bool:
    return get_vllm_container_config(model) is not None


def _managed_vllm_models() -> list[str]:
    return list(getattr(settings, "VLLM_MODEL_CONTAINERS", {}).keys())


def _inflight_cache_key(model: str) -> str:
    return f"{VLLM_INFLIGHT_KEY_PREFIX}{model}"


def _inflight_count(model: str) -> int:
    try:
        return max(0, int(cache.get(_inflight_cache_key(model), 0) or 0))
    except (TypeError, ValueError):
        return 0


def _increment_inflight(model: str) -> None:
    key = _inflight_cache_key(model)
    cache.add(key, 0, timeout=None)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=None)


def _decrement_inflight(model: str) -> None:
    key = _inflight_cache_key(model)
    try:
        count = cache.decr(key)
    except ValueError:
        cache.set(key, 0, timeout=None)
        return

    if count < 0:
        cache.set(key, 0, timeout=None)


def _wait_for_other_inflight_requests(model: str) -> None:
    timeout = float(getattr(settings, "VLLM_INFLIGHT_DRAIN_TIMEOUT_SECONDS", 7200))
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        busy_models = [
            other_model
            for other_model in _managed_vllm_models()
            if other_model != model and _inflight_count(other_model) > 0
        ]
        if not busy_models:
            return
        time.sleep(float(getattr(settings, "VLLM_RUNTIME_POLL_SECONDS", 1.0)))

    raise VllmRuntimeError(
        f"Timed out waiting for active vLLM requests to finish before switching to '{model}'."
    )


def _command_text(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _run_command(
    command: list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VllmRuntimeError(f"Required command was not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VllmRuntimeError(f"Command timed out: {_command_text(command)}") from exc

    if check and completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        if details:
            raise VllmRuntimeError(f"Command failed: {_command_text(command)}\n{details}")
        raise VllmRuntimeError(f"Command failed: {_command_text(command)}")

    return completed


def _compose_command(*args: str) -> list[str]:
    command = shlex.split(str(getattr(settings, "VLLM_DOCKER_COMPOSE_COMMAND", "docker compose")))
    return [*command, *args]


def _docker_command(*args: str) -> list[str]:
    return ["docker", *args]


def _container_status(container_name: str) -> str | None:
    result = _run_command(
        _docker_command("inspect", "-f", "{{.State.Status}}", container_name),
        timeout=getattr(settings, "VLLM_DOCKER_COMMAND_TIMEOUT_SECONDS", 30),
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip().lower()


def _container_running(container_name: str) -> bool:
    return _container_status(container_name) == "running"


def _wait_until_stopped(container_name: str) -> None:
    timeout = float(getattr(settings, "VLLM_CONTAINER_STOP_TIMEOUT_SECONDS", 60))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _container_running(container_name):
            return
        time.sleep(float(getattr(settings, "VLLM_RUNTIME_POLL_SECONDS", 1.0)))
    raise VllmRuntimeError(f"Timed out waiting for container '{container_name}' to stop.")


def _stop_container(container_name: str) -> None:
    if not _container_running(container_name):
        return

    stop_grace_seconds = int(getattr(settings, "VLLM_CONTAINER_STOP_GRACE_SECONDS", 10))
    _run_command(
        _docker_command("stop", "--time", str(stop_grace_seconds), container_name),
        timeout=stop_grace_seconds + float(getattr(settings, "VLLM_DOCKER_COMMAND_TIMEOUT_SECONDS", 30)),
    )
    _wait_until_stopped(container_name)


def _remove_stopped_container(container_name: str) -> None:
    status = _container_status(container_name)
    if status is None or status == "running":
        return

    _run_command(
        _docker_command("rm", container_name),
        timeout=getattr(settings, "VLLM_DOCKER_COMMAND_TIMEOUT_SECONDS", 30),
    )


def _start_container(config: VllmContainerConfig) -> None:
    _remove_stopped_container(config.container_name)
    _run_command(
        _compose_command("up", "-d", config.service_name),
        cwd=getattr(settings, "VLLM_DOCKER_COMPOSE_DIR", None),
        timeout=float(getattr(settings, "VLLM_DOCKER_START_TIMEOUT_SECONDS", 180)),
    )


def _model_is_listed(model: str, payload: dict) -> bool:
    model_items = payload.get("data")
    if not isinstance(model_items, list):
        return True
    model_ids = {
        item.get("id")
        for item in model_items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return not model_ids or model in model_ids


def _wait_for_vllm_ready(model: str) -> None:
    base_url = str(settings.VLLM_MODELS[model]).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.VLLM_API_KEY}"} if settings.VLLM_API_KEY else {}
    timeout = float(getattr(settings, "VLLM_MODEL_START_TIMEOUT_SECONDS", 900))
    deadline = time.monotonic() + timeout
    last_error = ""

    while time.monotonic() < deadline:
        try:
            response = requests.get(
                f"{base_url}/models",
                headers=headers,
                timeout=settings.LLM_MODEL_HEALTH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            if _model_is_listed(model, payload):
                return
            last_error = "server is running, but the requested served model is not listed"
        except (RequestException, ValueError) as exc:
            last_error = str(exc)

        time.sleep(float(getattr(settings, "VLLM_RUNTIME_POLL_SECONDS", 1.0)))

    detail = f" Last error: {last_error}" if last_error else ""
    raise VllmRuntimeError(
        f"Timed out waiting for vLLM model '{model}' to become ready at {base_url}.{detail}"
    )


def _clear_model_status_cache() -> None:
    for model in _managed_vllm_models():
        cache.delete(f"llm:model-status:{model}")


def ensure_vllm_model_ready(model: str) -> None:
    if not vllm_auto_switch_enabled() or not is_managed_vllm_model(model):
        return

    _switch_vllm_model(model)


def _switch_vllm_model(model: str) -> None:
    requested_config = get_vllm_container_config(model)
    if requested_config is None:
        return

    _wait_for_other_inflight_requests(model)

    for other_model in _managed_vllm_models():
        if other_model == model:
            continue
        other_config = get_vllm_container_config(other_model)
        if other_config is not None:
            _stop_container(other_config.container_name)

    if not _container_running(requested_config.container_name):
        _start_container(requested_config)

    if not _container_running(requested_config.container_name):
        raise VllmRuntimeError(
            f"vLLM container '{requested_config.container_name}' did not start."
        )

    _wait_for_vllm_ready(model)
    cache.set(ACTIVE_VLLM_MODEL_KEY, model, timeout=None)
    _clear_model_status_cache()


def _register_vllm_inference(model: str) -> None:
    with _CacheLock(
        GPU_SLOT_LOCK_KEY,
        wait_timeout=float(getattr(settings, "VLLM_GPU_LOCK_WAIT_TIMEOUT_SECONDS", 900)),
        ttl=int(getattr(settings, "VLLM_GPU_LOCK_TTL_SECONDS", 7200)),
        poll_seconds=float(getattr(settings, "VLLM_RUNTIME_POLL_SECONDS", 1.0)),
    ):
        requested_config = get_vllm_container_config(model)
        active_model = cache.get(ACTIVE_VLLM_MODEL_KEY)
        if requested_config is None:
            return

        if active_model != model or not _container_running(requested_config.container_name):
            _switch_vllm_model(model)

        _increment_inflight(model)


@contextmanager
def managed_vllm_model(model: str):
    if not vllm_auto_switch_enabled() or not is_managed_vllm_model(model):
        yield
        return

    _register_vllm_inference(model)
    try:
        yield
    finally:
        _decrement_inflight(model)
