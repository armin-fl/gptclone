from collections.abc import Iterable

from django.conf import settings
from django.db.models import Q

from .cache import (
    cached_page,
    get_conversation_messages_version,
    get_user_conversations_version,
)
from .models import Conversation, Message
from .pagination import (
    CursorError,
    clamp_limit,
    conversation_cursor_filter,
    decode_cursor,
    encode_cursor,
    message_before_filter,
)
from .serializers import ConversationDetailSerializer, ConversationListSerializer, MessageSerializer


def build_message_preview(content: str) -> str:
    return f"{content[:120]}..." if len(content) > 120 else content


def sync_conversation_summary(conversation: Conversation) -> Conversation:
    latest_message = (
        Message.objects.filter(conversation=conversation)
        .order_by("-created_at", "-id")
        .only("content", "created_at")
        .first()
    )
    conversation.message_count = Message.objects.filter(conversation=conversation).count()
    if latest_message:
        conversation.last_message_preview = build_message_preview(latest_message.content)
        conversation.last_message_at = latest_message.created_at
    else:
        conversation.last_message_preview = ""
        conversation.last_message_at = None
    return conversation


def save_conversation_summary(conversation: Conversation, update_fields: Iterable[str] = ()) -> Conversation:
    sync_conversation_summary(conversation)
    fields = {
        "last_message_preview",
        "message_count",
        "last_message_at",
        *update_fields,
    }
    conversation.save(update_fields=list(fields))
    return conversation


def latest_prompt_messages(conversation: Conversation, *, exclude_message_id: int | None = None) -> list[Message]:
    qs = Message.objects.filter(conversation=conversation).only("id", "role", "content", "created_at")
    if exclude_message_id is not None:
        qs = qs.exclude(id=exclude_message_id)
    messages = list(qs.order_by("-created_at", "-id")[: settings.CHAT_PROMPT_HISTORY_MESSAGE_LIMIT])
    return list(reversed(messages))


def serialize_conversation_summary(conversation: Conversation) -> dict:
    return ConversationListSerializer(conversation).data


def serialize_conversation_detail(conversation: Conversation, *, before: str | None = None, limit: int | None = None) -> dict:
    page = get_message_page(conversation, before=before, limit=limit)
    data = ConversationDetailSerializer(conversation).data
    data["messages"] = page["results"]
    data["next_before"] = page["next_before"]
    return data


def get_conversation_page(user, *, cursor: str | None = None, limit: str | None = None, query: str | None = None) -> dict:
    page_limit = clamp_limit(limit, default=settings.CHAT_CONVERSATION_PAGE_SIZE)
    clean_query = (query or "").strip()
    decoded_cursor = decode_cursor(cursor)
    version = get_user_conversations_version(user.id)
    cache_key = f"chat:user:{user.id}:conversations:v{version}:limit:{page_limit}:cursor:{cursor or ''}:q:{clean_query}"

    def build() -> dict:
        qs = Conversation.objects.filter(user=user)
        if clean_query:
            qs = qs.filter(Q(title__icontains=clean_query) | Q(last_message_preview__icontains=clean_query))
        qs = qs.filter(conversation_cursor_filter(decoded_cursor)).order_by("-is_pinned", "-updated_at", "-id")
        conversations = list(qs[: page_limit + 1])
        has_more = len(conversations) > page_limit
        page_items = conversations[:page_limit]
        next_cursor = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = encode_cursor(
                {
                    "pinned": last.is_pinned,
                    "updated_at": last.updated_at.isoformat(),
                    "id": str(last.id),
                }
            )
        return {
            "results": ConversationListSerializer(page_items, many=True).data,
            "next_cursor": next_cursor,
        }

    return cached_page(cache_key, build)


def get_message_page(conversation: Conversation, *, before: str | None = None, limit: int | str | None = None) -> dict:
    if isinstance(limit, int):
        page_limit = min(max(limit, 1), 100)
    else:
        page_limit = clamp_limit(limit, default=settings.CHAT_MESSAGE_PAGE_SIZE)
    decoded_before = decode_cursor(before)
    version = get_conversation_messages_version(str(conversation.id))
    cache_key = f"chat:conversation:{conversation.id}:messages:v{version}:limit:{page_limit}:before:{before or ''}"

    def build() -> dict:
        qs = (
            Message.objects.filter(conversation=conversation)
            .filter(message_before_filter(decoded_before))
            .order_by("-created_at", "-id")
        )
        newest_first = list(qs[: page_limit + 1])
        has_more = len(newest_first) > page_limit
        page_items = list(reversed(newest_first[:page_limit]))
        next_before = None
        if has_more and page_items:
            oldest = page_items[0]
            next_before = encode_cursor(
                {
                    "created_at": oldest.created_at.isoformat(),
                    "id": oldest.id,
                }
            )
        return {
            "results": MessageSerializer(page_items, many=True).data,
            "next_before": next_before,
        }

    return cached_page(cache_key, build)
