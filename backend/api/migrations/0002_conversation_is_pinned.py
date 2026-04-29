# Generated manually for sidebar conversation actions.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="is_pinned",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name="conversation",
            options={"ordering": ["-is_pinned", "-updated_at"]},
        ),
    ]
