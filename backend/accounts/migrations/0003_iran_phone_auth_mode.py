# Generated manually for auth flow hardening.

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_phoneotp"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="phone_number",
            field=models.CharField(
                db_index=True,
                max_length=20,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Phone number must be 11 English digits and start with 09.",
                        regex=r"^09[0-9]{9}$",
                    )
                ],
            ),
        ),
        migrations.AlterField(
            model_name="phoneotp",
            name="phone_number",
            field=models.CharField(
                db_index=True,
                max_length=20,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Phone number must be 11 English digits and start with 09.",
                        regex=r"^09[0-9]{9}$",
                    )
                ],
            ),
        ),
        migrations.AddField(
            model_name="phoneotp",
            name="purpose",
            field=models.CharField(
                choices=[("login", "Login"), ("register", "Register")],
                db_index=True,
                default="login",
                max_length=20,
            ),
        ),
        migrations.RemoveIndex(
            model_name="phoneotp",
            name="phone_otp_lookup_idx",
        ),
        migrations.AddIndex(
            model_name="phoneotp",
            index=models.Index(
                fields=["phone_number", "purpose", "is_used", "-created_at"],
                name="phone_otp_purpose_idx",
            ),
        ),
    ]
