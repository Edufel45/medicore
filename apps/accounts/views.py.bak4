"""
MediCore — Accounts Views
============================================================
Authentication + role-aware home dashboard.
"""

from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from .forms import MediCoreLoginForm


class MediCoreLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = MediCoreLoginForm
    redirect_authenticated_user = True


@login_required
def home(request):
    """
    Role-aware home.
    - Doctor goes straight to the key-in lookup.
    - Nurse goes straight to their lookup / vitals station.
    - Lab technician goes straight to their lookup station.
    - Everyone else uses their role dashboard.
    """
    role = None
    try:
        role = request.user.profile.role
    except Exception:
        role = None

    # Doctors land on the lookup page
    if role == "doctor":
        return redirect("clinical:doctor_lookup")

    # Nurses land on their lookup / vitals station
    if role == "nurse":
        return redirect("clinical:nurse_lookup")

    # Lab technicians land on their lookup station
    if role == "lab":
        return redirect("clinical:lab_lookup")

    template_map = {
        "reception": "accounts/home_reception.html",
        "nurse":     "accounts/home_nurse.html",
        "lab":       "accounts/home_lab.html",
        "pharmacy":  "accounts/home_pharmacy.html",
        "accounts":  "accounts/home_accounts.html",
    }

    template = template_map.get(role, "accounts/home.html")
    return render(request, template)


def logout_view(request):
    auth_logout(request)
    return redirect("accounts:login")