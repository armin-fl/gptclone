import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import call, patch

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from requests import RequestException

from accounts.models import User
from api.models import Conversation, Message
from .model_runtime import (
    ACTIVE_VLLM_MODEL_KEY,
    get_vllm_container_config,
    _inflight_cache_key,
    _inflight_count,
    _switch_vllm_model,
    is_managed_vllm_model,
    managed_vllm_model,
    warmup_vllm_models,
)
from .services import (
    UnsupportedLlmModelError,
    UnsupportedVllmModelError,
    _build_ollama_client,
    _build_vllm_client,
    build_history_as_system_message,
    get_available_llm_models,
    get_ollama_base_url,
    get_available_vllm_models,
    get_llm_model_status,
    get_vllm_base_url,
    request_llm_chat,
    request_ollama_chat,
    request_vllm_chat,
    strip_thinking_blocks,
    stream_ollama_chat,
    stream_vllm_chat,
)
from .serializers import SendMessageSerializer


class VllmServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

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

    def test_build_history_as_system_message_strips_thinking_blocks(self):
        history = build_history_as_system_message(
            [
                SimpleNamespace(role="assistant", content="<think>private chain</think>\n\nVisible answer"),
                SimpleNamespace(role="assistant", content="<think>unfinished"),
            ]
        )

        self.assertIn("ASSISTANT: Visible answer", history)
        self.assertNotIn("private chain", history)
        self.assertNotIn("unfinished", history)

    def test_strip_thinking_blocks_removes_complete_and_incomplete_blocks(self):
        self.assertEqual(
            strip_thinking_blocks("Intro\n<think>hidden</think>\nFinal"),
            "Intro\n\nFinal",
        )
        self.assertEqual(strip_thinking_blocks("<think>still thinking"), "")

    def test_send_message_serializer_accepts_thinking_enabled(self):
        serializer = SendMessageSerializer(
            data={"content": "Hello", "thinking_enabled": True, "stream": True}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.validated_data["thinking_enabled"])
        self.assertEqual(serializer.validated_data["thinking_effort"], "medium")

    def test_send_message_serializer_accepts_thinking_effort(self):
        serializer = SendMessageSerializer(
            data={"content": "Hello", "thinking_effort": "long", "stream": True}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.validated_data["thinking_enabled"])
        self.assertEqual(serializer.validated_data["thinking_effort"], "long")

    @override_settings(
        VLLM_AUTO_SWITCH_ENABLED=True,
        VLLM_SLEEP_MODE_ENABLED=False,
        VLLM_MODEL_CONTAINERS={
            "model-1": {
                "service_name": "vllm-model-1",
                "container_name": "vllm-model-1-dev",
            },
        },
    )
    @patch("api.model_runtime._switch_vllm_model")
    @patch("api.model_runtime._container_running")
    def test_managed_vllm_model_allows_nested_same_model_requests(
        self,
        mock_container_running,
        mock_switch_vllm_model,
    ):
        cache.set(ACTIVE_VLLM_MODEL_KEY, "model-1", timeout=None)
        mock_container_running.return_value = True

        with managed_vllm_model("model-1"):
            self.assertEqual(_inflight_count("model-1"), 1)
            with managed_vllm_model("model-1"):
                self.assertEqual(_inflight_count("model-1"), 2)
            self.assertEqual(_inflight_count("model-1"), 1)

        self.assertEqual(_inflight_count("model-1"), 0)
        mock_switch_vllm_model.assert_not_called()

    @override_settings(
        VLLM_AUTO_SWITCH_ENABLED=True,
        VLLM_SLEEP_MODE_ENABLED=True,
        VLLM_MODEL_CONTAINERS={
            "model-1": {
                "service_name": "vllm-model-1",
                "container_name": "vllm-model-1-dev",
            },
        },
    )
    @patch("api.model_runtime._switch_vllm_model")
    @patch("api.model_runtime.is_vllm_model_sleeping")
    @patch("api.model_runtime._container_running")
    def test_managed_vllm_model_wakes_cached_active_sleeping_model(
        self,
        mock_container_running,
        mock_is_vllm_model_sleeping,
        mock_switch_vllm_model,
    ):
        cache.set(ACTIVE_VLLM_MODEL_KEY, "model-1", timeout=None)
        mock_container_running.return_value = True
        mock_is_vllm_model_sleeping.return_value = True

        with managed_vllm_model("model-1"):
            self.assertEqual(_inflight_count("model-1"), 1)

        self.assertEqual(_inflight_count("model-1"), 0)
        mock_switch_vllm_model.assert_called_once_with("model-1")

    @override_settings(
        VLLM_AUTO_SWITCH_ENABLED=True,
        VLLM_SLEEP_MODE_ENABLED=True,
        VLLM_MODEL_CONTAINERS={
            "model-1": {
                "service_name": "vllm-model-1",
                "container_name": "vllm-model-1-dev",
            },
        },
    )
    @patch("api.model_runtime._sleep_vllm_model")
    @patch("api.model_runtime.is_vllm_model_sleeping")
    @patch("api.model_runtime._container_running")
    def test_managed_vllm_model_does_not_sleep_after_same_model_request(
        self,
        mock_container_running,
        mock_is_vllm_model_sleeping,
        mock_sleep_vllm_model,
    ):
        cache.set(ACTIVE_VLLM_MODEL_KEY, "model-1", timeout=None)
        mock_container_running.return_value = True
        mock_is_vllm_model_sleeping.return_value = False

        with managed_vllm_model("model-1"):
            self.assertEqual(_inflight_count("model-1"), 1)

        self.assertEqual(_inflight_count("model-1"), 0)
        mock_sleep_vllm_model.assert_not_called()

    @override_settings(
        VLLM_AUTO_SWITCH_ENABLED=True,
        VLLM_MODEL_CONTAINERS={
            "model-1": {
                "service_name": "vllm-model-1",
                "container_name": "vllm-model-1-dev",
            },
            "model-2": {
                "service_name": "vllm-model-2",
                "container_name": "vllm-model-2-dev",
            },
        },
        VLLM_MODELS={
            "model-1": "http://127.0.0.1:8101/v1",
            "model-2": "http://127.0.0.1:8102/v1",
        },
        VLLM_INFLIGHT_DRAIN_TIMEOUT_SECONDS=5,
        VLLM_RUNTIME_POLL_SECONDS=0.01,
    )
    @patch("api.model_runtime._wake_vllm_model")
    @patch("api.model_runtime._wait_for_vllm_ready")
    @patch("api.model_runtime._start_container")
    @patch("api.model_runtime._sleep_vllm_model")
    @patch("api.model_runtime._container_running")
    @patch("api.model_runtime.time.sleep")
    def test_switch_waits_for_other_model_inflight_requests_to_finish(
        self,
        mock_sleep,
        mock_container_running,
        mock_sleep_vllm_model,
        mock_start_container,
        mock_wait_for_vllm_ready,
        mock_wake_vllm_model,
    ):
        cache.set(ACTIVE_VLLM_MODEL_KEY, "model-1", timeout=None)
        cache.set(_inflight_cache_key("model-1"), 1, timeout=None)
        mock_container_running.return_value = True
        mock_sleep.side_effect = lambda _seconds: cache.set(_inflight_cache_key("model-1"), 0)

        with managed_vllm_model("model-2"):
            self.assertEqual(cache.get(ACTIVE_VLLM_MODEL_KEY), "model-2")
            self.assertEqual(_inflight_count("model-2"), 1)

        self.assertEqual(_inflight_count("model-2"), 0)
        mock_sleep.assert_called()
        mock_sleep_vllm_model.assert_called_once_with("model-1", level=1)
        mock_wake_vllm_model.assert_called_once_with("model-2")
        mock_start_container.assert_not_called()
        mock_wait_for_vllm_ready.assert_called_once_with("model-2")

    @override_settings(
        VLLM_AUTO_SWITCH_ENABLED=True,
        VLLM_SLEEP_MODE_ENABLED=True,
        VLLM_MODEL_CONTAINERS={
            "model-1": {
                "service_name": "vllm-model-1",
                "container_name": "vllm-model-1-dev",
            },
            "model-2": {
                "service_name": "vllm-model-2",
                "container_name": "vllm-model-2-dev",
            },
            "model-3": {
                "service_name": "vllm-model-3",
                "container_name": "vllm-model-3-dev",
            },
            "model-4": {
                "service_name": "vllm-model-4",
                "container_name": "vllm-model-4-dev",
            },
        },
        VLLM_MODELS={
            "model-1": "http://127.0.0.1:8101/v1",
            "model-2": "http://127.0.0.1:8102/v1",
            "model-3": "http://127.0.0.1:8103/v1",
            "model-4": "http://127.0.0.1:8104/v1",
        },
    )
    @patch("api.model_runtime._wake_vllm_model")
    @patch("api.model_runtime._wait_for_vllm_ready")
    @patch("api.model_runtime._sleep_vllm_model")
    @patch("api.model_runtime._container_running")
    def test_switch_sleeps_only_the_cached_active_model(
        self,
        mock_container_running,
        mock_sleep_vllm_model,
        mock_wait_for_vllm_ready,
        mock_wake_vllm_model,
    ):
        cache.set(ACTIVE_VLLM_MODEL_KEY, "model-1", timeout=None)
        mock_container_running.return_value = True

        _switch_vllm_model("model-4")

        mock_sleep_vllm_model.assert_called_once_with("model-1", level=1)
        mock_wake_vllm_model.assert_called_once_with("model-4")
        mock_wait_for_vllm_ready.assert_called_once_with("model-4")

    @override_settings(
        VLLM_AUTO_SWITCH_ENABLED=True,
        VLLM_SLEEP_MODE_ENABLED=True,
        VLLM_MODEL_CONTAINERS={
            "model-1": {
                "service_name": "vllm-model-1",
                "container_name": "vllm-model-1-dev",
            },
            "model-2": {
                "service_name": "vllm-model-2",
                "container_name": "vllm-model-2-dev",
            },
        },
        VLLM_MODELS={
            "model-1": "http://127.0.0.1:8101/v1",
            "model-2": "http://127.0.0.1:8102/v1",
        },
    )
    @patch("api.model_runtime._sleep_vllm_model")
    @patch("api.model_runtime._switch_vllm_model")
    def test_warmup_vllm_models_switches_each_model_then_sleeps_it(
        self,
        mock_switch_vllm_model,
        mock_sleep_vllm_model,
    ):
        cache.set(ACTIVE_VLLM_MODEL_KEY, "model-2", timeout=None)

        warmed_models = warmup_vllm_models(["model-1", "model-2"])

        self.assertEqual(warmed_models, ["model-1", "model-2"])
        mock_switch_vllm_model.assert_has_calls([call("model-1"), call("model-2")])
        mock_sleep_vllm_model.assert_has_calls(
            [call("model-1", level=1), call("model-2", level=1)]
        )
        self.assertIsNone(cache.get(ACTIVE_VLLM_MODEL_KEY))

    def test_get_available_vllm_models_returns_hardcoded_models(self):
        self.assertEqual(
            get_available_vllm_models(),
            [
                "gpt-oss-20b",
                "qwen3-32b-awq",
                "qwq-32b-awq",
                "deepseek-r1-distill-qwen-32b-awq",
            ],
        )

    def test_get_available_llm_models_returns_gateway_models(self):
        self.assertEqual(
            get_available_llm_models(),
            [
                "gpt-oss-20b",
                "qwen3-32b-awq",
                "qwq-32b-awq",
                "deepseek-r1-distill-qwen-32b-awq",
                "qwen3:14b",
            ],
        )

    def test_get_vllm_base_url_routes_by_model_name(self):
        self.assertEqual(
            get_vllm_base_url("gpt-oss-20b"),
            "http://127.0.0.1:8001/v1",
        )
        self.assertEqual(
            get_vllm_base_url("qwen3-32b-awq"),
            "http://127.0.0.1:8002/v1",
        )
        self.assertEqual(
            get_vllm_base_url("qwq-32b-awq"),
            "http://127.0.0.1:8003/v1",
        )
        self.assertEqual(
            get_vllm_base_url("deepseek-r1-distill-qwen-32b-awq"),
            "http://127.0.0.1:8004/v1",
        )

    def test_deepseek_context_and_container_config_are_registered(self):
        model = "deepseek-r1-distill-qwen-32b-awq"
        config = get_vllm_container_config(model)

        self.assertEqual(settings.VLLM_MODEL_CONTEXT_TOKENS[model], 32768)
        self.assertTrue(is_managed_vllm_model(model))
        self.assertIsNotNone(config)
        self.assertEqual(config.service_name, "vllm-deepseek-r1-distill-qwen-32b-awq")
        self.assertEqual(
            config.container_name,
            "vllm-deepseek-r1-distill-qwen-32b-awq-dev",
        )

    def test_get_ollama_base_url_routes_by_model_name(self):
        self.assertEqual(
            get_ollama_base_url("qwen3:14b"),
            "http://127.0.0.1:11434/v1",
        )

    @override_settings(
        LLM_MODELS={
            "qwen3:14b": {
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434/api",
            },
        }
    )
    def test_get_ollama_base_url_normalizes_native_api_url(self):
        self.assertEqual(
            get_ollama_base_url("qwen3:14b"),
            "http://127.0.0.1:11434/v1",
        )

    def test_get_vllm_base_url_rejects_unknown_model(self):
        with self.assertRaisesMessage(
            UnsupportedVllmModelError,
            "Unsupported vLLM model 'unknown-model'.",
        ):
            get_vllm_base_url("unknown-model")

    def test_get_ollama_base_url_rejects_non_ollama_model(self):
        with self.assertRaisesMessage(
            UnsupportedLlmModelError,
            "Model 'gpt-oss-20b' is not configured for Ollama.",
        ):
            get_ollama_base_url("gpt-oss-20b")

    @patch("api.services.requests.get")
    def test_get_llm_model_status_marks_served_model_available(self, mock_get):
        mock_get.return_value = _FakeModelListResponse(["gpt-oss-20b"])

        status = get_llm_model_status("gpt-oss-20b")

        self.assertEqual(status["id"], "gpt-oss-20b")
        self.assertTrue(status["available"])
        self.assertEqual(status["provider"], "vllm")
        self.assertFalse(status["supports_thinking_toggle"])
        mock_get.assert_called_once_with(
            "http://127.0.0.1:8001/v1/models",
            headers={"Authorization": f"Bearer {settings.VLLM_API_KEY}"},
            timeout=settings.LLM_MODEL_HEALTH_TIMEOUT_SECONDS,
        )

    @override_settings(VLLM_AUTO_SWITCH_ENABLED=True, VLLM_SLEEP_MODE_ENABLED=True)
    @patch("api.services.is_vllm_model_sleeping")
    @patch("api.services.requests.get")
    def test_get_llm_model_status_marks_sleeping_model_available(
        self,
        mock_get,
        mock_is_vllm_model_sleeping,
    ):
        mock_get.return_value = _FakeModelListResponse(["gpt-oss-20b"])
        mock_is_vllm_model_sleeping.return_value = True

        status = get_llm_model_status("gpt-oss-20b")

        self.assertTrue(status["available"])
        self.assertTrue(status["running"])
        self.assertTrue(status["sleeping"])
        self.assertEqual(
            status["reason"],
            "Model server is sleeping. It will wake automatically on first request.",
        )

    @patch("api.services.requests.get")
    def test_get_llm_model_status_grays_out_stopped_model(self, mock_get):
        mock_get.side_effect = RequestException("Connection refused")

        status = get_llm_model_status("gpt-oss-20b")

        self.assertFalse(status["available"])

    @patch("api.services.LangfuseOpenAI")
    def test_build_vllm_client_uses_openai_compatible_base_url(self, mock_openai):
        _build_vllm_client("gpt-oss-20b")

        mock_openai.assert_called_once_with(
            api_key=settings.VLLM_API_KEY,
            base_url="http://127.0.0.1:8001/v1",
            timeout=120,
        )

    @patch("api.services.LangfuseOpenAI")
    def test_build_ollama_client_uses_openai_compatible_base_url(self, mock_openai):
        _build_ollama_client("qwen3:14b")

        mock_openai.assert_called_once_with(
            api_key=settings.OLLAMA_API_KEY,
            base_url="http://127.0.0.1:11434/v1",
            timeout=settings.OLLAMA_TIMEOUT_SECONDS,
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._tokenize_vllm_messages")
    @patch("api.services._build_vllm_client")
    def test_request_vllm_chat_routes_gpt_oss_20b_to_its_vllm_server(
        self,
        mock_build_client,
        mock_tokenize_vllm_messages,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _FakeCompletion(" Hello from vLLM ")
        mock_tokenize_vllm_messages.return_value = (9, 32768)
        mock_propagate_attributes.return_value = nullcontext()

        content = request_vllm_chat(
            model="gpt-oss-20b",
            messages=[{"role": "user", "content": "Hello"}],
            langfuse_session_id="conversation-1",
            langfuse_user_id="user-1",
            langfuse_metadata={"message_id": 10},
        )

        self.assertEqual(content, "Hello from vLLM")
        mock_build_client.assert_called_once_with("gpt-oss-20b")
        mock_propagate_attributes.assert_called_once_with(
            trace_name="vllm-gpt-oss-20b",
            tags=["vllm", "gpt-oss-20b"],
            metadata={
                "provider": "vllm",
                "model": "gpt-oss-20b",
                "message_id": "10",
            },
            session_id="conversation-1",
            user_id="user-1",
        )
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-oss-20b",
            messages=[{"role": "user", "content": "Hello"}],
            stream=False,
            max_tokens=32759,
        )

    @patch("api.services.request_ollama_chat")
    def test_request_llm_chat_routes_qwen_to_ollama(self, mock_request_ollama_chat):
        mock_request_ollama_chat.return_value = "Hello from Qwen"

        content = request_llm_chat(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "Hello"}],
            langfuse_session_id="conversation-1",
            langfuse_user_id="user-1",
            langfuse_metadata={"message_id": 10},
        )

        self.assertEqual(content, "Hello from Qwen")
        mock_request_ollama_chat.assert_called_once_with(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "Hello"}],
            thinking_enabled=False,
            langfuse_session_id="conversation-1",
            langfuse_user_id="user-1",
            langfuse_metadata={"message_id": 10},
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._build_ollama_client")
    def test_request_ollama_chat_calls_openai_compatible_chat_api(
        self,
        mock_build_client,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _FakeCompletion(" Hello from Ollama ")
        mock_propagate_attributes.return_value = nullcontext()

        content = request_ollama_chat(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "Hello"}],
            langfuse_session_id="conversation-1",
            langfuse_user_id="user-1",
            langfuse_metadata={"message_id": 10},
        )

        self.assertEqual(content, "Hello from Ollama")
        mock_build_client.assert_called_once_with("qwen3:14b")
        mock_propagate_attributes.assert_called_once_with(
            trace_name="ollama-qwen3:14b",
            tags=["ollama", "qwen3:14b"],
            metadata={
                "provider": "ollama",
                "model": "qwen3:14b",
                "message_id": "10",
            },
            session_id="conversation-1",
            user_id="user-1",
        )
        mock_client.chat.completions.create.assert_called_once_with(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "Hello"}],
            stream=False,
            max_tokens=32751,
            reasoning_effort="none",
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._tokenize_vllm_messages")
    @patch("api.services._build_vllm_client")
    def test_request_vllm_chat_passes_qwen_thinking_toggle(
        self,
        mock_build_client,
        mock_tokenize_vllm_messages,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _FakeCompletion(" Hello ")
        mock_tokenize_vllm_messages.return_value = (10, 32768)
        mock_propagate_attributes.return_value = nullcontext()

        content = request_vllm_chat(
            model="qwen3-32b-awq",
            messages=[{"role": "user", "content": "Hello"}],
            thinking_enabled=True,
        )

        self.assertEqual(content, "Hello")
        mock_client.chat.completions.create.assert_called_once_with(
            model="qwen3-32b-awq",
            messages=[{"role": "user", "content": "Hello"}],
            stream=False,
            max_tokens=32758,
            extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._build_ollama_client")
    def test_request_ollama_chat_passes_thinking_effort_when_enabled(
        self,
        mock_build_client,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _FakeCompletion(" Hello ")
        mock_propagate_attributes.return_value = nullcontext()

        content = request_ollama_chat(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "Hello"}],
            thinking_enabled=True,
            thinking_effort="long",
        )

        self.assertEqual(content, "Hello")
        mock_client.chat.completions.create.assert_called_once_with(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "Hello"}],
            stream=False,
            max_tokens=32751,
            reasoning_effort="long",
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._tokenize_vllm_messages")
    @patch("api.services._build_vllm_client")
    def test_request_vllm_chat_rejects_empty_assistant_message(
        self,
        mock_build_client,
        mock_tokenize_vllm_messages,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _FakeCompletion(" ")
        mock_tokenize_vllm_messages.return_value = (9, 32768)
        mock_propagate_attributes.return_value = nullcontext()

        with self.assertRaisesMessage(
            RuntimeError,
            "vLLM returned an empty assistant message.",
        ):
            request_vllm_chat(
                model="gpt-oss-20b",
                messages=[{"role": "user", "content": "Hello"}],
            )

        mock_propagate_attributes.assert_called_once_with(
            trace_name="vllm-gpt-oss-20b",
            tags=["vllm", "gpt-oss-20b"],
            metadata={
                "provider": "vllm",
                "model": "gpt-oss-20b",
            },
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._tokenize_vllm_messages")
    @patch("api.services._build_vllm_client")
    def test_stream_vllm_chat_yields_assistant_deltas(
        self,
        mock_build_client,
        mock_tokenize_vllm_messages,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = [
            _FakeStreamChunk("Hello"),
            _FakeStreamChunk(None),
            _FakeStreamChunk(" from vLLM"),
        ]
        mock_tokenize_vllm_messages.return_value = (9, 32768)
        mock_propagate_attributes.return_value = nullcontext()

        chunks = list(
            stream_vllm_chat(
                model="gpt-oss-20b",
                messages=[{"role": "user", "content": "Hello"}],
            )
        )

        self.assertEqual(chunks, ["Hello", " from vLLM"])
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-oss-20b",
            messages=[{"role": "user", "content": "Hello"}],
            stream=True,
            max_tokens=32759,
            stream_options={"include_usage": True},
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._build_ollama_client")
    def test_stream_ollama_chat_yields_assistant_deltas(
        self,
        mock_build_client,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = [
            _FakeStreamChunk("Hello"),
            _FakeStreamChunk(None),
            _FakeStreamChunk(" from Ollama"),
        ]
        mock_propagate_attributes.return_value = nullcontext()

        chunks = list(
            stream_ollama_chat(
                model="qwen3:14b",
                messages=[{"role": "user", "content": "Hello"}],
            )
        )

        self.assertEqual(chunks, ["Hello", " from Ollama"])
        mock_client.chat.completions.create.assert_called_once_with(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "Hello"}],
            stream=True,
            max_tokens=32751,
            reasoning_effort="none",
            stream_options={"include_usage": True},
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._tokenize_vllm_messages")
    @patch("api.services._build_vllm_client")
    def test_stream_vllm_chat_normalizes_reasoning_deltas(
        self,
        mock_build_client,
        mock_tokenize_vllm_messages,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = [
            _FakeStreamChunk(reasoning_content="Plan"),
            _FakeStreamChunk("Answer"),
        ]
        mock_tokenize_vllm_messages.return_value = (9, 32768)
        mock_propagate_attributes.return_value = nullcontext()

        chunks = list(
            stream_vllm_chat(
                model="qwen3-32b-awq",
                messages=[{"role": "user", "content": "Hello"}],
                thinking_enabled=False,
            )
        )

        self.assertEqual(chunks, ["<think>", "Plan", "</think>\n\n", "Answer"])
        mock_client.chat.completions.create.assert_called_once_with(
            model="qwen3-32b-awq",
            messages=[{"role": "user", "content": "Hello"}],
            stream=True,
            max_tokens=32759,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            stream_options={"include_usage": True},
        )


class _FakeCompletion:
    def __init__(self, content, reasoning_content=None):
        message = SimpleNamespace(content=content)
        if reasoning_content is not None:
            message.reasoning_content = reasoning_content
        self.choices = [
            SimpleNamespace(
                message=message,
            )
        ]


class _FakeStreamChunk:
    def __init__(self, content=None, reasoning_content=None):
        delta = SimpleNamespace(content=content)
        if reasoning_content is not None:
            delta.reasoning_content = reasoning_content
        self.choices = [
            SimpleNamespace(
                delta=delta,
            )
        ]


class _FakeModelListResponse:
    def __init__(self, model_ids):
        self.model_ids = model_ids

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "object": "list",
            "data": [{"id": model_id, "object": "model"} for model_id in self.model_ids],
        }


class ConversationAuthorizationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone_number="09100000001")
        self.other_user = User.objects.create_user(phone_number="09100000002")

    def authenticate(self, user):
        access_token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

    def test_conversation_endpoints_require_authentication(self):
        response = self.client.get("/api/conversations/")

        self.assertEqual(response.status_code, 401)

    @patch("api.views.get_llm_model_statuses")
    def test_model_list_endpoint_returns_model_statuses(self, mock_get_statuses):
        mock_get_statuses.return_value = [
            {
                "id": "gpt-oss-20b",
                "label": "gpt-oss-20b",
                "provider": "vllm",
                "available": False,
                "reason": "Connection refused",
            }
        ]

        self.authenticate(self.user)
        response = self.client.get("/api/models/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["models"][0]["id"], "gpt-oss-20b")
        self.assertFalse(response.data["models"][0]["available"])

    def test_conversations_are_scoped_to_authenticated_user(self):
        self.authenticate(self.user)
        create_response = self.client.post("/api/conversations/", {}, format="json")

        self.assertEqual(create_response.status_code, 201)
        conversation_id = create_response.data["id"]
        conversation = Conversation.objects.get(id=conversation_id)
        self.assertEqual(conversation.user, self.user)

        self.authenticate(self.other_user)
        list_response = self.client.get("/api/conversations/")
        detail_response = self.client.get(f"/api/conversations/{conversation_id}/")
        send_response = self.client.post(
            f"/api/conversations/{conversation_id}/messages/",
            {"content": "Hello"},
            format="json",
        )
        regenerate_response = self.client.post(
            f"/api/conversations/{conversation_id}/messages/1/regenerate/",
            {"stream": True},
            format="json",
        )
        fork_response = self.client.post(
            f"/api/conversations/{conversation_id}/messages/1/fork/",
            {},
            format="json",
        )
        edit_response = self.client.post(
            f"/api/conversations/{conversation_id}/messages/1/edit/",
            {"content": "Edited", "stream": True},
            format="json",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data, {"results": [], "next_cursor": None})
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(send_response.status_code, 404)
        self.assertEqual(regenerate_response.status_code, 404)
        self.assertEqual(fork_response.status_code, 404)
        self.assertEqual(edit_response.status_code, 404)

    def test_conversation_can_be_renamed_pinned_and_deleted_by_owner(self):
        self.authenticate(self.user)
        create_response = self.client.post("/api/conversations/", {"title": "Original"}, format="json")
        conversation_id = create_response.data["id"]

        update_response = self.client.patch(
            f"/api/conversations/{conversation_id}/",
            {"title": "Renamed chat", "is_pinned": True},
            format="json",
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data["title"], "Renamed chat")
        self.assertTrue(update_response.data["is_pinned"])
        conversation = Conversation.objects.get(id=conversation_id)
        self.assertEqual(conversation.title, "Renamed chat")
        self.assertTrue(conversation.is_pinned)

        delete_response = self.client.delete(f"/api/conversations/{conversation_id}/")

        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Conversation.objects.filter(id=conversation_id).exists())

    def test_other_user_cannot_rename_or_delete_conversation(self):
        conversation = Conversation.objects.create(user=self.user, title="Private")
        self.authenticate(self.other_user)

        update_response = self.client.patch(
            f"/api/conversations/{conversation.id}/",
            {"title": "Stolen"},
            format="json",
        )
        delete_response = self.client.delete(f"/api/conversations/{conversation.id}/")

        self.assertEqual(update_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        conversation.refresh_from_db()
        self.assertEqual(conversation.title, "Private")

    def test_conversation_list_uses_cursor_pages_without_loading_messages(self):
        self.authenticate(self.user)
        for index in range(3):
            conversation = Conversation.objects.create(user=self.user, title=f"Chat {index}")
            for message_index in range(2):
                Message.objects.create(
                    conversation=conversation,
                    role=Message.Role.USER,
                    content=f"Message {index}-{message_index}",
                )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/conversations/?limit=2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertIsNotNone(response.data["next_cursor"])
        self.assertFalse(
            any('FROM "api_message"' in query["sql"] for query in queries),
            [query["sql"] for query in queries],
        )

        next_response = self.client.get(f"/api/conversations/?limit=2&cursor={response.data['next_cursor']}")
        self.assertEqual(next_response.status_code, 200)
        self.assertEqual(len(next_response.data["results"]), 1)
        self.assertIsNone(next_response.data["next_cursor"])

    def test_conversation_list_cache_invalidates_after_rename(self):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user, title="Original")

        first_response = self.client.get("/api/conversations/")
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.data["results"][0]["title"], "Original")

        update_response = self.client.patch(
            f"/api/conversations/{conversation.id}/",
            {"title": "Renamed"},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)

        second_response = self.client.get("/api/conversations/")
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.data["results"][0]["title"], "Renamed")

    def test_conversation_detail_uses_message_cursor_pages(self):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user, title="Paged")
        for index in range(3):
            Message.objects.create(
                conversation=conversation,
                role=Message.Role.USER,
                content=f"Message {index}",
            )

        first_response = self.client.get(f"/api/conversations/{conversation.id}/?limit=2")
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(
            [message["content"] for message in first_response.data["messages"]],
            ["Message 1", "Message 2"],
        )
        self.assertIsNotNone(first_response.data["next_before"])

        next_response = self.client.get(
            f"/api/conversations/{conversation.id}/?limit=2&before={first_response.data['next_before']}"
        )
        self.assertEqual(next_response.status_code, 200)
        self.assertEqual(
            [message["content"] for message in next_response.data["messages"]],
            ["Message 0"],
        )

    @patch("api.views.stream_llm_chat")
    def test_streaming_message_returns_deltas_and_persists_assistant(self, mock_stream_llm_chat):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user)
        mock_stream_llm_chat.return_value = ["Hello", " stream"]

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/",
            {"content": "Hi", "stream": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/x-ndjson")

        body = b"".join(response.streaming_content).decode("utf-8")
        events = [json.loads(line) for line in body.splitlines()]

        self.assertEqual([event["type"] for event in events], ["message", "delta", "delta", "done"])
        self.assertEqual(events[0]["message"]["content"], "Hi")
        self.assertEqual(events[1]["delta"], "Hello")
        self.assertEqual(events[2]["delta"], " stream")
        self.assertEqual(events[3]["message"]["content"], "Hello stream")
        self.assertEqual(events[3]["conversation"]["last_message_preview"], "Hello stream")
        self.assertTrue(
            Message.objects.filter(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                content="Hello stream",
            ).exists()
        )

    @patch("api.views.time.monotonic")
    @patch("api.views.stream_llm_chat")
    def test_streaming_message_saves_thinking_duration_and_strips_preview(
        self,
        mock_stream_llm_chat,
        mock_monotonic,
    ):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user)
        mock_stream_llm_chat.return_value = [
            "<think>",
            "private plan",
            "</think>\n\n",
            "Final answer",
        ]
        mock_monotonic.side_effect = [10.0, 18.4]

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/",
            {"content": "Hi", "stream": True, "thinking_enabled": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode("utf-8")
        events = [json.loads(line) for line in body.splitlines()]

        done = events[-1]
        self.assertEqual(done["message"]["content"], "<think>private plan</think>\n\nFinal answer")
        self.assertEqual(done["message"]["thinking_duration_ms"], 8400)
        self.assertEqual(done["conversation"]["last_message_preview"], "Final answer")
        stored_message = Message.objects.get(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
        )
        self.assertEqual(stored_message.thinking_duration_ms, 8400)
        mock_stream_llm_chat.assert_called_once()
        self.assertTrue(mock_stream_llm_chat.call_args.kwargs["thinking_enabled"])

    @patch("api.views.stream_llm_chat")
    def test_regenerate_latest_assistant_replaces_only_that_response(self, mock_stream_llm_chat):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user)
        first_user = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Explain this",
        )
        latest_assistant = Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="Old answer",
        )
        mock_stream_llm_chat.return_value = ["New", " answer"]

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/{latest_assistant.id}/regenerate/",
            {"stream": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode("utf-8")
        events = [json.loads(line) for line in body.splitlines()]

        self.assertEqual([event["type"] for event in events], ["sync", "delta", "delta", "done"])
        self.assertEqual([message["content"] for message in events[0]["conversation"]["messages"]], ["Explain this"])
        self.assertEqual(events[3]["message"]["content"], "New answer")

        stored_messages = list(Message.objects.filter(conversation=conversation).order_by("created_at", "id"))
        self.assertEqual([message.content for message in stored_messages], ["Explain this", "New answer"])
        self.assertEqual(stored_messages[0].id, first_user.id)

    @patch("api.views.stream_llm_chat")
    def test_regenerate_rejects_non_latest_assistant_without_deleting_messages(self, mock_stream_llm_chat):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user)
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Explain this",
        )
        old_assistant = Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="Old answer",
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Follow-up",
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="Latest answer",
        )

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/{old_assistant.id}/regenerate/",
            {"stream": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Only the latest assistant message", response.data["detail"])
        mock_stream_llm_chat.assert_not_called()

        stored_messages = list(Message.objects.filter(conversation=conversation).order_by("created_at", "id"))
        self.assertEqual(
            [message.content for message in stored_messages],
            ["Explain this", "Old answer", "Follow-up", "Latest answer"],
        )

    def test_fork_message_creates_new_conversation_through_target_message(self):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user, title="Original chat")
        first_user = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Start",
        )
        target_assistant = Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="Branch point",
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Do not copy",
        )

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/{target_assistant.id}/fork/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.data["id"], str(conversation.id))
        self.assertEqual(response.data["title"], "Original chat (fork)")
        self.assertEqual(
            [message["content"] for message in response.data["messages"]],
            ["Start", "Branch point"],
        )

        self.assertEqual(Message.objects.filter(conversation=conversation).count(), 3)
        forked_conversation = Conversation.objects.get(id=response.data["id"])
        forked_messages = list(Message.objects.filter(conversation=forked_conversation).order_by("created_at", "id"))
        self.assertEqual([message.content for message in forked_messages], ["Start", "Branch point"])
        self.assertNotEqual(forked_messages[0].id, first_user.id)

    def test_fork_rejects_user_request(self):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user)
        user_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Start",
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="Answer",
        )

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/{user_message.id}/fork/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Only assistant responses can be forked.")
        self.assertEqual(Conversation.objects.count(), 1)

    @patch("api.views.stream_llm_chat")
    def test_edit_user_request_replaces_tail_and_streams_new_response(self, mock_stream_llm_chat):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user, title="Original chat")
        edited_user = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Old question",
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="Old answer",
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Later question",
        )
        mock_stream_llm_chat.return_value = ["New", " answer"]

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/{edited_user.id}/edit/",
            {"content": "Edited question", "stream": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode("utf-8")
        events = [json.loads(line) for line in body.splitlines()]

        self.assertEqual([event["type"] for event in events], ["sync", "delta", "delta", "done"])
        self.assertEqual([message["content"] for message in events[0]["conversation"]["messages"]], ["Edited question"])
        self.assertEqual(events[3]["message"]["content"], "New answer")

        stored_messages = list(Message.objects.filter(conversation=conversation).order_by("created_at", "id"))
        self.assertEqual([message.content for message in stored_messages], ["Edited question", "New answer"])

    @patch("api.views.stream_llm_chat")
    def test_edit_rejects_assistant_response_without_deleting_messages(self, mock_stream_llm_chat):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user)
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="Question",
        )
        assistant_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="Answer",
        )

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/{assistant_message.id}/edit/",
            {"content": "Edited", "stream": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Only user requests can be edited.")
        mock_stream_llm_chat.assert_not_called()
        stored_messages = list(Message.objects.filter(conversation=conversation).order_by("created_at", "id"))
        self.assertEqual([message.content for message in stored_messages], ["Question", "Answer"])
