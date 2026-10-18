"""
MediCore - SHA URLs
============================================================
Namespace: sha
"""

from django.urls import path

from . import views

app_name = "sha"

urlpatterns = [
    # Claims (accountant)
    path("claims/", views.claims_list, name="claims_list"),
    path("claims/<int:claim_id>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:claim_id>/set/", views.claim_set_status, name="claim_set_status"),

    # OTP verification (used during patient registration)
    path("send-otp/",    views.send_otp_view,    name="send_otp"),
    path("confirm-otp/", views.confirm_otp_view, name="confirm_otp"),
]