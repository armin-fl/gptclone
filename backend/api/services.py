import re
from concurrent.futures import ThreadPoolExecutor

import requests
from django.conf import settings
from django.core.cache import cache
from langfuse import propagate_attributes
from langfuse.openai import OpenAI as LangfuseOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAIError
from requests import RequestException

from .model_runtime import is_managed_vllm_model, managed_vllm_model, vllm_auto_switch_enabled


class UnsupportedLlmModelError(RuntimeError):
    pass


class UnsupportedVllmModelError(RuntimeError):
    pass


THINKING_BLOCK_RE = re.compile(r"<think\b[^>]*>(.*?)</think>", re.IGNORECASE | re.DOTALL)
INCOMPLETE_THINKING_BLOCK_RE = re.compile(r"<think\b[^>]*>.*$", re.IGNORECASE | re.DOTALL)
VLLM_CHAT_TEMPLATE_THINKING_TOGGLE = "vllm_chat_template"
OLLAMA_REASONING_EFFORT_THINKING_TOGGLE = "ollama_reasoning_effort"


def strip_thinking_blocks(content: str) -> str:
    without_complete_blocks = THINKING_BLOCK_RE.sub("", content)
    without_incomplete_blocks = INCOMPLETE_THINKING_BLOCK_RE.sub("", without_complete_blocks)
    without_extra_blank_lines = re.sub(r"\n{3,}", "\n\n", without_incomplete_blocks)
    return without_extra_blank_lines.strip()


def has_thinking_content(content: str) -> bool:
    for match in THINKING_BLOCK_RE.finditer(content):
        if match.group(1).strip():
            return True

    incomplete_match = INCOMPLETE_THINKING_BLOCK_RE.search(THINKING_BLOCK_RE.sub("", content))
    if not incomplete_match:
        return False
    block_content = re.sub(r"^<think\b[^>]*>", "", incomplete_match.group(0), flags=re.IGNORECASE | re.DOTALL)
    return bool(block_content.strip())


def _thinking_toggle_type(model: str) -> str:
    return str(settings.LLM_MODELS.get(model, {}).get("thinking_toggle", ""))


def model_supports_thinking_toggle(model: str) -> bool:
    return _thinking_toggle_type(model) in {
        VLLM_CHAT_TEMPLATE_THINKING_TOGGLE,
        OLLAMA_REASONING_EFFORT_THINKING_TOGGLE,
    }


# Flow 4: called by ConversationSendMessageView.post() to turn previous Message rows into LLM context.
def build_history_as_system_message(conversation_messages) -> str:
    lines = []
    for msg in conversation_messages:
        role = msg.role.upper()
        content = strip_thinking_blocks(str(msg.content))
        if not content:
            continue
        lines.append(f"{role}: {content}")

    transcript = "\n".join(lines).strip()
    if not transcript:
        transcript = "(No previous conversation history yet.)"

    return (
        "You are continuing an existing conversation. "
        "Use the following full chat history as context.\n\n"
        f"{transcript}"
    )


# Flow 2: called by the view to reject unknown requested models before contacting an LLM provider.
def get_available_llm_models() -> list[str]:
    return list(settings.LLM_MODELS.keys())


def get_llm_model_config(model: str) -> dict:
    try:
        return settings.LLM_MODELS[model]
    except KeyError as exc:
        available_models = ", ".join(get_available_llm_models())
        raise UnsupportedLlmModelError(
            f"Unsupported LLM model '{model}'. Available models: {available_models}."
        ) from exc


def _model_status_cache_key(model: str) -> str:
    return f"llm:model-status:{model}"


def _provider_api_key(provider: str) -> str:
    if provider == "vllm":
        return settings.VLLM_API_KEY
    if provider == "ollama":
        return settings.OLLAMA_API_KEY
    return ""


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


