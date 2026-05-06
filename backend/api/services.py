from django.conf import settings
from langfuse import propagate_attributes
from langfuse.openai import OpenAI as LangfuseOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAIError


class UnsupportedLlmModelError(RuntimeError):
    pass


class UnsupportedVllmModelError(RuntimeError):
    pass


# Flow 4: called by ConversationSendMessageView.post() to turn previous Message rows into LLM context.
def build_history_as_system_message(conversation_messages) -> str:
    lines = []
    for msg in conversation_messages:
        role = msg.role.upper()
        lines.append(f"{role}: {msg.content}")

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
    content = getattr(message, "content", "") if message else ""
    return (content or "").strip()


def _get_assistant_delta(chunk) -> str:
    choices = getattr(chunk, "choices", None) or []
    first_choice = choices[0] if choices else None
    delta = getattr(first_choice, "delta", None)
    content = getattr(delta, "content", "") if delta else ""
    if content is None:
        return ""
    return str(content)


# Flow 5: called by the view with prepared messages; traces, calls vLLM, then returns assistant text.
def request_vllm_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
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
        with propagate_attributes(**trace_context):
            completion = _build_vllm_client(model).chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
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
        with propagate_attributes(**trace_context):
            stream = _build_vllm_client(model).chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                delta = _get_assistant_delta(chunk)
                if delta:
                    yield delta
    except OpenAIError as exc:
        raise _format_vllm_error(exc, endpoint) from exc


def request_ollama_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
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
            )
            for chunk in stream:
                delta = _get_assistant_delta(chunk)
                if delta:
                    yield delta
    except OpenAIError as exc:
        raise _format_ollama_error(exc, endpoint) from exc


def request_llm_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> str:
    provider = get_llm_model_config(model).get("provider")
    if provider == "vllm":
        return request_vllm_chat(
            model=model,
            messages=messages,
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id,
            langfuse_metadata=langfuse_metadata,
        )
    if provider == "ollama":
        return request_ollama_chat(
            model=model,
            messages=messages,
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id,
            langfuse_metadata=langfuse_metadata,
        )
    raise RuntimeError(f"Unsupported LLM provider '{provider}' for model '{model}'.")


def stream_llm_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
):
    provider = get_llm_model_config(model).get("provider")
    if provider == "vllm":
        yield from stream_vllm_chat(
            model=model,
            messages=messages,
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id,
            langfuse_metadata=langfuse_metadata,
        )
        return
    if provider == "ollama":
        yield from stream_ollama_chat(
            model=model,
            messages=messages,
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id,
            langfuse_metadata=langfuse_metadata,
        )
        return
    raise RuntimeError(f"Unsupported LLM provider '{provider}' for model '{model}'.")
