"""
MediCore — Audit Views
============================================================
Read-only audit log, restricted to the Accountant role.

Routes:
  /audit/            list with filters (q, role, range)
  /audit/export/     CSV download (same filters as list)
"""

import csv
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .decorators import role_required
from .models import AuditLog


# ------------------------------------------------------------
# Filter helper — used by both list and export
# ------------------------------------------------------------
def _filter_audit_qs(request):
    """Apply the URL filters to an AuditLog queryset."""
    qs = AuditLog.objects.select_related("user").all()

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(detail__icontains=q)
            | Q(path__icontains=q)
            | Q(patient_sid__icontains=q)
            | Q(user__username__icontains=q)
        )

    role_filter = (request.GET.get("role") or "").strip()
    if role_filter:
        qs = qs.filter(role=role_filter)

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

    return qs


# ------------------------------------------------------------
# Audit list
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
def audit_list(request):
    qs = _filter_audit_qs(request).order_by("-created_at")

    total = qs.count()
    entries = list(qs[:200])

    # KPI counts for today
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_qs = AuditLog.objects.filter(created_at__gte=today_start)

    context = {
        "entries": entries,
        "total": total,
        "shown": len(entries),
        "q": (request.GET.get("q") or ""),
        "role_filter": (request.GET.get("role") or ""),
        "range": (request.GET.get("range") or "today"),
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
            "today": today_qs.count(),
            "logins": today_qs.filter(action="login").count(),
            "views": today_qs.filter(action__startswith="patient.view").count(),
            "payments": today_qs.filter(action__startswith="payment.").count(),
        },
    }
    return render(request, "accounts/audit.html", context)


# ------------------------------------------------------------
# CSV export
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
def audit_export_csv(request):
    """
    /audit/export/ — download the filtered audit log as CSV.
    Uses the same filters as the list view.
    """
    qs = _filter_audit_qs(request).order_by("-created_at")

    # Log the export itself
    AuditLog.objects.create(
        user=request.user,
        role="accounts",
        action="audit.export",
        detail=f"Exported {qs.count()} audit entries to CSV",
        path=request.path,
        method=request.method,
    )

    # Build the response
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    filename = f"medicore_audit_{timestamp}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Timestamp",
        "User",
        "Role",
        "Action",
        "Detail",
        "Patient SID",
        "IP Address",
        "Method",
        "Path",
    ])

    for entry in qs.iterator():
        writer.writerow([
            entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            entry.user.username if entry.user else "system",
            entry.role or "—",
            entry.get_action_display(),
            entry.detail or "",
            entry.patient_sid or "",
            entry.ip_address or "",
            entry.method or "",
            entry.path or "",
        ])

    return response