def get_llm_model_status(model: str) -> dict:
    cached = cache.get(_model_status_cache_key(model))
    if cached is not None:
        return cached

    provider = ""
    server_reachable = False
    try:
        config = get_llm_model_config(model)
        provider = str(config.get("provider", ""))
        base_url = str(config["base_url"]).rstrip("/")
        api_key = _provider_api_key(provider)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = requests.get(
            f"{base_url}/models",
            headers=headers,
            timeout=settings.LLM_MODEL_HEALTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        server_reachable = True

        is_available = True
        reason = ""
        try:
            is_available = _model_is_listed(model, response.json())
            if not is_available:
                reason = "Model server is running, but this model is not served."
        except ValueError:
            pass
    except (UnsupportedLlmModelError, KeyError, RequestException) as exc:
        is_available = False
        reason = str(exc)

    is_managed = provider == "vllm" and is_managed_vllm_model(model)
    if not is_available and not server_reachable and is_managed and vllm_auto_switch_enabled():
        is_available = True
        reason = "Model container is stopped. It will start automatically on first request."

    status = {
        "id": model,
        "label": str(settings.LLM_MODELS.get(model, {}).get("label", model)),
        "provider": provider or str(settings.LLM_MODELS.get(model, {}).get("provider", "")),
        "supports_thinking_toggle": model_supports_thinking_toggle(model),
        "managed": is_managed,
        "running": server_reachable,
        "available": is_available,
        "reason": reason,
    }
    cache.set(_model_status_cache_key(model), status, settings.LLM_MODEL_HEALTH_CACHE_SECONDS)
    return status


def get_llm_model_statuses() -> list[dict]:
    models = get_available_llm_models()
    if not models:
        return []

    with ThreadPoolExecutor(max_workers=min(len(models), 8)) as executor:
        statuses_by_model = dict(zip(models, executor.map(get_llm_model_status, models)))
    return [statuses_by_model[model] for model in models]


def get_available_vllm_models() -> list[str]:
    return list(settings.VLLM_MODELS.keys())


# Flow 5a: used by request_vllm_chat() and _build_vllm_client() to find the model's vLLM URL.
def get_vllm_base_url(model: str) -> str:
    try:
        return settings.VLLM_MODELS[model]
    except KeyError as exc:
        available_models = ", ".join(get_available_vllm_models())
        raise UnsupportedVllmModelError(
            f"Unsupported vLLM model '{model}'. Available models: {available_models}."
        ) from exc


# Flow 5b: called by chat request functions to build Langfuse/OpenAI clients.
def _build_vllm_client(model: str) -> LangfuseOpenAI:
    return LangfuseOpenAI(
        api_key=settings.VLLM_API_KEY,
        base_url=get_vllm_base_url(model),
        timeout=settings.VLLM_TIMEOUT_SECONDS,
    )


def _build_ollama_client(model: str) -> LangfuseOpenAI:
    return LangfuseOpenAI(
        api_key=settings.OLLAMA_API_KEY,
        base_url=get_ollama_base_url(model),
        timeout=settings.OLLAMA_TIMEOUT_SECONDS,
    )


def _build_trace_context(
    *,
    provider: str,
    model: str,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> dict:
    raw_trace_metadata = {
        "provider": provider,
        "model": model,
        **(langfuse_metadata or {}),
    }
    trace_metadata = {
        key: str(value)
        for key, value in raw_trace_metadata.items()
        if value is not None
    }
    trace_context = {
        "trace_name": f"{provider}-chat-completion",
        "tags": ["gptclone", provider, model],
        "metadata": trace_metadata,
    }
    if langfuse_session_id:
        trace_context["session_id"] = langfuse_session_id
    if langfuse_user_id:
        trace_context["user_id"] = langfuse_user_id
    return trace_context


def _format_vllm_error(exc: OpenAIError, endpoint: str) -> RuntimeError:
    if isinstance(exc, APIStatusError):
        details = getattr(exc.response, "text", "") or str(exc)
        return RuntimeError(f"vLLM HTTP error {exc.status_code}: {details}")
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return RuntimeError(f"Could not reach vLLM at {endpoint}: {exc}")
    return RuntimeError(f"vLLM request failed: {exc}")


def _format_ollama_error(exc: OpenAIError, endpoint: str) -> RuntimeError:
    if isinstance(exc, APIStatusError):
        details = getattr(exc.response, "text", "") or str(exc)
        return RuntimeError(f"Ollama HTTP error {exc.status_code}: {details}")
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return RuntimeError(f"Could not reach Ollama at {endpoint}: {exc}")
    return RuntimeError(f"Ollama request failed: {exc}")


def get_ollama_base_url(model: str) -> str:
    config = get_llm_model_config(model)
    if config.get("provider") != "ollama":
        raise UnsupportedLlmModelError(f"Model '{model}' is not configured for Ollama.")

    base_url = str(config["base_url"]).rstrip("/")
    if base_url.endswith("/api"):
        return f"{base_url[:-4]}/v1"
    if base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


# Flow 6: called by chat request functions to extract text that the view saves and returns.
def _get_assistant_content(completion) -> str:
    choices = getattr(completion, "choices", None) or []
    first_choice = choices[0] if choices else None
    message = getattr(first_choice, "message", None)
    content = _normalize_text_part(getattr(message, "content", "") if message else "")
    reasoning = _get_reasoning_part(message)
    if reasoning and content:
        return f"<think>{reasoning}</think>\n\n{content}".strip()
    if reasoning:
        return f"<think>{reasoning}</think>".strip()
    return content.strip()


def _get_assistant_delta(chunk) -> str:
    choices = getattr(chunk, "choices", None) or []
    first_choice = choices[0] if choices else None
    delta = getattr(first_choice, "delta", None)
    content = _normalize_text_part(getattr(delta, "content", "") if delta else "")
    return content


def _normalize_text_part(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("content", "text", "value"):
            nested = value.get(key)
            if nested:
                return str(nested)
        return ""
    return str(value)


def _get_reasoning_part(message_or_delta) -> str:
    if not message_or_delta:
        return ""
    for attr in ("reasoning_content", "reasoning", "thinking"):
        content = _normalize_text_part(getattr(message_or_delta, attr, ""))
        if content:
            return content
    return ""


def _get_assistant_delta_parts(chunk) -> tuple[str, str]:
    choices = getattr(chunk, "choices", None) or []
    first_choice = choices[0] if choices else None
    delta = getattr(first_choice, "delta", None)
    return _get_reasoning_part(delta), _normalize_text_part(getattr(delta, "content", "") if delta else "")


def _positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _estimate_message_tokens(messages: list[dict[str, str]]) -> int:
    content_chars = sum(len(str(message.get("content", ""))) for message in messages)
    return max(1, (content_chars // 4) + (len(messages) * 8) + 8)


def _server_root_from_openai_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        return base_url[:-3]
    return base_url


def _tokenize_vllm_messages(
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
) -> tuple[int | None, int | None]:
    try:
        base_url = get_vllm_base_url(model)
        headers = (
            {"Authorization": f"Bearer {settings.VLLM_API_KEY}"}
            if settings.VLLM_API_KEY
            else {}
        )
        payload = {"model": model, "messages": messages}
        if _thinking_toggle_type(model) == VLLM_CHAT_TEMPLATE_THINKING_TOGGLE:
            payload["chat_template_kwargs"] = {"enable_thinking": thinking_enabled}
        response = requests.post(
            f"{_server_root_from_openai_base_url(base_url)}/tokenize",
            headers=headers,
            json=payload,
            timeout=settings.LLM_MODEL_HEALTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (UnsupportedVllmModelError, RequestException, ValueError):
        return None, None

    return _positive_int(payload.get("count")), _positive_int(payload.get("max_model_len"))


def _max_completion_tokens(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
) -> int:
    config = settings.LLM_MODELS.get(model, {})
    context_tokens = _positive_int(config.get("max_context_tokens"))
    prompt_tokens = None

    if provider == "vllm":
        prompt_tokens, server_context_tokens = _tokenize_vllm_messages(
            model,
            messages,
            thinking_enabled=thinking_enabled,
        )
        context_tokens = server_context_tokens or context_tokens

    if context_tokens is None:
        return settings.LLM_MAX_COMPLETION_TOKENS

    if prompt_tokens is None:
        prompt_tokens = _estimate_message_tokens(messages)

    remaining_context_tokens = max(1, context_tokens - prompt_tokens)
    return min(settings.LLM_MAX_COMPLETION_TOKENS, remaining_context_tokens)


def _chat_completion_options(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
    stream: bool = False,
) -> dict:
    options = {
        "max_tokens": _max_completion_tokens(
            provider=provider,
            model=model,
            messages=messages,
            thinking_enabled=thinking_enabled,
        ),
    }
    toggle_type = _thinking_toggle_type(model)
    if toggle_type == VLLM_CHAT_TEMPLATE_THINKING_TOGGLE:
        options["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": thinking_enabled},
        }
    elif toggle_type == OLLAMA_REASONING_EFFORT_THINKING_TOGGLE:
        options["reasoning_effort"] = "medium" if thinking_enabled else "none"
    if stream:
        options["stream_options"] = {"include_usage": True}
    return options


def _normalized_stream_deltas(stream):
    is_reasoning_open = False
    for chunk in stream:
        reasoning_delta, content_delta = _get_assistant_delta_parts(chunk)
        if reasoning_delta:
            if not is_reasoning_open:
                is_reasoning_open = True
                yield "<think>"
            yield reasoning_delta
        if content_delta:
            if is_reasoning_open:
                is_reasoning_open = False
                yield "</think>\n\n"
            yield content_delta
    if is_reasoning_open:
        yield "</think>\n\n"


# Flow 5: called by the view with prepared messages; traces, calls vLLM, then returns assistant text.
def request_vllm_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> str:
    endpoint = f"{get_vllm_base_url(model).rstrip('/')}/chat/completions"
    trace_context = _build_trace_context(
        provider="vllm",
        model=model,
        langfuse_session_id=langfuse_session_id,
        langfuse_user_id=langfuse_user_id,
        langfuse_metadata=langfuse_metadata,
    )

    try:
        with managed_vllm_model(model), propagate_attributes(**trace_context):
            completion = _build_vllm_client(model).chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
                **_chat_completion_options(
                    provider="vllm",
                    model=model,
                    messages=messages,
                    thinking_enabled=thinking_enabled,
                ),
            )
    except OpenAIError as exc:
        raise _format_vllm_error(exc, endpoint) from exc

    content = _get_assistant_content(completion)
    if not content:
        raise RuntimeError("vLLM returned an empty assistant message.")

    return content


def stream_vllm_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
):
    endpoint = f"{get_vllm_base_url(model).rstrip('/')}/chat/completions"
    trace_context = _build_trace_context(
        provider="vllm",
        model=model,
        langfuse_session_id=langfuse_session_id,
        langfuse_user_id=langfuse_user_id,
        langfuse_metadata=langfuse_metadata,
    )

    try:
        with managed_vllm_model(model), propagate_attributes(**trace_context):
            stream = _build_vllm_client(model).chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                **_chat_completion_options(
                    provider="vllm",
                    model=model,
                    messages=messages,
                    thinking_enabled=thinking_enabled,
                    stream=True,
                ),
            )
            yield from _normalized_stream_deltas(stream)
    except OpenAIError as exc:
        raise _format_vllm_error(exc, endpoint) from exc


def request_ollama_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> str:
    endpoint = f"{get_ollama_base_url(model).rstrip('/')}/chat/completions"
    trace_context = _build_trace_context(
        provider="ollama",
        model=model,
        langfuse_session_id=langfuse_session_id,
        langfuse_user_id=langfuse_user_id,
        langfuse_metadata=langfuse_metadata,
    )

    try:
        with propagate_attributes(**trace_context):
            completion = _build_ollama_client(model).chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
                **_chat_completion_options(
                    provider="ollama",
                    model=model,
                    messages=messages,
                    thinking_enabled=thinking_enabled,
                ),
            )
    except OpenAIError as exc:
        raise _format_ollama_error(exc, endpoint) from exc

    content = _get_assistant_content(completion)
    if not content:
        raise RuntimeError("Ollama returned an empty assistant message.")

    return content


def stream_ollama_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
):
    endpoint = f"{get_ollama_base_url(model).rstrip('/')}/chat/completions"
    trace_context = _build_trace_context(
        provider="ollama",
        model=model,
        langfuse_session_id=langfuse_session_id,
        langfuse_user_id=langfuse_user_id,
        langfuse_metadata=langfuse_metadata,
    )

    try:
        with propagate_attributes(**trace_context):
            stream = _build_ollama_client(model).chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                **_chat_completion_options(
                    provider="ollama",
                    model=model,
                    messages=messages,
                    thinking_enabled=thinking_enabled,
                    stream=True,
                ),
            )
            yield from _normalized_stream_deltas(stream)
    except OpenAIError as exc:
        raise _format_ollama_error(exc, endpoint) from exc


def request_llm_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> str:
    provider = get_llm_model_config(model).get("provider")
    if provider == "vllm":
        return request_vllm_chat(
            model=model,
            messages=messages,
            thinking_enabled=thinking_enabled,
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id,
            langfuse_metadata=langfuse_metadata,
        )
    if provider == "ollama":
        return request_ollama_chat(
            model=model,
            messages=messages,
            thinking_enabled=thinking_enabled,
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id,
            langfuse_metadata=langfuse_metadata,
        )
    raise RuntimeError(f"Unsupported LLM provider '{provider}' for model '{model}'.")


def stream_llm_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    thinking_enabled: bool = False,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
):
    provider = get_llm_model_config(model).get("provider")
    if provider == "vllm":
        yield from stream_vllm_chat(
            model=model,
            messages=messages,
            thinking_enabled=thinking_enabled,
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id,
            langfuse_metadata=langfuse_metadata,
        )
        return
    if provider == "ollama":
        yield from stream_ollama_chat(
            model=model,
            messages=messages,
            thinking_enabled=thinking_enabled,
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id,
            langfuse_metadata=langfuse_metadata,
        )
        return
    raise RuntimeError(f"Unsupported LLM provider '{provider}' for model '{model}'.")
