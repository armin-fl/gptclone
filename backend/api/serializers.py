from rest_framework import serializers

from .models import Conversation, Message

THINKING_EFFORT_CHOICES = ("none", "short", "medium", "long")


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ("id", "role", "content", "thinking_duration_ms", "created_at")


class ConversationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = (
            "id",
            "title",
            "is_pinned",
            "created_at",
            "updated_at",
            "last_message_preview",
            "message_count",
            "last_message_at",
        )


class ConversationDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = (
            "id",
            "title",
            "is_pinned",
            "created_at",
            "updated_at",
            "last_message_preview",
            "message_count",
            "last_message_at",
        )


class ConversationCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def create(self, validated_data):
        title = validated_data.get("title") or Conversation.DEFAULT_TITLE
        return Conversation.objects.create(title=title, user=self.context["request"].user)


class ConversationUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False, allow_blank=False)
    is_pinned = serializers.BooleanField(required=False)

    def validate_title(self, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise serializers.ValidationError("Title cannot be empty.")
        return clean

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide title or is_pinned.")
        return attrs


class SendMessageSerializer(serializers.Serializer):
    content = serializers.CharField()
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    system_instruction = serializers.CharField(required=False, allow_blank=True)
    thinking_enabled = serializers.BooleanField(required=False, default=False)
    thinking_effort = serializers.ChoiceField(
        choices=THINKING_EFFORT_CHOICES,
        required=False,
    )
    stream = serializers.BooleanField(required=False, default=False)

    def validate_content(self, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise serializers.ValidationError("Message content cannot be empty.")
        return clean

    def validate(self, attrs):
        effort = attrs.get("thinking_effort")
        if effort is None:
            attrs["thinking_effort"] = "medium" if attrs.get("thinking_enabled", False) else "none"
        else:
            attrs["thinking_enabled"] = effort != "none"
        return attrs


class RegenerateMessageSerializer(serializers.Serializer):
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    system_instruction = serializers.CharField(required=False, allow_blank=True)
    thinking_enabled = serializers.BooleanField(required=False, default=False)
    thinking_effort = serializers.ChoiceField(
        choices=THINKING_EFFORT_CHOICES,
        required=False,
    )
    stream = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        effort = attrs.get("thinking_effort")
        if effort is None:
            attrs["thinking_effort"] = "medium" if attrs.get("thinking_enabled", False) else "none"
        else:
            attrs["thinking_enabled"] = effort != "none"
        return attrs


class EditMessageSerializer(SendMessageSerializer):
    pass
