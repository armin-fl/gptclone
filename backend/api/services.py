from django.conf import settings
from langfuse import propagate_attributes
from langfuse.openai import OpenAI as LangfuseOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAIError


class UnsupportedVllmModelError(RuntimeError):
    pass


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


def get_available_vllm_models() -> list[str]:
    return list(settings.VLLM_MODELS.keys())


def get_vllm_base_url(model: str) -> str:
    try:
        return settings.VLLM_MODELS[model]
    except KeyError as exc:
        available_models = ", ".join(get_available_vllm_models())
        raise UnsupportedVllmModelError(
            f"Unsupported vLLM model '{model}'. Available models: {available_models}."
        ) from exc


def _build_vllm_client(model: str) -> LangfuseOpenAI:
    return LangfuseOpenAI(
        api_key=settings.VLLM_API_KEY,
        base_url=get_vllm_base_url(model),
        timeout=settings.VLLM_TIMEOUT_SECONDS,
    )


def _get_assistant_content(completion) -> str:
    choices = getattr(completion, "choices", None) or []
    first_choice = choices[0] if choices else None
    message = getattr(first_choice, "message", None)
    content = getattr(message, "content", "") if message else ""
    return (content or "").strip()


def request_vllm_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> str:
    endpoint = f"{get_vllm_base_url(model).rstrip('/')}/chat/completions"
    raw_trace_metadata = {
        "provider": "vllm",
        "model": model,
        **(langfuse_metadata or {}),
    }
    trace_metadata = {
        key: str(value)
        for key, value in raw_trace_metadata.items()
        if value is not None
    }
    trace_context = {
        "trace_name": "vllm-chat-completion",
        "tags": ["gptclone", "vllm", model],
        "metadata": trace_metadata,
    }
    if langfuse_session_id:
        trace_context["session_id"] = langfuse_session_id
    if langfuse_user_id:
        trace_context["user_id"] = langfuse_user_id

    try:
        with propagate_attributes(**trace_context):
            completion = _build_vllm_client(model).chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
            )
    except APIStatusError as exc:
        details = getattr(exc.response, "text", "") or str(exc)
        raise RuntimeError(f"vLLM HTTP error {exc.status_code}: {details}") from exc
    except (APIConnectionError, APITimeoutError) as exc:
        raise RuntimeError(f"Could not reach vLLM at {endpoint}: {exc}") from exc
    except OpenAIError as exc:
        raise RuntimeError(f"vLLM request failed: {exc}") from exc

    content = _get_assistant_content(completion)
    if not content:
        raise RuntimeError("vLLM returned an empty assistant message.")

    return content
