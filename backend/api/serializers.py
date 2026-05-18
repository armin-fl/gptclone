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


class ImageGenerationSerializer(serializers.Serializer):
    prompt = serializers.CharField()
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    n = serializers.IntegerField(min_value=1, max_value=4, required=False, default=1)
    size = serializers.RegexField(
        regex=r"^[0-9]{2,5}x[0-9]{2,5}$",
        required=False,
        default="1024x1024",
        error_messages={"invalid": "Size must use WIDTHxHEIGHT format, such as 1024x1024."},
    )
    negative_prompt = serializers.CharField(required=False, allow_blank=True)
    num_inference_steps = serializers.IntegerField(min_value=1, max_value=150, required=False)
    guidance_scale = serializers.FloatField(min_value=0.0, max_value=20.0, required=False)
    true_cfg_scale = serializers.FloatField(min_value=0.0, max_value=20.0, required=False)
    seed = serializers.IntegerField(min_value=0, required=False)

    def validate_prompt(self, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise serializers.ValidationError("Prompt cannot be empty.")
        return clean

    def validate_size(self, value: str) -> str:
        width, height = [int(part) for part in value.lower().split("x", 1)]
        if width < 256 or height < 256 or width > 2048 or height > 2048:
            raise serializers.ValidationError("Size dimensions must be between 256 and 2048 pixels.")
        if width % 8 != 0 or height % 8 != 0:
            raise serializers.ValidationError("Size dimensions must be divisible by 8.")
        return f"{width}x{height}"
