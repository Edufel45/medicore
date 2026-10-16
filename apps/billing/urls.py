"""
MediCore — Billing URLs
============================================================
Namespace: billing
"""

from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("ledger/", views.ledger, name="ledger"),
    path(
        "visits/<str:queue_number>/receipt/",
        views.receipt_view,
        name="receipt",
    ),
]