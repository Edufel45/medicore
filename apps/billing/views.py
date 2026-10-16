"""
MediCore — Billing Views
============================================================
Receipt view + ledger.
"""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.decorators import role_required
from apps.clinical.models import Visit

from .models import Bill, Payment, Receipt


# ------------------------------------------------------------
# Receipt — printable HTML
# ------------------------------------------------------------
@login_required
@role_required("reception", "pharmacy", "accounts", "admin")
def receipt_view(request, queue_number):
    """
    /billing/visits/<queue_number>/receipt/ — printable receipt.
    """
    visit = get_object_or_404(Visit, queue_number=queue_number)
    bill = getattr(visit, "bill", None)
    if not bill:
        return redirect("clinical:queue")

    receipt, _ = Receipt.objects.get_or_create(
        bill=bill,
        defaults={
            "total_paid": bill.paid_amount,
            "sha_covered": bill.sha_covered_amount or 0,
            "issued_by": request.user,
        },
    )

    return render(request, "billing/receipt_viewer.html", {
        "receipt": receipt,
        "bill": bill,
        "visit": visit,
        "patient": visit.patient,
        "line_items": bill.line_items.all(),
        "issued_at": receipt.issued_at or timezone.now(),
    })


# ------------------------------------------------------------
# Ledger — all payments with filters
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
def ledger(request):
    """
    /billing/ledger/ — all payments.
    Filters: q (search), method, range (date)
    """
    qs = (
        Payment.objects
        .select_related("bill__visit__patient", "initiated_by")
        .filter(status="success")
        .order_by("-initiated_at")
    )

    # ---- Search ----
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(mpesa_receipt__icontains=q)
            | Q(bill__visit__patient__sid__icontains=q)
            | Q(bill__visit__patient__full_name__icontains=q)
            | Q(bill__visit__queue_number__icontains=q)
        )

    # ---- Method filter ----
    method_filter = (request.GET.get("method") or "").strip()
    if method_filter:
        qs = qs.filter(method=method_filter)

    # ---- Date range ----
    rng = (request.GET.get("range") or "today").strip()
    now = timezone.now()
    if rng == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        qs = qs.filter(initiated_at__gte=start)
    elif rng == "7":
        qs = qs.filter(initiated_at__gte=now - timedelta(days=7))
    elif rng == "30":
        qs = qs.filter(initiated_at__gte=now - timedelta(days=30))
    # "all" → no filter

    # ---- KPIs ----
    total_amount = qs.aggregate(total=Sum("amount"))["total"] or 0

    # ---- Method breakdown ----
    method_breakdown = {}
    reg_total = 0.0
    pharmacy_total = 0.0

    for p in qs.select_related("bill").prefetch_related("bill__line_items")[:500]:
        key = p.get_method_display()
        bucket = method_breakdown.setdefault(key, {"count": 0, "total": 0.0})
        bucket["count"] += 1
        bucket["total"] += float(p.amount)

        # Classify registration-only vs others
        kinds = {li.kind for li in p.bill.line_items.all()}
        if kinds == {"registration"}:
            reg_total += float(p.amount)
        else:
            pharmacy_total += float(p.amount)

    return render(request, "billing/ledger.html", {
        "payments": list(qs[:200]),
        "total_count": qs.count(),
        "total_amount": total_amount,
        "q": q,
        "method_filter": method_filter,
        "range": rng,
        "method_choices": [
            ("", "All methods"),
            ("mpesa", "M-Pesa"),
            ("cash", "Cash"),
            ("sha", "SHA"),
        ],
        "range_choices": [
            ("today", "Today"),
            ("7", "Last 7 days"),
            ("30", "Last 30 days"),
            ("all", "All time"),
        ],
        "reg_total": reg_total,
        "pharmacy_total": pharmacy_total,
        "method_breakdown": method_breakdown,
    })