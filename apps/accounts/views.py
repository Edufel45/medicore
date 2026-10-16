"""
MediCore — Accounts Views
============================================================
Currently provides:

  - home   : /  (temporary dashboard for Phase 1)
  - login  : /accounts/login/  (Phase 3 will flesh this out)

Real views for registration, queue, billing, etc. are added per
phase as we build the system.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render


def home(request):
    """
    Temporary landing page.
    Phase 3 replaces this with a role-aware dashboard.
    """
    return render(request, "accounts/home.html")