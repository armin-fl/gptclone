import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from api.models import Conversation, Message
from .services import (
    UnsupportedVllmModelError,
    _build_vllm_client,
    build_history_as_system_message,
    get_available_vllm_models,
    get_vllm_base_url,
    request_vllm_chat,
    stream_vllm_chat,
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

    @patch("api.services.propagate_attributes")
    @patch("api.services._build_vllm_client")
    def test_stream_vllm_chat_yields_assistant_deltas(
        self,
        mock_build_client,
        mock_propagate_attributes,
    ):
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = [
            _FakeStreamChunk("Hello"),
            _FakeStreamChunk(None),
            _FakeStreamChunk(" from vLLM"),
        ]
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
        )


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]


class _FakeStreamChunk:
    def __init__(self, content):
        self.choices = [
            SimpleNamespace(
                delta=SimpleNamespace(content=content),
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
        self.assertEqual(list_response.data, [])
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

    @patch("api.views.stream_vllm_chat")
    def test_streaming_message_returns_deltas_and_persists_assistant(self, mock_stream_vllm_chat):
        self.authenticate(self.user)
        conversation = Conversation.objects.create(user=self.user)
        mock_stream_vllm_chat.return_value = ["Hello", " stream"]

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/",
            {"content": "Hi", "stream": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/x-ndjson")

        body = b"".join(response.streaming_content).decode("utf-8")
        events = [json.loads(line) for line in body.splitlines()]

        self.assertEqual([event["type"] for event in events], ["conversation", "delta", "delta", "done"])
        self.assertEqual(events[1]["delta"], "Hello")
        self.assertEqual(events[2]["delta"], " stream")
        self.assertEqual(events[3]["conversation"]["messages"][-1]["content"], "Hello stream")
        self.assertTrue(
            Message.objects.filter(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                content="Hello stream",
            ).exists()
        )

    @patch("api.views.stream_vllm_chat")
    def test_regenerate_latest_assistant_replaces_only_that_response(self, mock_stream_vllm_chat):
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
        mock_stream_vllm_chat.return_value = ["New", " answer"]

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/{latest_assistant.id}/regenerate/",
            {"stream": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode("utf-8")
        events = [json.loads(line) for line in body.splitlines()]

        self.assertEqual([event["type"] for event in events], ["conversation", "delta", "delta", "done"])
        self.assertEqual([message["content"] for message in events[0]["conversation"]["messages"]], ["Explain this"])
        self.assertEqual(events[3]["conversation"]["messages"][-1]["content"], "New answer")

        stored_messages = list(Message.objects.filter(conversation=conversation).order_by("created_at", "id"))
        self.assertEqual([message.content for message in stored_messages], ["Explain this", "New answer"])
        self.assertEqual(stored_messages[0].id, first_user.id)

    @patch("api.views.stream_vllm_chat")
    def test_regenerate_rejects_non_latest_assistant_without_deleting_messages(self, mock_stream_vllm_chat):
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
        mock_stream_vllm_chat.assert_not_called()

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

    @patch("api.views.stream_vllm_chat")
    def test_edit_user_request_replaces_tail_and_streams_new_response(self, mock_stream_vllm_chat):
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
        mock_stream_vllm_chat.return_value = ["New", " answer"]

        response = self.client.post(
            f"/api/conversations/{conversation.id}/messages/{edited_user.id}/edit/",
            {"content": "Edited question", "stream": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode("utf-8")
        events = [json.loads(line) for line in body.splitlines()]

        self.assertEqual([event["type"] for event in events], ["conversation", "delta", "delta", "done"])
        self.assertEqual([message["content"] for message in events[0]["conversation"]["messages"]], ["Edited question"])
        self.assertEqual(events[3]["conversation"]["messages"][-1]["content"], "New answer")

        stored_messages = list(Message.objects.filter(conversation=conversation).order_by("created_at", "id"))
        self.assertEqual([message.content for message in stored_messages], ["Edited question", "New answer"])

    @patch("api.views.stream_vllm_chat")
    def test_edit_rejects_assistant_response_without_deleting_messages(self, mock_stream_vllm_chat):
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
        mock_stream_vllm_chat.assert_not_called()
        stored_messages = list(Message.objects.filter(conversation=conversation).order_by("created_at", "id"))
        self.assertEqual([message.content for message in stored_messages], ["Question", "Answer"])
