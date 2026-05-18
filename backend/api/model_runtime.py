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
IMAGE_SLEEPING_KEY_PREFIX = "image:omni-sleeping:"


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


def vllm_sleep_mode_enabled() -> bool:
    return _bool_setting("VLLM_SLEEP_MODE_ENABLED", False)


def image_sleep_mode_enabled() -> bool:
    return _bool_setting("IMAGE_SLEEP_MODE_ENABLED", vllm_sleep_mode_enabled())


def get_vllm_container_config(model: str) -> VllmContainerConfig | None:
    raw_config = getattr(settings, "VLLM_MODEL_CONTAINERS", {}).get(model)
    return _parse_container_config("VLLM_MODEL_CONTAINERS", model, raw_config)


def get_image_container_config(model: str) -> VllmContainerConfig | None:
    raw_config = getattr(settings, "IMAGE_MODEL_CONTAINERS", {}).get(model)
    return _parse_container_config("IMAGE_MODEL_CONTAINERS", model, raw_config)


def _parse_container_config(
    setting_name: str,
    model: str,
    raw_config,
) -> VllmContainerConfig | None:
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
            f"{setting_name}['{model}'] must define service_name and container_name."
        )
    return VllmContainerConfig(
        service_name=str(service_name),
        container_name=str(container_name),
    )


def is_managed_vllm_model(model: str) -> bool:
    return get_vllm_container_config(model) is not None


def is_managed_image_model(model: str) -> bool:
    return get_image_container_config(model) is not None


def _managed_vllm_models() -> list[str]:
    return list(getattr(settings, "VLLM_MODEL_CONTAINERS", {}).keys())


def _managed_image_models() -> list[str]:
    return list(getattr(settings, "IMAGE_MODEL_CONTAINERS", {}).keys())


def _managed_inflight_models() -> list[str]:
    return [
        *_managed_vllm_models(),
        *[f"image:{image_model}" for image_model in _managed_image_models()],
    ]


def _inflight_cache_key(model: str) -> str:
    return f"{VLLM_INFLIGHT_KEY_PREFIX}{model}"


def _image_sleeping_cache_key(model: str) -> str:
    return f"{IMAGE_SLEEPING_KEY_PREFIX}{model}"


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
            for other_model in _managed_inflight_models()
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


def _container_health_status(container_name: str) -> str | None:
    result = _run_command(
        _docker_command(
            "inspect",
            "-f",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            container_name,
        ),
        timeout=getattr(settings, "VLLM_DOCKER_COMMAND_TIMEOUT_SECONDS", 30),
        check=False,
    )
    if result.returncode != 0:
        return None
    status = result.stdout.strip().lower()
    if not status or status == "none":
        return None
    return status


def _wait_until_healthy_if_configured(container_name: str) -> None:
    timeout = float(getattr(settings, "VLLM_MODEL_START_TIMEOUT_SECONDS", 900))
    deadline = time.monotonic() + timeout
    last_status = ""

    while time.monotonic() < deadline:
        if not _container_running(container_name):
            raise VllmRuntimeError(f"Container '{container_name}' stopped before becoming healthy.")

        health_status = _container_health_status(container_name)
        if health_status is None:
            return
        if health_status == "healthy":
            return
        last_status = health_status
        time.sleep(float(getattr(settings, "VLLM_RUNTIME_POLL_SECONDS", 1.0)))

    suffix = f" Last health status: {last_status}." if last_status else ""
    raise VllmRuntimeError(f"Timed out waiting for container '{container_name}' to become healthy.{suffix}")


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
    _wait_until_healthy_if_configured(config.container_name)


def _restart_container(config: VllmContainerConfig) -> None:
    _stop_container(config.container_name)
    _remove_stopped_container(config.container_name)
    _start_container(config)


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


def _image_model_base_url(model: str) -> str:
    try:
        return str(settings.IMAGE_MODELS[model]["base_url"]).rstrip("/")
    except KeyError as exc:
        raise VllmRuntimeError(f"Unsupported managed image model '{model}'.") from exc


def _wait_for_image_model_ready(model: str) -> None:
    base_url = _image_model_base_url(model)
    headers = {"Authorization": f"Bearer {settings.IMAGE_API_KEY}"} if settings.IMAGE_API_KEY else {}
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
        f"Timed out waiting for image model '{model}' to become ready at {base_url}.{detail}"
    )


def _vllm_server_root(model: str) -> str:
    base_url = str(settings.VLLM_MODELS[model]).rstrip("/")
    if base_url.endswith("/v1"):
        return base_url[:-3]
    return base_url


