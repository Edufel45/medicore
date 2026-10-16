"""
MediCore — Patients URLs
============================================================
"""

from django.urls import path

from . import views

app_name = "patients"

urlpatterns = [
    path("",              views.patient_list,    name="list"),
    path("register/",     views.register,        name="register"),
    path("sha-verify/",   views.sha_verify_ajax, name="sha_verify"),
]