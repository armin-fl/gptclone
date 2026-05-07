import re

from django.db import migrations, models


THINKING_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
INCOMPLETE_THINKING_BLOCK_RE = re.compile(r"<think\b[^>]*>.*$", re.IGNORECASE | re.DOTALL)


def _preview(content: str) -> str:
    stripped = INCOMPLETE_THINKING_BLOCK_RE.sub("", THINKING_BLOCK_RE.sub("", content)).strip()
    return f"{stripped[:120]}..." if len(stripped) > 120 else stripped


def strip_existing_conversation_previews(apps, schema_editor):
    Conversation = apps.get_model("api", "Conversation")
    Message = apps.get_model("api", "Message")

    for conversation in Conversation.objects.all().iterator():
        latest_message = (
            Message.objects.filter(conversation=conversation)
            .order_by("-created_at", "-id")
            .first()
        )
        conversation.last_message_preview = _preview(latest_message.content) if latest_message else ""
        conversation.save(update_fields=["last_message_preview"])


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0003_conversation_summary_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="thinking_duration_ms",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(strip_existing_conversation_previews, migrations.RunPython.noop),
    ]
