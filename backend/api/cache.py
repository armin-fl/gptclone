from collections.abc import Callable
from typing import TypeVar

from django.conf import settings
from django.core.cache import cache

T = TypeVar("T")


def _user_conversations_version_key(user_id: int) -> str:
    return f"chat:user:{user_id}:conversations:version"


def _conversation_messages_version_key(conversation_id: str) -> str:
    return f"chat:conversation:{conversation_id}:messages:version"


def _get_version(key: str) -> int:
    version = cache.get(key)
    if version is None:
        cache.set(key, 1, None)
        return 1
    return int(version)


def _bump_version(key: str) -> None:
    cache.add(key, 1, None)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 2, None)


def get_user_conversations_version(user_id: int) -> int:
    return _get_version(_user_conversations_version_key(user_id))


def get_conversation_messages_version(conversation_id: str) -> int:
    return _get_version(_conversation_messages_version_key(conversation_id))


def bump_user_conversations(user_id: int | None) -> None:
    if user_id is not None:
        _bump_version(_user_conversations_version_key(user_id))


def bump_conversation_messages(conversation_id: str) -> None:
    _bump_version(_conversation_messages_version_key(str(conversation_id)))


def bump_conversation_cache(*, conversation_id: str, user_id: int | None) -> None:
    bump_conversation_messages(str(conversation_id))
    bump_user_conversations(user_id)


def cached_page(key: str, builder: Callable[[], T]) -> T:
    cached = cache.get(key)
    if cached is not None:
        return cached

    value = builder()
    cache.set(key, value, settings.CHAT_PAGE_CACHE_SECONDS)
    return value
