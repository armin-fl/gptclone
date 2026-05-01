import json

from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Conversation, Message
from .serializers import (
    ConversationCreateSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    ConversationUpdateSerializer,
    EditMessageSerializer,
    RegenerateMessageSerializer,
    SendMessageSerializer,
)
from .services import (
    UnsupportedVllmModelError,
    build_history_as_system_message,
    get_available_vllm_models,
    request_vllm_chat,
    stream_vllm_chat,
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


class ConversationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    # List flow: GET /conversations/ returns conversations and previews; separate from the send-message path.
    def get(self, request):
        qs = (
            Conversation.objects.filter(user=request.user)
            .select_related("user")
            .prefetch_related(Prefetch("messages", queryset=Message.objects.only("content", "created_at")))
        )

        serializer = ConversationListSerializer(qs, many=True)
        return Response(serializer.data)

    # Create flow: POST /conversations/ creates the conversation_id used by ConversationSendMessageView.post().
    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        conversation = serializer.save()
        output = ConversationDetailSerializer(conversation)
        return Response(output.data, status=status.HTTP_201_CREATED)


class ConversationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_conversation(self, request, conversation_id, *, include_messages: bool = True):
        qs = Conversation.objects.select_related("user").filter(id=conversation_id, user=request.user)
        if include_messages:
            qs = qs.prefetch_related("messages")
        return qs.first()

    # Detail flow: GET /conversations/<id>/ returns messages, same shape as the send-message response.
    def get(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ConversationDetailSerializer(conversation)
        return Response(serializer.data)

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

        return Response(ConversationDetailSerializer(conversation).data)

    def delete(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id, include_messages=False)
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        conversation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationSendMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def _serialize_conversation(self, conversation_id, model: str) -> dict:
        refreshed = (
            Conversation.objects.select_related("user")
            .prefetch_related("messages")
            .get(id=conversation_id)
        )
        response_data = ConversationDetailSerializer(refreshed).data
        response_data["active_model"] = model
        return response_data

    def _save_assistant_reply(self, conversation_id, assistant_reply: str, model: str) -> dict | None:
        clean_reply = assistant_reply.strip()
        if not clean_reply:
            return None

        with transaction.atomic():
            conversation = Conversation.objects.select_for_update().get(id=conversation_id)
            Message.objects.create(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                content=clean_reply,
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
            conversation.save(update_fields=["title", "updated_at"])

        return self._serialize_conversation(conversation_id, model)

    def _stream_assistant_events(
        self,
        *,
        conversation_id,
        model: str,
        llm_messages: list[dict[str, str]],
        langfuse_user_id: str | None,
        langfuse_metadata: dict,
    ):
        chunks: list[str] = []
        yield _stream_event(
            {
                "type": "conversation",
                "conversation": self._serialize_conversation(conversation_id, model),
            }
        )

        try:
            for delta in stream_vllm_chat(
                model=model,
                messages=llm_messages,
                langfuse_session_id=str(conversation_id),
                langfuse_user_id=langfuse_user_id,
                langfuse_metadata=langfuse_metadata,
            ):
                chunks.append(delta)
                yield _stream_event({"type": "delta", "delta": delta})
        except GeneratorExit:
            self._save_assistant_reply(conversation_id, "".join(chunks), model)
            raise
        except (UnsupportedVllmModelError, RuntimeError) as exc:
            self._save_assistant_reply(conversation_id, "".join(chunks), model)
            yield _stream_event(
                {
                    "type": "error",
                    "detail": "Failed to get response from vLLM.",
                    "error": str(exc),
                }
            )
            return

        response_data = self._save_assistant_reply(conversation_id, "".join(chunks), model)
        if response_data is None:
            yield _stream_event(
                {
                    "type": "error",
                    "detail": "Failed to get response from vLLM.",
                    "error": "vLLM returned an empty assistant message.",
                }
            )
            return

        yield _stream_event({"type": "done", "conversation": response_data})

    def _streaming_response(
        self,
        *,
        conversation_id,
        model: str,
        llm_messages: list[dict[str, str]],
        langfuse_user_id: str | None,
        langfuse_metadata: dict,
    ) -> StreamingHttpResponse:
        response = StreamingHttpResponse(
            self._stream_assistant_events(
                conversation_id=conversation_id,
                model=model,
                llm_messages=llm_messages,
                langfuse_user_id=langfuse_user_id,
                langfuse_metadata=langfuse_metadata,
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
            Conversation.objects.select_related("user")
            .prefetch_related("messages")
            .filter(id=conversation_id, user=request.user)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        content = serializer.validated_data["content"]
        model = serializer.validated_data.get("model") or settings.VLLM_MODEL
        # Flow 2: check the requested model through get_available_vllm_models() before saving or calling vLLM.
        if model not in get_available_vllm_models():
            return Response(
                {
                    "detail": "Unsupported vLLM model.",
                    "available_models": get_available_vllm_models(),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        system_instruction = (
            serializer.validated_data.get("system_instruction")
            or "You are a helpful AI assistant. Keep answers clear and concise unless asked otherwise."
        )
        wants_stream = serializer.validated_data.get("stream", False)

        with transaction.atomic():
            # Flow 3: store the USER message first so later history and response include this request.
            user_message = Message.objects.create(
                conversation=conversation,
                role=Message.Role.USER,
                content=content,
            )

            previous_messages = (
                Message.objects.filter(conversation=conversation)
                .exclude(id=user_message.id)
                .order_by("created_at")
            )
            # Flow 4: build_history_as_system_message() turns older messages into vLLM context.
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
            }

            if wants_stream:
                return self._streaming_response(
                    conversation_id=conversation.id,
                    model=model,
                    llm_messages=llm_messages,
                    langfuse_user_id=str(conversation.user_id) if conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )

            try:
                # Flow 5: request_vllm_chat() sends the prepared messages to vLLM and returns assistant text.
                assistant_reply = request_vllm_chat(
                    model=model,
                    messages=llm_messages,
                    langfuse_session_id=str(conversation.id),
                    langfuse_user_id=str(conversation.user_id) if conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )
            except UnsupportedVllmModelError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Unsupported vLLM model.",
                        "error": str(exc),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except RuntimeError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Failed to get response from vLLM.",
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
            conversation.save(update_fields=["title", "updated_at"])

        # Flow 8: reload and serialize the full conversation as the HTTP response back to the client.
        return Response(self._serialize_conversation(conversation.id, model))


class ConversationRegenerateMessageView(ConversationSendMessageView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id, message_id):
        serializer = RegenerateMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        conversation = (
            Conversation.objects.select_related("user")
            .filter(id=conversation_id, user=request.user)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        model = serializer.validated_data.get("model") or settings.VLLM_MODEL
        if model not in get_available_vllm_models():
            return Response(
                {
                    "detail": "Unsupported vLLM model.",
                    "available_models": get_available_vllm_models(),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        system_instruction = (
            serializer.validated_data.get("system_instruction")
            or "You are a helpful AI assistant. Keep answers clear and concise unless asked otherwise."
        )
        wants_stream = serializer.validated_data.get("stream", False)

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
            }

            target_message.delete()
            locked_conversation.updated_at = timezone.now()
            locked_conversation.save(update_fields=["updated_at"])

            if wants_stream:
                return self._streaming_response(
                    conversation_id=locked_conversation.id,
                    model=model,
                    llm_messages=llm_messages,
                    langfuse_user_id=str(locked_conversation.user_id) if locked_conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )

            try:
                assistant_reply = request_vllm_chat(
                    model=model,
                    messages=llm_messages,
                    langfuse_session_id=str(locked_conversation.id),
                    langfuse_user_id=str(locked_conversation.user_id) if locked_conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )
            except UnsupportedVllmModelError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Unsupported vLLM model.",
                        "error": str(exc),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except RuntimeError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Failed to get response from vLLM.",
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
            locked_conversation.save(update_fields=["updated_at"])

        return Response(self._serialize_conversation(conversation.id, model))


class ConversationForkMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id, message_id):
        conversation = (
            Conversation.objects.select_related("user")
            .filter(id=conversation_id, user=request.user)
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
                    )
                    for message in messages_to_copy
                ]
            )

        refreshed = (
            Conversation.objects.select_related("user")
            .prefetch_related("messages")
            .get(id=forked_conversation.id)
        )
        return Response(ConversationDetailSerializer(refreshed).data, status=status.HTTP_201_CREATED)


class ConversationEditMessageView(ConversationSendMessageView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id, message_id):
        serializer = EditMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        conversation = (
            Conversation.objects.select_related("user")
            .filter(id=conversation_id, user=request.user)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        model = serializer.validated_data.get("model") or settings.VLLM_MODEL
        if model not in get_available_vllm_models():
            return Response(
                {
                    "detail": "Unsupported vLLM model.",
                    "available_models": get_available_vllm_models(),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        system_instruction = (
            serializer.validated_data.get("system_instruction")
            or "You are a helpful AI assistant. Keep answers clear and concise unless asked otherwise."
        )
        wants_stream = serializer.validated_data.get("stream", False)
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
            locked_conversation.save(update_fields=update_fields)

            if wants_stream:
                return self._streaming_response(
                    conversation_id=locked_conversation.id,
                    model=model,
                    llm_messages=llm_messages,
                    langfuse_user_id=str(locked_conversation.user_id) if locked_conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )

            try:
                assistant_reply = request_vllm_chat(
                    model=model,
                    messages=llm_messages,
                    langfuse_session_id=str(locked_conversation.id),
                    langfuse_user_id=str(locked_conversation.user_id) if locked_conversation.user_id else None,
                    langfuse_metadata=langfuse_metadata,
                )
            except UnsupportedVllmModelError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Unsupported vLLM model.",
                        "error": str(exc),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except RuntimeError as exc:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Failed to get response from vLLM.",
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
            locked_conversation.save(update_fields=["updated_at"])

        return Response(self._serialize_conversation(conversation.id, model))
