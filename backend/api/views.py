import json
import time

from django.conf import settings
from django.db import transaction
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Conversation, Message
from .cache import bump_conversation_cache, bump_user_conversations
from .conversation_data import (
    latest_prompt_messages,
    save_conversation_summary,
    serialize_conversation_detail,
    serialize_conversation_summary,
    get_conversation_page,
)
from .pagination import CursorError
from .serializers import (
    ConversationCreateSerializer,
    ConversationUpdateSerializer,
    EditMessageSerializer,
    MessageSerializer,
    RegenerateMessageSerializer,
    SendMessageSerializer,
)
from .services import (
    UnsupportedLlmModelError,
    build_history_as_system_message,
    get_available_llm_models,
    get_llm_model_statuses,
    has_thinking_content,
    request_llm_chat,
    stream_llm_chat,
)


# Flow 7: used by ConversationSendMessageView.post() after saving the assistant reply to title a new chat.
def _build_title_from_user_message(content: str) -> str:
    title = " ".join(content.split())
    if len(title) > 60:
        title = f"{title[:57]}..."
    return title or Conversation.DEFAULT_TITLE


def _build_fork_title(title: str) -> str:
    base_title = " ".join(title.split()) or Conversation.DEFAULT_TITLE
    suffix = " (fork)"
    if len(base_title) + len(suffix) > 255:
        base_title = base_title[: 255 - len(suffix)].rstrip()
    return f"{base_title}{suffix}"


def _stream_event(event: dict) -> bytes:
    return f"{json.dumps(event)}\n".encode("utf-8")


class ThinkingDurationTracker:
    def __init__(self):
        self._started_at: float | None = None
        self.thinking_duration_ms: int | None = None

    def observe(self, content: str) -> None:
        if self.thinking_duration_ms is not None:
            return

        lower_content = content.lower()
        if self._started_at is None:
            if "<think" not in lower_content:
                return
            self._started_at = time.monotonic()

        if "</think>" in lower_content:
            self.finish()

    def finish(self) -> None:
        if self._started_at is None or self.thinking_duration_ms is not None:
            return
        self.thinking_duration_ms = max(0, round((time.monotonic() - self._started_at) * 1000))


class LlmModelListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"models": get_llm_model_statuses()})


class ConversationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    # List flow: GET /conversations/ returns conversations and previews; separate from the send-message path.
    def get(self, request):
        try:
            return Response(
                get_conversation_page(
                    request.user,
                    cursor=request.query_params.get("cursor"),
                    limit=request.query_params.get("limit"),
                    query=request.query_params.get("q"),
                )
            )
        except CursorError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    # Create flow: POST /conversations/ creates the conversation_id used by ConversationSendMessageView.post().
    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        conversation = serializer.save()
        save_conversation_summary(conversation)
        bump_user_conversations(request.user.id)
        output = serialize_conversation_detail(conversation)
        return Response(output, status=status.HTTP_201_CREATED)


class ConversationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_conversation(self, request, conversation_id, *, include_messages: bool = True):
        return Conversation.objects.filter(id=conversation_id, user=request.user).first()

    # Detail flow: GET /conversations/<id>/ returns messages, same shape as the send-message response.
    def get(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            return Response(
                serialize_conversation_detail(
                    conversation,
                    before=request.query_params.get("before"),
                    limit=request.query_params.get("limit"),
                )
            )
        except CursorError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id, include_messages=False)
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ConversationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        update_fields = []
        if "title" in serializer.validated_data:
            conversation.title = serializer.validated_data["title"]
            update_fields.append("title")
        if "is_pinned" in serializer.validated_data:
            conversation.is_pinned = serializer.validated_data["is_pinned"]
            update_fields.append("is_pinned")

        conversation.updated_at = timezone.now()
        update_fields.append("updated_at")
        conversation.save(update_fields=update_fields)
        bump_user_conversations(request.user.id)

        return Response(serialize_conversation_detail(conversation))

    def delete(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id, include_messages=False)
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        conversation.delete()
        bump_user_conversations(request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationSendMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def _serialize_conversation(self, conversation_id, model: str) -> dict:
        refreshed = Conversation.objects.get(id=conversation_id)
        response_data = serialize_conversation_detail(refreshed)
        response_data["active_model"] = model
        return response_data

    def _save_assistant_reply(
        self,
        conversation_id,
        assistant_reply: str,
        model: str,
        thinking_duration_ms: int | None = None,
    ) -> dict | None:
        clean_reply = assistant_reply.strip()
        if not clean_reply:
            return None
        if not has_thinking_content(clean_reply):
            thinking_duration_ms = None

        with transaction.atomic():
            conversation = Conversation.objects.select_for_update().get(id=conversation_id)
            assistant_message = Message.objects.create(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                content=clean_reply,
                thinking_duration_ms=thinking_duration_ms,
            )

            if conversation.title == Conversation.DEFAULT_TITLE:
                first_user_message = (
                    Message.objects.filter(conversation=conversation, role=Message.Role.USER)
                    .order_by("created_at")
                    .first()
                )
                if first_user_message:
                    conversation.title = _build_title_from_user_message(first_user_message.content)
            conversation.updated_at = timezone.now()
            save_conversation_summary(conversation, update_fields=["title", "updated_at"])

        bump_conversation_cache(conversation_id=str(conversation_id), user_id=conversation.user_id)
        return {
            "message": MessageSerializer(assistant_message).data,
            "conversation": serialize_conversation_summary(conversation),
            "active_model": model,
        }

    def _stream_assistant_events(
        self,
        *,
        conversation_id,
        model: str,
        llm_messages: list[dict[str, str]],
        langfuse_user_id: str | None,
        langfuse_metadata: dict,
        thinking_enabled: bool = False,
        initial_event: dict | None = None,
    ):
        chunks: list[str] = []
        thinking_tracker = ThinkingDurationTracker()
        if initial_event is not None:
            yield _stream_event(initial_event)

        try:
            for delta in stream_llm_chat(
                model=model,
                messages=llm_messages,
                thinking_enabled=thinking_enabled,
                langfuse_session_id=str(conversation_id),
                langfuse_user_id=langfuse_user_id,
                langfuse_metadata=langfuse_metadata,
            ):
                chunks.append(delta)
                thinking_tracker.observe("".join(chunks))
                yield _stream_event({"type": "delta", "delta": delta})
        except GeneratorExit:
            thinking_tracker.finish()
            self._save_assistant_reply(
                conversation_id,
                "".join(chunks),
                model,
                thinking_tracker.thinking_duration_ms,
            )
            raise
        except (UnsupportedLlmModelError, RuntimeError) as exc:
            thinking_tracker.finish()
            self._save_assistant_reply(
                conversation_id,
                "".join(chunks),
                model,
                thinking_tracker.thinking_duration_ms,
            )
            yield _stream_event(
                {
                    "type": "error",
                    "detail": "Failed to get response from LLM.",
                    "error": str(exc),
                }
            )
            return

        thinking_tracker.finish()
        saved_reply = self._save_assistant_reply(
            conversation_id,
            "".join(chunks),
            model,
            thinking_tracker.thinking_duration_ms,
        )
        if saved_reply is None:
            yield _stream_event(
                {
                    "type": "error",
                    "detail": "Failed to get response from LLM.",
                    "error": "LLM returned an empty assistant message.",
                }
            )
            return

        yield _stream_event({"type": "done", **saved_reply})

    def _streaming_response(
        self,
        *,
        conversation_id,
        model: str,
        llm_messages: list[dict[str, str]],
        langfuse_user_id: str | None,
        langfuse_metadata: dict,
        thinking_enabled: bool = False,
        initial_event: dict | None = None,
    ) -> StreamingHttpResponse:
        response = StreamingHttpResponse(
            self._stream_assistant_events(
                conversation_id=conversation_id,
                model=model,
                llm_messages=llm_messages,
                langfuse_user_id=langfuse_user_id,
                langfuse_metadata=langfuse_metadata,
                thinking_enabled=thinking_enabled,
                initial_event=initial_event,
            ),
            content_type="application/x-ndjson",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    # Flow 1: request entry; validates input, calls services, saves messages, and returns the conversation.
    def post(self, request, conversation_id):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        conversation = (
            Conversation.objects.filter(id=conversation_id, user=request.user)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        content = serializer.validated_data["content"]
        model = serializer.validated_data.get("model") or settings.LLM_MODEL
        # Flow 2: check the requested model before saving or calling the provider.
        if model not in get_available_llm_models():
            return Response(
                {
                    "detail": "Unsupported LLM model.",
                    "available_models": get_available_llm_models(),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        system_instruction = (
            serializer.validated_data.get("system_instruction")
            or "You are a helpful AI assistant. Keep answers clear and concise unless asked otherwise."
        )
        wants_stream = serializer.validated_data.get("stream", False)
        thinking_enabled = serializer.validated_data.get("thinking_enabled", False)

        with transaction.atomic():
            # Flow 3: store the USER message first so later history and response include this request.
            user_message = Message.objects.create(
                conversation=conversation,
                role=Message.Role.USER,
                content=content,
            )

            previous_messages = latest_prompt_messages(conversation, exclude_message_id=user_message.id)
            # Flow 4: build_history_as_system_message() turns older messages into LLM context.
            history_system_message = build_history_as_system_message(previous_messages)

            llm_messages = [
                {
                    "role": "system",
                    "content": f"{system_instruction}\n\n{history_system_message}",
                },
                {
                    "role": "user",
                    "content": user_message.content,
                },
            ]
            langfuse_metadata = {
                "conversation_id": str(conversation.id),
                "message_id": user_message.id,
                "thinking_enabled": thinking_enabled,
            }

            if wants_stream:
                if conversation.title == Conversation.DEFAULT_TITLE:
                    conversation.title = _build_title_from_user_message(user_message.content)
                conversation.updated_at = timezone.now()
                save_conversation_summary(conversation, update_fields=["title", "updated_at"])
                bump_conversation_cache(conversation_id=str(conversation.id), user_id=conversation.user_id)
                return self._streaming_response(
                    conversation_id=conversation.id,
                    model=model,
                    llm_messages=llm_messages,
                    langfuse_user_id=str(conversation.user_id) if conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                    thinking_enabled=thinking_enabled,
                    initial_event={
                        "type": "message",
                        "message": MessageSerializer(user_message).data,
                        "conversation": serialize_conversation_summary(conversation),
                    },
                )

            try:
                # Flow 5: request_llm_chat() sends the prepared messages to the provider and returns assistant text.
                assistant_reply = request_llm_chat(
                    model=model,
                    messages=llm_messages,
                    thinking_enabled=thinking_enabled,
                    langfuse_session_id=str(conversation.id),
                    langfuse_user_id=str(conversation.user_id) if conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )
            except UnsupportedLlmModelError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Unsupported LLM model.",
                        "error": str(exc),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except RuntimeError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Failed to get response from LLM.",
                        "error": str(exc),
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            # Flow 6: save the ASSISTANT message so the database matches what the user receives.
            Message.objects.create(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                content=assistant_reply,
            )

            if conversation.title == Conversation.DEFAULT_TITLE:
                conversation.title = _build_title_from_user_message(user_message.content)
            conversation.updated_at = timezone.now()
            save_conversation_summary(conversation, update_fields=["title", "updated_at"])
            bump_conversation_cache(conversation_id=str(conversation.id), user_id=conversation.user_id)

        # Flow 8: reload and serialize the full conversation as the HTTP response back to the client.
        return Response(self._serialize_conversation(conversation.id, model))


class ConversationRegenerateMessageView(ConversationSendMessageView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id, message_id):
        serializer = RegenerateMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        conversation = (
            Conversation.objects.filter(id=conversation_id, user=request.user)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        model = serializer.validated_data.get("model") or settings.LLM_MODEL
        if model not in get_available_llm_models():
            return Response(
                {
                    "detail": "Unsupported LLM model.",
                    "available_models": get_available_llm_models(),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        system_instruction = (
            serializer.validated_data.get("system_instruction")
            or "You are a helpful AI assistant. Keep answers clear and concise unless asked otherwise."
        )
        wants_stream = serializer.validated_data.get("stream", False)
        thinking_enabled = serializer.validated_data.get("thinking_enabled", False)

        with transaction.atomic():
            locked_conversation = (
                Conversation.objects.select_for_update()
                .get(id=conversation.id)
            )
            messages = list(
                Message.objects.filter(conversation=locked_conversation).order_by("created_at", "id")
            )
            target_index = next(
                (index for index, message in enumerate(messages) if message.id == message_id),
                None,
            )
            if target_index is None:
                return Response({"detail": "Message not found."}, status=status.HTTP_404_NOT_FOUND)

            target_message = messages[target_index]
            if target_message.role != Message.Role.ASSISTANT:
                return Response(
                    {"detail": "Only assistant messages can be regenerated."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if target_index != len(messages) - 1:
                return Response(
                    {
                        "detail": (
                            "Only the latest assistant message can be regenerated. "
                            "Fork the chat from an earlier message instead."
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user_index = None
            for index in range(target_index - 1, -1, -1):
                if messages[index].role == Message.Role.USER:
                    user_index = index
                    break

            if user_index is None:
                return Response(
                    {"detail": "No user message was found before this assistant response."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user_message = messages[user_index]
            previous_messages = messages[:user_index]
            history_system_message = build_history_as_system_message(previous_messages)
            llm_messages = [
                {
                    "role": "system",
                    "content": f"{system_instruction}\n\n{history_system_message}",
                },
                {
                    "role": "user",
                    "content": user_message.content,
                },
            ]
            langfuse_metadata = {
                "conversation_id": str(locked_conversation.id),
                "message_id": user_message.id,
                "regenerated_message_id": target_message.id,
                "thinking_enabled": thinking_enabled,
            }

            target_message.delete()
            locked_conversation.updated_at = timezone.now()
            save_conversation_summary(locked_conversation, update_fields=["updated_at"])
            bump_conversation_cache(
                conversation_id=str(locked_conversation.id),
                user_id=locked_conversation.user_id,
            )

            if wants_stream:
                return self._streaming_response(
                    conversation_id=locked_conversation.id,
                    model=model,
                    llm_messages=llm_messages,
                    langfuse_user_id=str(locked_conversation.user_id) if locked_conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                    thinking_enabled=thinking_enabled,
                    initial_event={
                        "type": "sync",
                        "conversation": serialize_conversation_detail(locked_conversation),
                    },
                )

            try:
                assistant_reply = request_llm_chat(
                    model=model,
                    messages=llm_messages,
                    thinking_enabled=thinking_enabled,
                    langfuse_session_id=str(locked_conversation.id),
                    langfuse_user_id=str(locked_conversation.user_id) if locked_conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )
            except UnsupportedLlmModelError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Unsupported LLM model.",
                        "error": str(exc),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except RuntimeError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Failed to get response from LLM.",
                        "error": str(exc),
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            Message.objects.create(
                conversation=locked_conversation,
                role=Message.Role.ASSISTANT,
                content=assistant_reply,
            )
            locked_conversation.updated_at = timezone.now()
            save_conversation_summary(locked_conversation, update_fields=["updated_at"])
            bump_conversation_cache(
                conversation_id=str(locked_conversation.id),
                user_id=locked_conversation.user_id,
            )

        return Response(self._serialize_conversation(conversation.id, model))


class ConversationForkMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id, message_id):
        conversation = (
            Conversation.objects.filter(id=conversation_id, user=request.user)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        messages = list(Message.objects.filter(conversation=conversation).order_by("created_at", "id"))
        target_index = next(
            (index for index, message in enumerate(messages) if message.id == message_id),
            None,
        )
        if target_index is None:
            return Response({"detail": "Message not found."}, status=status.HTTP_404_NOT_FOUND)
        if messages[target_index].role != Message.Role.ASSISTANT:
            return Response(
                {"detail": "Only assistant responses can be forked."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        messages_to_copy = messages[: target_index + 1]

        with transaction.atomic():
            forked_conversation = Conversation.objects.create(
                user=request.user,
                title=_build_fork_title(conversation.title),
            )
            Message.objects.bulk_create(
                [
                    Message(
                        conversation=forked_conversation,
                        role=message.role,
                        content=message.content,
                        thinking_duration_ms=message.thinking_duration_ms,
                    )
                    for message in messages_to_copy
                ]
            )
            save_conversation_summary(forked_conversation)

        bump_conversation_cache(conversation_id=str(forked_conversation.id), user_id=request.user.id)
        return Response(serialize_conversation_detail(forked_conversation), status=status.HTTP_201_CREATED)


class ConversationEditMessageView(ConversationSendMessageView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id, message_id):
        serializer = EditMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        conversation = (
            Conversation.objects.filter(id=conversation_id, user=request.user)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        model = serializer.validated_data.get("model") or settings.LLM_MODEL
        if model not in get_available_llm_models():
            return Response(
                {
                    "detail": "Unsupported LLM model.",
                    "available_models": get_available_llm_models(),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        system_instruction = (
            serializer.validated_data.get("system_instruction")
            or "You are a helpful AI assistant. Keep answers clear and concise unless asked otherwise."
        )
        wants_stream = serializer.validated_data.get("stream", False)
        thinking_enabled = serializer.validated_data.get("thinking_enabled", False)
        edited_content = serializer.validated_data["content"]

        with transaction.atomic():
            locked_conversation = Conversation.objects.select_for_update().get(id=conversation.id)
            messages = list(
                Message.objects.filter(conversation=locked_conversation).order_by("created_at", "id")
            )
            target_index = next(
                (index for index, message in enumerate(messages) if message.id == message_id),
                None,
            )
            if target_index is None:
                return Response({"detail": "Message not found."}, status=status.HTTP_404_NOT_FOUND)

            target_message = messages[target_index]
            if target_message.role != Message.Role.USER:
                return Response(
                    {"detail": "Only user requests can be edited."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            previous_messages = messages[:target_index]
            history_system_message = build_history_as_system_message(previous_messages)
            llm_messages = [
                {
                    "role": "system",
                    "content": f"{system_instruction}\n\n{history_system_message}",
                },
                {
                    "role": "user",
                    "content": edited_content,
                },
            ]
            langfuse_metadata = {
                "conversation_id": str(locked_conversation.id),
                "message_id": target_message.id,
                "edited_message_id": target_message.id,
                "thinking_enabled": thinking_enabled,
            }

            target_message.content = edited_content
            target_message.save(update_fields=["content"])
            delete_message_ids = [message.id for message in messages[target_index + 1 :]]
            if delete_message_ids:
                Message.objects.filter(conversation=locked_conversation, id__in=delete_message_ids).delete()

            if locked_conversation.title == Conversation.DEFAULT_TITLE:
                locked_conversation.title = _build_title_from_user_message(edited_content)
                update_fields = ["title", "updated_at"]
            else:
                update_fields = ["updated_at"]
            locked_conversation.updated_at = timezone.now()
            save_conversation_summary(locked_conversation, update_fields=update_fields)
            bump_conversation_cache(
                conversation_id=str(locked_conversation.id),
                user_id=locked_conversation.user_id,
            )

            if wants_stream:
                return self._streaming_response(
                    conversation_id=locked_conversation.id,
                    model=model,
                    llm_messages=llm_messages,
                    langfuse_user_id=str(locked_conversation.user_id) if locked_conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                    thinking_enabled=thinking_enabled,
                    initial_event={
                        "type": "sync",
                        "conversation": serialize_conversation_detail(locked_conversation),
                    },
                )

            try:
                assistant_reply = request_llm_chat(
                    model=model,
                    messages=llm_messages,
                    thinking_enabled=thinking_enabled,
                    langfuse_session_id=str(locked_conversation.id),
                    langfuse_user_id=str(locked_conversation.user_id) if locked_conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )
            except UnsupportedLlmModelError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Unsupported LLM model.",
                        "error": str(exc),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except RuntimeError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Failed to get response from LLM.",
                        "error": str(exc),
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            Message.objects.create(
                conversation=locked_conversation,
                role=Message.Role.ASSISTANT,
                content=assistant_reply,
            )
            locked_conversation.updated_at = timezone.now()
            save_conversation_summary(locked_conversation, update_fields=["updated_at"])
            bump_conversation_cache(
                conversation_id=str(locked_conversation.id),
                user_id=locked_conversation.user_id,
            )

        return Response(self._serialize_conversation(conversation.id, model))
