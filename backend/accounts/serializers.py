from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import PhoneOTP, User
from .phone_numbers import normalize_digits, normalize_phone_number


class PhoneOTPRequestSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)
    auth_mode = serializers.ChoiceField(
        choices=(PhoneOTP.Purpose.LOGIN, PhoneOTP.Purpose.REGISTER),
        error_messages={"invalid_choice": "Auth mode must be login or register."},
    )

    def validate_phone_number(self, value: str) -> str:
        try:
            return normalize_phone_number(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError("Phone number must be 11 English digits and start with 09.") from exc


class PhoneOTPVerifySerializer(PhoneOTPRequestSerializer):
    otp = serializers.CharField(max_length=16)

    def validate_otp(self, value: str) -> str:
        clean = normalize_digits(value).strip()
        if not clean.isascii() or not clean.isdigit() or len(clean) != 6:
            raise serializers.ValidationError("OTP must be a 6-digit code.")
        return clean


class UserSerializer(serializers.ModelSerializer):
    profile_image_url = serializers.SerializerMethodField()
    subscription_plan_label = serializers.CharField(source="get_subscription_plan_display", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "phone_number",
            "first_name",
            "last_name",
            "profile_image_url",
            "subscription_plan",
            "subscription_plan_label",
        )

    def get_profile_image_url(self, obj: User) -> str:
        if not obj.profile_image:
            return ""
        request = self.context.get("request")
        url = obj.profile_image.url
        return request.build_absolute_uri(url) if request else url


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "profile_image", "subscription_plan")
        extra_kwargs = {
            "first_name": {"required": False, "allow_blank": True},
            "last_name": {"required": False, "allow_blank": True},
            "profile_image": {"required": False, "allow_null": True},
            "subscription_plan": {"required": False},
        }


class PhoneChangeRequestSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)

    def validate_phone_number(self, value: str) -> str:
        try:
            clean = normalize_phone_number(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError("Phone number must be 11 English digits and start with 09.") from exc

        if User.objects.filter(phone_number=clean).exists():
            raise serializers.ValidationError("This phone number is already in use.")
        return clean


class PhoneChangeVerifySerializer(PhoneChangeRequestSerializer):
    otp = serializers.CharField(max_length=16)

    def validate_otp(self, value: str) -> str:
        clean = normalize_digits(value).strip()
        if not clean.isascii() or not clean.isdigit() or len(clean) != 6:
            raise serializers.ValidationError("OTP must be a 6-digit code.")
        return clean
