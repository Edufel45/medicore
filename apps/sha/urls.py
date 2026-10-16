"""
MediCore — SHA URLs
============================================================
Namespace: sha
"""

from django.urls import path

from . import views

app_name = "sha"

urlpatterns = [
    path("claims/", views.claims_list, name="claims_list"),
    path("claims/<int:claim_id>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:claim_id>/set/", views.claim_set_status, name="claim_set_status"),
]