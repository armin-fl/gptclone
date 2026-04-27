from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
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


def _build_title_from_user_message(content: str) -> str:
    title = " ".join(content.split())
    if len(title) > 60:
        title = f"{title[:57]}..."
    return title or Conversation.DEFAULT_TITLE


class ConversationListCreateView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        phone_number = (request.query_params.get("phone_number") or "").strip()
        qs = Conversation.objects.select_related("user").prefetch_related(
            Prefetch("messages", queryset=Message.objects.only("content", "created_at"))
        )
        if phone_number:
            qs = qs.filter(user__phone_number=phone_number)

        serializer = ConversationListSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = serializer.save()
        output = ConversationDetailSerializer(conversation)
        return Response(output.data, status=status.HTTP_201_CREATED)


class ConversationDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, conversation_id):
        conversation = (
            Conversation.objects.select_related("user")
            .prefetch_related("messages")
            .filter(id=conversation_id)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        phone_number = (request.query_params.get("phone_number") or "").strip()
        if phone_number and conversation.user and conversation.user.phone_number != phone_number:
            return Response(
                {"detail": "Conversation not found for this phone number."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ConversationDetailSerializer(conversation)
        return Response(serializer.data)


class ConversationSendMessageView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, conversation_id):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        conversation = (
            Conversation.objects.select_related("user")
            .prefetch_related("messages")
            .filter(id=conversation_id)
            .first()
        )
        if not conversation:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

        phone_number = (serializer.validated_data.get("phone_number") or "").strip()
        if phone_number and conversation.user and conversation.user.phone_number != phone_number:
            return Response(
                {"detail": "Conversation not found for this phone number."},
                status=status.HTTP_404_NOT_FOUND,
            )

        content = serializer.validated_data["content"]
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

        with transaction.atomic():
            user_message = Message.objects.create(
                conversation=conversation,
                role=Message.Role.USER,
                content=content,
            )

            previous_messages = conversation.messages.exclude(id=user_message.id)
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

            Message.objects.create(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                content=assistant_reply,
            )

            if conversation.title == Conversation.DEFAULT_TITLE:
                conversation.title = _build_title_from_user_message(user_message.content)
            conversation.updated_at = timezone.now()
            conversation.save(update_fields=["title", "updated_at"])

        refreshed = Conversation.objects.select_related("user").prefetch_related("messages").get(id=conversation.id)
        response_data = ConversationDetailSerializer(refreshed).data
        response_data["active_model"] = model
        return Response(response_data)
