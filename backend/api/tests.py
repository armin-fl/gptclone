import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .services import (
    UnsupportedVllmModelError,
    build_history_as_system_message,
    get_available_vllm_models,
    get_vllm_base_url,
    request_vllm_chat,
)


class VllmServiceTests(SimpleTestCase):
    def test_build_history_as_system_message_includes_previous_messages(self):
        history = build_history_as_system_message(
            [
                SimpleNamespace(role="user", content="Hello"),
                SimpleNamespace(role="assistant", content="Hi there"),
            ]
        )

        self.assertIn("Use the following full chat history as context.", history)
        self.assertIn("USER: Hello", history)
        self.assertIn("ASSISTANT: Hi there", history)

    def test_get_available_vllm_models_returns_hardcoded_models(self):
        self.assertEqual(get_available_vllm_models(), ["gpt-oss-20b"])

    def test_get_vllm_base_url_routes_by_model_name(self):
        self.assertEqual(
            get_vllm_base_url("gpt-oss-20b"),
            "http://127.0.0.1:8001/v1",
        )

    def test_get_vllm_base_url_rejects_unknown_model(self):
        with self.assertRaisesMessage(
            UnsupportedVllmModelError,
            "Unsupported vLLM model 'unknown-model'.",
        ):
            get_vllm_base_url("unknown-model")

    @patch("api.services.request.urlopen")
    def test_request_vllm_chat_routes_gpt_oss_20b_to_its_vllm_server(
        self,
        mock_urlopen,
    ):
        mock_urlopen.return_value = _FakeResponse(
            {"choices": [{"message": {"content": " Hello from vLLM "}}]}
        )

        content = request_vllm_chat(
            model="gpt-oss-20b",
            messages=[{"role": "user", "content": "Hello"}],
        )

        self.assertEqual(content, "Hello from vLLM")
        req = mock_urlopen.call_args.args[0]
        timeout = mock_urlopen.call_args.kwargs["timeout"]
        payload = json.loads(req.data.decode("utf-8"))

        self.assertEqual(req.full_url, "http://127.0.0.1:8001/v1/chat/completions")
        self.assertEqual(req.get_method(), "POST")
        self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual(timeout, 120)
        self.assertEqual(
            payload,
            {
                "model": "gpt-oss-20b",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
        )

    @patch("api.services.request.urlopen")
    def test_request_vllm_chat_rejects_empty_assistant_message(self, mock_urlopen):
        mock_urlopen.return_value = _FakeResponse(
            {"choices": [{"message": {"content": " "}}]}
        )

        with self.assertRaisesMessage(
            RuntimeError,
            "vLLM returned an empty assistant message.",
        ):
            request_vllm_chat(
                model="gpt-oss-20b",
                messages=[{"role": "user", "content": "Hello"}],
            )


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")
