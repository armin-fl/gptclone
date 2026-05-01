from django.db import migrations, models


def _preview(content: str) -> str:
    return f"{content[:120]}..." if len(content) > 120 else content


def populate_conversation_summaries(apps, schema_editor):
    Conversation = apps.get_model("api", "Conversation")
    Message = apps.get_model("api", "Message")

    for conversation in Conversation.objects.all().iterator():
        latest_message = (
            Message.objects.filter(conversation=conversation)
            .order_by("-created_at", "-id")
            .first()
        )
        conversation.message_count = Message.objects.filter(conversation=conversation).count()
        if latest_message:
            conversation.last_message_preview = _preview(latest_message.content)
            conversation.last_message_at = latest_message.created_at
        else:
            conversation.last_message_preview = ""
            conversation.last_message_at = None
        conversation.save(
            update_fields=[
                "last_message_preview",
                "message_count",
                "last_message_at",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0002_conversation_is_pinned"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="last_message_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conversation",
            name="last_message_preview",
            field=models.CharField(blank=True, default="", max_length=123),
        ),
        migrations.AddField(
            model_name="conversation",
            name="message_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name="message",
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.RunPython(populate_conversation_summaries, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(
                fields=["user", "-is_pinned", "-updated_at", "-id"],
                name="conv_user_pin_upd_id_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["conversation", "-created_at", "-id"],
                name="msg_conv_created_id_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["conversation", "role", "created_at", "id"],
                name="msg_conv_role_created_idx",
            ),
        ),
    ]
