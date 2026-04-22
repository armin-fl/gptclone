import json
from urllib import error, request

from django.conf import settings


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


def request_vllm_chat(*, model: str, messages: list[dict[str, str]]) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
        }
    ).encode("utf-8")

    endpoint = f"{get_vllm_base_url(model).rstrip('/')}/chat/completions"
    req = request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with request.urlopen(req, timeout=settings.VLLM_TIMEOUT_SECONDS) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"vLLM HTTP error {exc.code}: {details}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach vLLM at {endpoint}: {exc.reason}") from exc

    choices = parsed.get("choices") or []
    first_choice = choices[0] if choices else {}
    content = ((first_choice.get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("vLLM returned an empty assistant message.")

    return content