def _vllm_runtime_headers() -> dict[str, str]:
    api_key = str(getattr(settings, "VLLM_API_KEY", ""))
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _image_runtime_headers() -> dict[str, str]:
    api_key = str(getattr(settings, "IMAGE_API_KEY", ""))
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _vllm_runtime_timeout() -> float:
    return float(getattr(settings, "VLLM_SLEEP_ENDPOINT_TIMEOUT_SECONDS", 900))


def _parse_sleep_state(payload) -> bool | None:
    if isinstance(payload, bool):
        return payload
    if isinstance(payload, str):
        clean = payload.strip().lower()
        if clean in {"true", "1", "yes"}:
            return True
        if clean in {"false", "0", "no"}:
            return False
    if isinstance(payload, dict):
        for key in ("is_sleeping", "sleeping", "isSleeping"):
            if key in payload:
                return _parse_sleep_state(payload[key])
    return None


def is_vllm_model_sleeping(model: str) -> bool | None:
    try:
        response = requests.get(
            f"{_vllm_server_root(model)}/is_sleeping",
            headers=_vllm_runtime_headers(),
            timeout=settings.LLM_MODEL_HEALTH_TIMEOUT_SECONDS,
        )
        if getattr(response, "status_code", None) == 404:
            return None
        response.raise_for_status()
        try:
            return _parse_sleep_state(response.json())
        except ValueError:
            return _parse_sleep_state(response.text)
    except (KeyError, RequestException):
        return None


def _post_vllm_runtime_endpoint(model: str, endpoint: str, *, params: dict | None = None) -> None:
    try:
        response = requests.post(
            f"{_vllm_server_root(model)}/{endpoint.lstrip('/')}",
            headers=_vllm_runtime_headers(),
            params=params or {},
            timeout=_vllm_runtime_timeout(),
        )
        response.raise_for_status()
    except RequestException as exc:
        details = ""
        response = getattr(exc, "response", None)
        if response is not None:
            details = (getattr(response, "text", "") or "").strip()
        suffix = f" Response body: {details}" if details else ""
        raise VllmRuntimeError(
            f"vLLM sleep-mode endpoint '{endpoint}' failed for model '{model}': {exc}{suffix}"
        ) from exc


def _post_image_runtime_endpoint(model: str, endpoint: str, *, payload: dict) -> None:
    try:
        response = requests.post(
            f"{_image_model_base_url(model)}/{endpoint.lstrip('/')}",
            headers=_image_runtime_headers(),
            json=payload,
            timeout=_vllm_runtime_timeout(),
        )
        response.raise_for_status()
    except RequestException as exc:
        details = ""
        response = getattr(exc, "response", None)
        if response is not None:
            details = (getattr(response, "text", "") or "").strip()
        suffix = f" Response body: {details}" if details else ""
        raise VllmRuntimeError(
            f"Image model runtime endpoint '{endpoint}' failed for model '{model}': {exc}{suffix}"
        ) from exc


def _image_sleep_stage_ids(model: str) -> list[int]:
    raw_stage_ids = getattr(settings, "IMAGE_MODEL_SLEEP_STAGE_IDS", {}).get(model, [0])
    if isinstance(raw_stage_ids, int):
        raw_values = [raw_stage_ids]
    elif isinstance(raw_stage_ids, str):
        raw_values = [
            stage_id.strip()
            for stage_id in raw_stage_ids.split(",")
            if stage_id.strip()
        ]
    else:
        try:
            raw_values = list(raw_stage_ids)
        except TypeError as exc:
            raise VllmRuntimeError(
                f"IMAGE_MODEL_SLEEP_STAGE_IDS['{model}'] must be a list of stage ids."
            ) from exc

    try:
        stage_ids = [int(stage_id) for stage_id in raw_values]
    except (TypeError, ValueError) as exc:
        raise VllmRuntimeError(
            f"IMAGE_MODEL_SLEEP_STAGE_IDS['{model}'] must contain only integer stage ids."
        ) from exc

    if not stage_ids:
        raise VllmRuntimeError(
            f"IMAGE_MODEL_SLEEP_STAGE_IDS['{model}'] must include at least one stage id."
        )
    return stage_ids


def _clear_image_model_status_cache() -> None:
    for model in _managed_image_models():
        cache.delete(f"image:model-status:{model}")


def _mark_image_model_awake(model: str) -> None:
    cache.delete(_image_sleeping_cache_key(model))


def _mark_image_model_sleeping(model: str) -> None:
    cache.set(_image_sleeping_cache_key(model), True, timeout=None)


def _image_model_sleeping(model: str) -> bool:
    config = get_image_container_config(model)
    if config is None or not _container_running(config.container_name):
        _mark_image_model_awake(model)
        return False
    return cache.get(_image_sleeping_cache_key(model)) is True


