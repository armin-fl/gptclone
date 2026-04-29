from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import PhoneOTP, User
from .serializers import (
    PhoneChangeRequestSerializer,
    PhoneChangeVerifySerializer,
    PhoneOTPRequestSerializer,
    PhoneOTPVerifySerializer,
    UserProfileUpdateSerializer,
    UserSerializer,
)
from .services import OTPError, create_phone_otp, verify_phone_otp, verify_phone_otp_code


class RequestOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PhoneOTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]
        auth_mode = serializer.validated_data["auth_mode"]
        user_exists = User.objects.filter(phone_number=phone_number).exists()
        if auth_mode == PhoneOTP.Purpose.LOGIN and not user_exists:
            return Response(
                {"detail": "Account not found. Please register first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if auth_mode == PhoneOTP.Purpose.REGISTER and user_exists:
            return Response(
                {"detail": "Account already exists. Please log in."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = create_phone_otp(phone_number, auth_mode)
        response_data = {"detail": "OTP sent."}
        if settings.DEBUG:
            response_data["otp_code"] = created.code

        return Response(response_data, status=status.HTTP_201_CREATED)


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PhoneOTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = verify_phone_otp(
                serializer.validated_data["phone_number"],
                serializer.validated_data["otp"],
                serializer.validated_data["auth_mode"],
            )
        except OTPError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        refresh = RefreshToken.for_user(user)
        access_expires_at = timezone.now() + settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]
        refresh_expires_at = timezone.now() + settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "token_type": "Bearer",
                "access_expires_at": access_expires_at,
                "refresh_expires_at": refresh_expires_at,
                "user": UserSerializer(user, context={"request": request}).data,
            }
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        return Response(UserSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        serializer = UserProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user, context={"request": request}).data)


class RequestPhoneChangeOTPView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PhoneChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]
        created = create_phone_otp(phone_number, PhoneOTP.Purpose.CHANGE_PHONE)
        response_data = {"detail": "OTP sent."}
        if settings.DEBUG:
            response_data["otp_code"] = created.code

        return Response(response_data, status=status.HTTP_201_CREATED)


class VerifyPhoneChangeOTPView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PhoneChangeVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_phone_number = serializer.validated_data["phone_number"]
        if User.objects.filter(phone_number=new_phone_number).exclude(id=request.user.id).exists():
            return Response(
                {"detail": "This phone number is already in use."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            verified_phone_number = verify_phone_otp_code(
                new_phone_number,
                serializer.validated_data["otp"],
                PhoneOTP.Purpose.CHANGE_PHONE,
            )
        except OTPError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        request.user.phone_number = verified_phone_number
        try:
            request.user.save(update_fields=["phone_number"])
        except IntegrityError:
            return Response(
                {"detail": "This phone number is already in use."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(UserSerializer(request.user, context={"request": request}).data)
