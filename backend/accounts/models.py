from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from .managers import UserManager


phone_validator = RegexValidator(
    regex=r"^\+?[0-9]{8,15}$",
    message="Phone number must be 8-15 digits and can start with +.",
)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom user model using phone number as the login identity."""

    phone_number = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        validators=[phone_validator],
    )
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.phone_number


class PhoneOTP(models.Model):
    """One-time code for phone-number sign in."""

    phone_number = models.CharField(
        max_length=20,
        db_index=True,
        validators=[phone_validator],
    )
    code_hash = models.CharField(max_length=255)
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone_number", "is_used", "-created_at"], name="phone_otp_lookup_idx"),
        ]

    def __str__(self) -> str:
        return f"OTP for {self.phone_number}"
