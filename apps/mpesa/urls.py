"""
MediCore — M-Pesa URLs
============================================================
Namespace: mpesa
"""

from django.urls import path

from . import views

app_name = "mpesa"

urlpatterns = [
    # STK Push
    path("initiate/<str:queue_number>/",
         views.initiate_stk, name="initiate"),
    path("callback/stk/",
         views.stk_callback, name="stk_callback"),
    path("simulate/<int:transaction_id>/",
         views.simulate_callback, name="simulate_callback"),

    # Reversal
    path("reversal/<int:transaction_id>/",
         views.reversal_initiate, name="reversal"),

    # C2B
    path("callback/c2b/validate/",
         views.c2b_validation, name="c2b_validation"),
    path("callback/c2b/confirm/",
         views.c2b_confirmation, name="c2b_confirmation"),
    path("c2b/register/",
         views.c2b_register, name="c2b_register"),

    # Log
    path("transactions/",
         views.transactions, name="transactions"),
]