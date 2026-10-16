"""
MediCore — Audit Views
============================================================
Read-only audit log, restricted to the Accountant role.

Filters supported via querystring:
  ?q=...          free-text search across detail / path / patient_sid / user
  ?role=...       one of the role keys
  ?range=today    24h | 7 | 30 | all
"""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from .decorators import role_required
from .models import AuditLog


@login_required
@role_required("accounts", "admin")
def audit_list(request):
    qs = AuditLog.objects.select_related("user").all()

    # ----- Search -----
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(detail__icontains=q)
            | Q(path__icontains=q)
            | Q(patient_sid__icontains=q)
            | Q(user__username__icontains=q)
        )

    # ----- Role filter -----
    role_filter = (request.GET.get("role") or "").strip()
    if role_filter:
        qs = qs.filter(role=role_filter)

    # ----- Date range -----
    rng = (request.GET.get("range") or "today").strip()
    now = timezone.now()
    if rng == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        qs = qs.filter(created_at__gte=start)
    elif rng == "24h":
        qs = qs.filter(created_at__gte=now - timedelta(hours=24))
    elif rng == "7":
        qs = qs.filter(created_at__gte=now - timedelta(days=7))
    elif rng == "30":
        qs = qs.filter(created_at__gte=now - timedelta(days=30))
    # "all" → no filter

    total = qs.count()
    entries = list(qs[:200])  # cap display at 200

    # Group counts for the KPI cards (today)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_qs = AuditLog.objects.filter(created_at__gte=today_start)
    today_count = today_qs.count()
    login_count = today_qs.filter(action="login").count()
    view_count = today_qs.filter(action__startswith="patient.view").count()
    payment_count = today_qs.filter(action__startswith="payment.").count()

    context = {
        "entries": entries,
        "total": total,
        "shown": len(entries),
        "q": q,
        "role_filter": role_filter,
        "range": rng,
        "role_choices": [
            ("reception", "Receptionist"),
            ("nurse", "Nurse"),
            ("doctor", "Doctor"),
            ("lab", "Lab Technician"),
            ("pharmacy", "Pharmacist"),
            ("accounts", "Accountant"),
        ],
        "range_choices": [
            ("today", "Today"),
            ("24h", "Last 24 hours"),
            ("7", "Last 7 days"),
            ("30", "Last 30 days"),
            ("all", "All time"),
        ],
        "kpi": {
            "today": today_count,
            "logins": login_count,
            "views": view_count,
            "payments": payment_count,
        },
    }
    return render(request, "accounts/audit.html", context)