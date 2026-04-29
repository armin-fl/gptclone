# Generated manually for account profile settings.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_iran_phone_auth_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="profile_image",
            field=models.FileField(blank=True, null=True, upload_to="profile-images/"),
        ),
        migrations.AlterField(
            model_name="phoneotp",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("login", "Login"),
                    ("register", "Register"),
                    ("change_phone", "Change phone"),
                ],
                db_index=True,
                default="login",
                max_length=20,
            ),
        ),
    ]