def _sleep_image_model(model: str, *, level: int | None = None) -> None:
    config = get_image_container_config(model)
    if config is None or not _container_running(config.container_name):
        _mark_image_model_awake(model)
        return

    if not image_sleep_mode_enabled():
        _stop_container(config.container_name)
        _mark_image_model_awake(model)
        return

    if _image_model_sleeping(model):
        return

    try:
        _wake_image_model(model)
        _post_image_runtime_endpoint(
            model,
            "omni/sleep",
            payload={
                "stage_ids": _image_sleep_stage_ids(model),
                "level": int(
                    level if level is not None else getattr(settings, "IMAGE_SLEEP_LEVEL", 1)
                ),
            },
        )
    except VllmRuntimeError:
        if not _bool_setting("IMAGE_SLEEP_FALLBACK_STOP_ENABLED", True):
            raise
        _stop_container(config.container_name)
        _mark_image_model_awake(model)
        return

    _mark_image_model_sleeping(model)


def _wake_image_model(model: str) -> None:
    config = get_image_container_config(model)
    if (
        not image_sleep_mode_enabled()
        or config is None
        or not _container_running(config.container_name)
    ):
        return

    _post_image_runtime_endpoint(
        model,
        "omni/wakeup",
        payload={"stage_ids": _image_sleep_stage_ids(model)},
    )
    _mark_image_model_awake(model)


def _sleep_running_image_model_containers(*, except_model: str | None = None) -> None:
    changed_any = False
    for image_model in _managed_image_models():
        if image_model == except_model:
            continue
        config = get_image_container_config(image_model)
        if config is not None and _container_running(config.container_name):
            _sleep_image_model(
                image_model,
                level=int(getattr(settings, "IMAGE_SWITCH_SLEEP_LEVEL", 1)),
            )
            changed_any = True

    if changed_any:
        _clear_image_model_status_cache()


def _sleep_running_vllm_model_containers(*, except_model: str | None = None) -> None:
    changed_any = False
    active_model = cache.get(ACTIVE_VLLM_MODEL_KEY)
    for vllm_model in _managed_vllm_models():
        if vllm_model == except_model:
            continue
        config = get_vllm_container_config(vllm_model)
        if config is not None and _container_running(config.container_name):
            _sleep_vllm_model(
                vllm_model,
                level=int(getattr(settings, "VLLM_SWITCH_SLEEP_LEVEL", 1)),
            )
            changed_any = True

    if changed_any:
        if not vllm_sleep_mode_enabled() and (
            except_model is None or active_model != except_model
        ):
            cache.delete(ACTIVE_VLLM_MODEL_KEY)
        _clear_model_status_cache()


def _sleep_vllm_model(model: str, *, level: int | None = None) -> None:
    if not vllm_sleep_mode_enabled():
        config = get_vllm_container_config(model)
        if config is not None:
            _stop_container(config.container_name)
        return

    config = get_vllm_container_config(model)
    if config is None or not _container_running(config.container_name):
        return

    sleep_state = is_vllm_model_sleeping(model)
    if sleep_state is True:
        return
    if sleep_state is None:
        raise VllmRuntimeError(
            f"vLLM sleep mode is enabled, but '{model}' does not expose /is_sleeping. "
            "Recreate the container after adding VLLM_SERVER_DEV_MODE=1 and --enable-sleep-mode."
        )

    _post_vllm_runtime_endpoint(
        model,
        "sleep",
        params={
            "level": int(
                level if level is not None else getattr(settings, "VLLM_SLEEP_LEVEL", 1)
            )
        },
    )


def _wake_vllm_model(model: str) -> None:
    if not vllm_sleep_mode_enabled():
        return

    sleep_state = is_vllm_model_sleeping(model)
    if sleep_state is False:
        return
    if sleep_state is None:
        raise VllmRuntimeError(
            f"vLLM sleep mode is enabled, but '{model}' does not expose /is_sleeping. "
            "Recreate the container after adding VLLM_SERVER_DEV_MODE=1 and --enable-sleep-mode."
        )

    try:
        _post_vllm_runtime_endpoint(model, "wake_up")
        return
    except VllmRuntimeError as exc:
        config = get_vllm_container_config(model)
        if config is None or not _container_running(config.container_name):
            raise

        _restart_container(config)
        _wait_for_vllm_ready(model)
        restarted_sleep_state = is_vllm_model_sleeping(model)
        if restarted_sleep_state is None:
            raise VllmRuntimeError(
                f"Restarted '{model}' after wake failure, but /is_sleeping is unavailable."
            ) from exc
        if restarted_sleep_state is False:
            return
        try:
            _post_vllm_runtime_endpoint(model, "wake_up")
        except VllmRuntimeError as restart_exc:
            raise VllmRuntimeError(
                f"{exc} Restarted '{model}' once, but wake_up still failed: {restart_exc}"
            ) from restart_exc


def _clear_model_status_cache() -> None:
    for model in _managed_vllm_models():
        cache.delete(f"llm:model-status:{model}")


