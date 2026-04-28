from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
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
    SendMessageSerializer,
)
from .services import (
    UnsupportedVllmModelError,
    build_history_as_system_message,
    get_available_vllm_models,
    request_vllm_chat,
)


# Flow 7: used by ConversationSendMessageView.post() after saving the assistant reply to title a new chat.
def _build_title_from_user_message(content: str) -> str:
    title = " ".join(content.split())
    if len(title) > 60:
        title = f"{title[:57]}..."
    return title or Conversation.DEFAULT_TITLE


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

    # Detail flow: GET /conversations/<id>/ returns messages, same shape as the send-message response.
    def get(self, request, conversation_id):
        conversation = (
            Conversation.objects.select_related("user")
            .prefetch_related("messages")
            .filter(id=conversation_id, user=request.user)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ConversationDetailSerializer(conversation)
        return Response(serializer.data)


class ConversationSendMessageView(APIView):
    permission_classes = [IsAuthenticated]

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

        with transaction.atomic():
            # Flow 3: store the USER message first so later history and response include this request.
            user_message = Message.objects.create(
                conversation=conversation,
                role=Message.Role.USER,
                content=content,
            )

            previous_messages = conversation.messages.exclude(id=user_message.id)
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

            try:
                # Flow 5: request_vllm_chat() sends the prepared messages to vLLM and returns assistant text.
                assistant_reply = request_vllm_chat(
                    model=model,
                    messages=llm_messages,
                    langfuse_session_id=str(conversation.id),
                    langfuse_user_id=str(conversation.user_id) if conversation.user_id else None,
                    langfuse_metadata={
                        "conversation_id": str(conversation.id),
                        "message_id": user_message.id,
                    },
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
        refreshed = Conversation.objects.select_related("user").prefetch_related("messages").get(id=conversation.id)
        response_data = ConversationDetailSerializer(refreshed).data
        response_data["active_model"] = model
        return Response(response_data)
