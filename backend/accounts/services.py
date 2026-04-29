import secrets
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import PhoneOTP, User
from .phone_numbers import normalize_phone_number as normalize_iran_phone_number


class OTPError(ValueError):
    pass


@dataclass(frozen=True)
class CreatedOTP:
    otp: PhoneOTP
    code: str


def normalize_phone_number(phone_number: str) -> str:
    try:
        return normalize_iran_phone_number(phone_number)
    except ValidationError as exc:
        raise OTPError("Enter a valid phone number.") from exc


def create_phone_otp(phone_number: str, purpose: str) -> CreatedOTP:
    clean = normalize_phone_number(phone_number)
    if purpose not in PhoneOTP.Purpose.values:
        raise OTPError("Invalid auth mode.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = timezone.now() + timezone.timedelta(seconds=int(settings.OTP_CODE_TTL_SECONDS))

    with transaction.atomic():
        PhoneOTP.objects.filter(phone_number=clean, purpose=purpose, is_used=False).update(is_used=True)
        otp = PhoneOTP.objects.create(
            phone_number=clean,
            purpose=purpose,
            code_hash=make_password(code),
            expires_at=expires_at,
        )

    return CreatedOTP(otp=otp, code=code)


def verify_phone_otp_code(phone_number: str, code: str, purpose: str) -> str:
    clean = normalize_phone_number(phone_number)
    if purpose not in PhoneOTP.Purpose.values:
        raise OTPError("Invalid auth mode.")

    clean_code = str(code or "").strip()
    if not clean_code:
        raise OTPError("OTP code is required.")

    now = timezone.now()
    max_attempts = int(settings.OTP_MAX_ATTEMPTS)

    with transaction.atomic():
        otp = (
            PhoneOTP.objects.select_for_update()
            .filter(phone_number=clean, purpose=purpose, is_used=False)
            .order_by("-created_at")
            .first()
        )
        if not otp:
            raise OTPError("Invalid or expired OTP code.")

        if otp.expires_at <= now or otp.attempts >= max_attempts:
            otp.is_used = True
            otp.save(update_fields=["is_used"])
            raise OTPError("Invalid or expired OTP code.")

        if not check_password(clean_code, otp.code_hash):
            otp.attempts += 1
            if otp.attempts >= max_attempts:
                otp.is_used = True
                otp.save(update_fields=["attempts", "is_used"])
            else:
                otp.save(update_fields=["attempts"])
            raise OTPError("Invalid or expired OTP code.")

        otp.is_used = True
        otp.verified_at = now
        otp.save(update_fields=["is_used", "verified_at"])

    return clean


def verify_phone_otp(phone_number: str, code: str, purpose: str) -> User:
    clean = verify_phone_otp_code(phone_number, code, purpose)

    if purpose == PhoneOTP.Purpose.REGISTER:
        try:
            user = User.objects.create_user(phone_number=clean)
        except IntegrityError as exc:
            raise OTPError("Account already exists. Please log in.") from exc
    elif purpose == PhoneOTP.Purpose.LOGIN:
        try:
            user = User.objects.get(phone_number=clean)
        except User.DoesNotExist as exc:
            raise OTPError("Account not found. Please register first.") from exc
    else:
        raise OTPError("Invalid auth mode.")

    if not user.is_active:
        raise OTPError("User is inactive.")
    return user
