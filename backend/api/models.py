import uuid

from django.conf import settings
from django.db import models


class Conversation(models.Model):
    DEFAULT_TITLE = "New Chat"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    title = models.CharField(max_length=255, default=DEFAULT_TITLE)
    is_pinned = models.BooleanField(default=False)
    last_message_preview = models.CharField(max_length=123, blank=True, default="")
    message_count = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-updated_at"]
        indexes = [
            models.Index(
                fields=["user", "-is_pinned", "-updated_at", "-id"],
                name="conv_user_pin_upd_id_idx",
            ),
        ]

    def __str__(self) -> str:
        user_label = self.user.phone_number if self.user else "anonymous"
        return f"{self.title} ({user_label})"


class Message(models.Model):
    class Role(models.TextChoices):
        SYSTEM = "system", "System"
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    thinking_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["conversation", "-created_at", "-id"],
                name="msg_conv_created_id_idx",
            ),
            models.Index(
                fields=["conversation", "role", "created_at", "id"],
                name="msg_conv_role_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:40]}"
