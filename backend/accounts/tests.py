from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import UntypedToken

from .models import PhoneOTP, User


@override_settings(
    DEBUG=True,
    OTP_CODE_TTL_SECONDS=300,
    OTP_MAX_ATTEMPTS=5,
)
class PhoneOTPAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_request_and_verify_otp_returns_jwt(self):
        request_response = self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "+15551234567"},
            format="json",
        )

        self.assertEqual(request_response.status_code, 201)
        code = request_response.data["otp_code"]
        self.assertEqual(PhoneOTP.objects.filter(phone_number="+15551234567").count(), 1)

        verify_response = self.client.post(
            "/api/auth/verify-otp/",
            {"phone_number": "+15551234567", "otp": code},
            format="json",
        )

        self.assertEqual(verify_response.status_code, 200)
        access_token = verify_response.data["access"]
        refresh_token = verify_response.data["refresh"]
        payload = UntypedToken(access_token)
        user = User.objects.get(phone_number="+15551234567")
        self.assertEqual(payload["token_type"], "access")
        self.assertEqual(payload["user_id"], str(user.id))
        self.assertEqual(verify_response.data["user"]["phone_number"], "+15551234567")

        refresh_response = self.client.post(
            "/api/auth/refresh/",
            {"refresh": refresh_token},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, 200)
        self.assertIn("access", refresh_response.data)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        me_response = self.client.get("/api/auth/me/")

        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.data["phone_number"], "+15551234567")

    def test_verify_rejects_wrong_otp(self):
        self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "+15559876543"},
            format="json",
        )

        response = self.client.post(
            "/api/auth/verify-otp/",
            {"phone_number": "+15559876543", "otp": "000000"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Invalid or expired OTP code.")
