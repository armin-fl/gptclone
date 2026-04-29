from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from api.models import Conversation
from .services import (
    UnsupportedVllmModelError,
    _build_vllm_client,
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

    @patch("api.services.LangfuseOpenAI")
    def test_build_vllm_client_uses_openai_compatible_base_url(self, mock_openai):
        _build_vllm_client("gpt-oss-20b")

        mock_openai.assert_called_once_with(
            api_key=settings.VLLM_API_KEY,
            base_url="http://127.0.0.1:8001/v1",
            timeout=120,
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._build_vllm_client")
    def test_request_vllm_chat_routes_gpt_oss_20b_to_its_vllm_server(
        self,
        mock_build_client,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _FakeCompletion(" Hello from vLLM ")
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
            trace_name="vllm-chat-completion",
            tags=["gptclone", "vllm", "gpt-oss-20b"],
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
        )

    @patch("api.services.propagate_attributes")
    @patch("api.services._build_vllm_client")
    def test_request_vllm_chat_rejects_empty_assistant_message(
        self,
        mock_build_client,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _FakeCompletion(" ")
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
            trace_name="vllm-chat-completion",
            tags=["gptclone", "vllm", "gpt-oss-20b"],
            metadata={
                "provider": "vllm",
                "model": "gpt-oss-20b",
            },
        )


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]


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

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data, [])
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(send_response.status_code, 404)

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
