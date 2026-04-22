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
