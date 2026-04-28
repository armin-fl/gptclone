from rest_framework import serializers

from .models import User, phone_validator


class PhoneOTPRequestSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)

    def validate_phone_number(self, value: str) -> str:
        clean = value.strip()
        phone_validator(clean)
        return clean


class PhoneOTPVerifySerializer(PhoneOTPRequestSerializer):
    otp = serializers.RegexField(
        regex=r"^\d{6}$",
        error_messages={"invalid": "OTP must be a 6-digit code."},
    )


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "phone_number", "first_name", "last_name")
