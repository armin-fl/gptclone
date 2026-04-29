from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken

from .models import PhoneOTP, User


@override_settings(
    DEBUG=True,
    OTP_CODE_TTL_SECONDS=300,
    OTP_MAX_ATTEMPTS=5,
)
class PhoneOTPAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_register_request_and_verify_otp_creates_user_and_returns_jwt(self):
        request_response = self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "09123456789", "auth_mode": "register"},
            format="json",
        )

        self.assertEqual(request_response.status_code, 201)
        code = request_response.data["otp_code"]
        self.assertEqual(
            PhoneOTP.objects.filter(phone_number="09123456789", purpose=PhoneOTP.Purpose.REGISTER).count(),
            1,
        )

        verify_response = self.client.post(
            "/api/auth/verify-otp/",
            {"phone_number": "09123456789", "otp": code, "auth_mode": "register"},
            format="json",
        )

        self.assertEqual(verify_response.status_code, 200)
        access_token = verify_response.data["access"]
        refresh_token = verify_response.data["refresh"]
        payload = UntypedToken(access_token)
        user = User.objects.get(phone_number="09123456789")
        self.assertEqual(payload["token_type"], "access")
        self.assertEqual(payload["user_id"], str(user.id))
        self.assertEqual(verify_response.data["user"]["phone_number"], "09123456789")

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
        self.assertEqual(me_response.data["phone_number"], "09123456789")

    def test_login_requires_existing_user(self):
        response = self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "09123456780", "auth_mode": "login"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Account not found. Please register first.")
        self.assertFalse(User.objects.filter(phone_number="09123456780").exists())
        self.assertFalse(PhoneOTP.objects.filter(phone_number="09123456780").exists())

    def test_existing_user_can_login_without_registering_again(self):
        User.objects.create_user(phone_number="09123456780")

        request_response = self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "09123456780", "auth_mode": "login"},
            format="json",
        )

        self.assertEqual(request_response.status_code, 201)
        code = request_response.data["otp_code"]

        verify_response = self.client.post(
            "/api/auth/verify-otp/",
            {"phone_number": "09123456780", "otp": code, "auth_mode": "login"},
            format="json",
        )

        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(User.objects.filter(phone_number="09123456780").count(), 1)
        self.assertEqual(verify_response.data["user"]["phone_number"], "09123456780")

    def test_register_rejects_existing_user(self):
        User.objects.create_user(phone_number="09123456780")

        response = self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "09123456780", "auth_mode": "register"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Account already exists. Please log in.")

    def test_phone_number_digits_are_normalized_to_english(self):
        request_response = self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "۰۹۱۲۳۴۵۶۷۸۹", "auth_mode": "register"},
            format="json",
        )

        self.assertEqual(request_response.status_code, 201)
        self.assertTrue(
            PhoneOTP.objects.filter(phone_number="09123456789", purpose=PhoneOTP.Purpose.REGISTER).exists()
        )

    def test_verify_rejects_wrong_otp(self):
        self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "09123456780", "auth_mode": "register"},
            format="json",
        )

        response = self.client.post(
            "/api/auth/verify-otp/",
            {"phone_number": "09123456780", "otp": "000000", "auth_mode": "register"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Invalid or expired OTP code.")

    def test_phone_number_must_start_with_09(self):
        response = self.client.post(
            "/api/auth/request-otp/",
            {"phone_number": "08123456789", "auth_mode": "register"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone_number", response.data)

    def test_user_phone_number_is_unique(self):
        self.assertTrue(User._meta.get_field("phone_number").unique)

    def test_profile_update_saves_name_fields(self):
        user = User.objects.create_user(phone_number="09100000001")
        self.authenticate(user)

        response = self.client.patch(
            "/api/auth/me/",
            {"first_name": "Ada", "last_name": "Lovelace"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")
        self.assertEqual(response.data["first_name"], "Ada")
        self.assertEqual(response.data["last_name"], "Lovelace")

    def test_authenticated_user_can_change_phone_after_otp_verification(self):
        user = User.objects.create_user(phone_number="09100000001")
        self.authenticate(user)

        request_response = self.client.post(
            "/api/auth/change-phone/request-otp/",
            {"phone_number": "09100000002"},
            format="json",
        )

        self.assertEqual(request_response.status_code, 201)
        code = request_response.data["otp_code"]
        self.assertTrue(
            PhoneOTP.objects.filter(
                phone_number="09100000002",
                purpose=PhoneOTP.Purpose.CHANGE_PHONE,
            ).exists()
        )

        verify_response = self.client.post(
            "/api/auth/change-phone/verify-otp/",
            {"phone_number": "09100000002", "otp": code},
            format="json",
        )

        self.assertEqual(verify_response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.phone_number, "09100000002")
        self.assertEqual(verify_response.data["phone_number"], "09100000002")

    def test_phone_change_rejects_existing_phone_number(self):
        user = User.objects.create_user(phone_number="09100000001")
        User.objects.create_user(phone_number="09100000002")
        self.authenticate(user)

        response = self.client.post(
            "/api/auth/change-phone/request-otp/",
            {"phone_number": "09100000002"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone_number", response.data)