def ensure_vllm_model_ready(model: str) -> None:
    if not vllm_auto_switch_enabled() or not is_managed_vllm_model(model):
        return

    _switch_vllm_model(model)


def warmup_vllm_model(model: str, *, sleep_after: bool = True) -> None:
    if not vllm_auto_switch_enabled():
        raise VllmRuntimeError("vLLM auto-switching is disabled.")
    if not is_managed_vllm_model(model):
        raise VllmRuntimeError(f"Unsupported managed vLLM model '{model}'.")

    _switch_vllm_model(model)
    if sleep_after:
        _sleep_vllm_model(
            model,
            level=int(getattr(settings, "VLLM_WARMUP_SLEEP_LEVEL", 1)),
        )


def warmup_vllm_models(
    models: list[str] | tuple[str, ...] | None = None,
    *,
    sleep_after: bool = True,
) -> list[str]:
    selected_models = list(models) if models is not None else _managed_vllm_models()

    unknown_models = [
        model
        for model in selected_models
        if not is_managed_vllm_model(model)
    ]
    if unknown_models:
        raise VllmRuntimeError(
            "Unsupported managed vLLM model(s): " + ", ".join(unknown_models)
        )

    with _CacheLock(
        GPU_SLOT_LOCK_KEY,
        wait_timeout=float(getattr(settings, "VLLM_GPU_LOCK_WAIT_TIMEOUT_SECONDS", 900)),
        ttl=int(getattr(settings, "VLLM_GPU_LOCK_TTL_SECONDS", 7200)),
        poll_seconds=float(getattr(settings, "VLLM_RUNTIME_POLL_SECONDS", 1.0)),
    ):
        warmed_models = []
        for model in selected_models:
            warmup_vllm_model(model, sleep_after=sleep_after)
            warmed_models.append(model)

    if sleep_after:
        cache.delete(ACTIVE_VLLM_MODEL_KEY)
    _clear_model_status_cache()
    return warmed_models


def _switch_vllm_model(model: str) -> None:
    requested_config = get_vllm_container_config(model)
    if requested_config is None:
        return

    _wait_for_other_inflight_requests(model)
    _sleep_running_image_model_containers()

    active_model = cache.get(ACTIVE_VLLM_MODEL_KEY)
    if active_model and active_model != model and is_managed_vllm_model(active_model):
        active_config = get_vllm_container_config(active_model)
        if active_config is not None and _container_running(active_config.container_name):
            _sleep_vllm_model(
                active_model,
                level=int(getattr(settings, "VLLM_SWITCH_SLEEP_LEVEL", 1)),
            )

    was_running = _container_running(requested_config.container_name)
    if not was_running:
        _start_container(requested_config)

    if not _container_running(requested_config.container_name):
        raise VllmRuntimeError(
            f"vLLM container '{requested_config.container_name}' did not start."
        )

    _wait_for_vllm_ready(model)
    _wake_vllm_model(model)

    cache.set(ACTIVE_VLLM_MODEL_KEY, model, timeout=None)
    _clear_model_status_cache()


def _switch_image_model(model: str) -> None:
    requested_config = get_image_container_config(model)
    if requested_config is None:
        return

    _wait_for_other_inflight_requests(f"image:{model}")
    _sleep_running_vllm_model_containers()
    _sleep_running_image_model_containers(except_model=model)

    if not _container_running(requested_config.container_name):
        _start_container(requested_config)
        _mark_image_model_awake(model)

    if not _container_running(requested_config.container_name):
        raise VllmRuntimeError(
            f"Image model container '{requested_config.container_name}' did not start."
        )

    _wake_image_model(model)
    _wait_for_image_model_ready(model)
    _clear_image_model_status_cache()


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

        is_running = _container_running(requested_config.container_name)
        is_sleeping = (
            is_running
            and vllm_sleep_mode_enabled()
            and is_vllm_model_sleeping(model) is True
        )
        if active_model != model or not is_running or is_sleeping:
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


@contextmanager
def managed_image_model(model: str):
    if not vllm_auto_switch_enabled() or not is_managed_image_model(model):
        yield
        return

    inflight_model = f"image:{model}"
    with _CacheLock(
        GPU_SLOT_LOCK_KEY,
        wait_timeout=float(getattr(settings, "VLLM_GPU_LOCK_WAIT_TIMEOUT_SECONDS", 900)),
        ttl=int(getattr(settings, "VLLM_GPU_LOCK_TTL_SECONDS", 7200)),
        poll_seconds=float(getattr(settings, "VLLM_RUNTIME_POLL_SECONDS", 1.0)),
    ):
        _switch_image_model(model)

    _increment_inflight(inflight_model)
    try:
        yield
    finally:
        _decrement_inflight(inflight_model)
