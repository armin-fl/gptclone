# Generated manually for dev bootstrap.

from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PhoneOTP",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "phone_number",
                    models.CharField(
                        db_index=True,
                        max_length=20,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Phone number must be 8-15 digits and can start with +.",
                                regex=r"^\+?[0-9]{8,15}$",
                            )
                        ],
                    ),
                ),
                ("code_hash", models.CharField(max_length=255)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("is_used", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["phone_number", "is_used", "-created_at"],
                        name="phone_otp_lookup_idx",
                    )
                ],
            },
        ),
    ]
