import base64
import json
from datetime import datetime
from uuid import UUID

from django.db.models import Q
from django.utils.dateparse import parse_datetime


class CursorError(ValueError):
    pass


def clamp_limit(value: str | None, *, default: int, maximum: int = 100) -> int:
    if value is None:
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise CursorError("Limit must be a positive integer.") from exc
    if limit < 1:
        raise CursorError("Limit must be a positive integer.")
    return min(limit, maximum)


def encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> dict | None:
    if not value:
        return None
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CursorError("Invalid cursor.") from exc
    if not isinstance(payload, dict):
        raise CursorError("Invalid cursor.")
    return payload


def parse_datetime_cursor(value: object) -> datetime:
    if not isinstance(value, str):
        raise CursorError("Invalid cursor.")
    parsed = parse_datetime(value)
    if parsed is None:
        raise CursorError("Invalid cursor.")
    return parsed


def parse_uuid_cursor(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise CursorError("Invalid cursor.") from exc


def parse_int_cursor(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CursorError("Invalid cursor.") from exc


def conversation_cursor_filter(cursor: dict | None) -> Q:
    if cursor is None:
        return Q()

    is_pinned = cursor.get("pinned")
    if not isinstance(is_pinned, bool):
        raise CursorError("Invalid cursor.")
    updated_at = parse_datetime_cursor(cursor.get("updated_at"))
    conversation_id = parse_uuid_cursor(cursor.get("id"))

    return (
        Q(is_pinned__lt=is_pinned)
        | Q(is_pinned=is_pinned, updated_at__lt=updated_at)
        | Q(is_pinned=is_pinned, updated_at=updated_at, id__lt=conversation_id)
    )


def message_before_filter(cursor: dict | None) -> Q:
    if cursor is None:
        return Q()

    created_at = parse_datetime_cursor(cursor.get("created_at"))
    message_id = parse_int_cursor(cursor.get("id"))
    return Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=message_id)
