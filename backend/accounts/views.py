from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import PhoneOTPRequestSerializer, PhoneOTPVerifySerializer, UserSerializer
from .services import OTPError, create_phone_otp, verify_phone_otp


class RequestOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PhoneOTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        created = create_phone_otp(serializer.validated_data["phone_number"])
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
                "user": UserSerializer(user).data,
            }
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
