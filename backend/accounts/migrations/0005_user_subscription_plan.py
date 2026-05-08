# Generated manually for subscription plans.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_user_profile_image_phone_change_purpose"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="subscription_plan",
            field=models.CharField(
                choices=[
                    ("free", "Free"),
                    ("pro", "Pro"),
                    ("ultra_pro", "Ultra Pro"),
                ],
                db_index=True,
                default="free",
                max_length=20,
            ),
        ),
    ]
