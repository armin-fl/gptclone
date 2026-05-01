from rest_framework import serializers

from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ("id", "role", "content", "created_at")


class ConversationListSerializer(serializers.ModelSerializer):
    user_phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    last_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = (
            "id",
            "title",
            "user_phone_number",
            "is_pinned",
            "created_at",
            "updated_at",
            "last_message_preview",
        )

    def get_last_message_preview(self, obj: Conversation) -> str:
        messages = list(obj.messages.all())
        last_message = messages[-1] if messages else None
        if not last_message:
            return ""
        return (last_message.content[:120] + "...") if len(last_message.content) > 120 else last_message.content


class ConversationDetailSerializer(serializers.ModelSerializer):
    user_phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = (
            "id",
            "title",
            "user_phone_number",
            "is_pinned",
            "created_at",
            "updated_at",
            "messages",
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
    stream = serializers.BooleanField(required=False, default=False)

    def validate_content(self, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise serializers.ValidationError("Message content cannot be empty.")
        return clean


class RegenerateMessageSerializer(serializers.Serializer):
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    system_instruction = serializers.CharField(required=False, allow_blank=True)
    stream = serializers.BooleanField(required=False, default=False)


class EditMessageSerializer(SendMessageSerializer):
    pass
