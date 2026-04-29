import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator


IRAN_MOBILE_PHONE_PATTERN = r"^09[0-9]{9}$"
PHONE_NUMBER_ERROR = "Phone number must be 11 English digits and start with 09."

phone_validator = RegexValidator(
    regex=IRAN_MOBILE_PHONE_PATTERN,
    message=PHONE_NUMBER_ERROR,
)

def normalize_digits(value: str) -> str:
    normalized = []
    for char in str(value or ""):
        try:
            normalized.append(str(unicodedata.digit(char)))
        except (TypeError, ValueError):
            normalized.append(char)
    return "".join(normalized)


def normalize_phone_number(value: str) -> str:
    clean = re.sub(r"\D", "", normalize_digits(value))
    try:
        phone_validator(clean)
    except ValidationError as exc:
        raise ValidationError(PHONE_NUMBER_ERROR) from exc
    return clean
