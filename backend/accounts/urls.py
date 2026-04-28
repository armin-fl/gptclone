from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import MeView, RequestOTPView, VerifyOTPView

urlpatterns = [
    path("request-otp/", RequestOTPView.as_view(), name="auth-request-otp"),
    path("verify-otp/", VerifyOTPView.as_view(), name="auth-verify-otp"),
    path("refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me/", MeView.as_view(), name="auth-me"),
]
