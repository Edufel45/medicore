"""
MediCore — SHA Views
============================================================
Claims list + claim detail with lifecycle simulation.

Routes:
  /sha/claims/            list of all claims with filters
  /sha/claims/<id>/       single claim with timeline
  /sha/claims/<id>/set/   POST — set status (Approved/Rejected/Pending/Paid)
"""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.decorators import role_required
from apps.accounts.models import AuditLog

from .models import SHAClaim


# ------------------------------------------------------------
# Claims list
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
def claims_list(request):
    """
    /sha/claims/ — all SHA claims.
    Filters: q (search), status, range (date)
    """
    qs = (
        SHAClaim.objects
        .select_related("visit__patient")
        .order_by("-created_at")
    )

    # Search
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(claim_reference__icontains=q)
            | Q(patient_name__icontains=q)
            | Q(sha_number__icontains=q)
            | Q(visit__patient__sid__icontains=q)
        )

    # Status filter
    status_filter = (request.GET.get("status") or "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)

    # Date range
    rng = (request.GET.get("range") or "30").strip()
    now = timezone.now()
    if rng == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        qs = qs.filter(created_at__gte=start)
    elif rng == "7":
        qs = qs.filter(created_at__gte=now - timedelta(days=7))
    elif rng == "30":
        qs = qs.filter(created_at__gte=now - timedelta(days=30))
    # "all" → no filter

    # KPIs
    base_qs = SHAClaim.objects.all()
    counts = {
        "total": base_qs.count(),
        "pending": base_qs.filter(
            status__in=["draft", "pending", "under_review"]
        ).count(),
        "approved": base_qs.filter(status="approved").count(),
        "rejected": base_qs.filter(status="rejected").count(),
        "paid": base_qs.filter(status="paid").count(),
    }

    return render(request, "sha/claims_list.html", {
        "claims": list(qs[:200]),
        "total": qs.count(),
        "q": q,
        "status_filter": status_filter,
        "range": rng,
        "counts": counts,
        "status_choices": [
            ("", "All statuses"),
            ("draft", "Draft"),
            ("pending", "Pending"),
            ("under_review", "Under review"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("paid", "Paid"),
        ],
        "range_choices": [
            ("today", "Today"),
            ("7", "Last 7 days"),
            ("30", "Last 30 days"),
            ("all", "All time"),
        ],
    })


# ------------------------------------------------------------
# Claim detail
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
def claim_detail(request, claim_id):
    """
    /sha/claims/<id>/ — one claim with full lifecycle + simulate actions.
    """
    claim = get_object_or_404(
        SHAClaim.objects.select_related("visit__patient"),
        pk=claim_id,
    )
    visit = claim.visit
    bill = getattr(visit, "bill", None)

    steps = [
        {
            "label": "Submitted to SHA",
            "small": f"Reference {claim.claim_reference}",
            "done": True,
            "current": False,
            "failed": False,
        },
        {
            "label": "Awaiting adjudication",
            "small": "SHA reviewing benefit package",
            "done": claim.status in ("under_review", "approved", "rejected", "paid"),
            "current": claim.status in ("draft", "pending"),
            "failed": False,
        },
        {
            "label": "Decision",
            "small": (
                "Approved" if claim.status in ("approved", "paid")
                else "Rejected" if claim.status == "rejected"
                else "Awaiting outcome"
            ),
            "done": claim.status in ("approved", "rejected", "paid"),
            "current": claim.status == "under_review",
            "failed": claim.status == "rejected",
        },
        {
            "label": "Paid",
            "small": "Funds disbursed" if claim.status == "paid" else "Pending disbursement",
            "done": claim.status == "paid",
            "current": False,
            "failed": False,
        },
    ]

    return render(request, "sha/claim_detail.html", {
        "claim": claim,
        "visit": visit,
        "patient": visit.patient,
        "bill": bill,
        "line_items": bill.line_items.all() if bill else [],
        "steps": steps,
    })


# ------------------------------------------------------------
# Simulate claim status change
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
def claim_set_status(request, claim_id):
    """
    POST — set a claim's status.

    Form fields:
      status: "pending" | "under_review" | "approved" | "rejected" | "paid"
      reason: optional rejection reason
    """
    if request.method != "POST":
        return redirect("sha:claim_detail", claim_id=claim_id)

    claim = get_object_or_404(SHAClaim, pk=claim_id)
    new_status = (request.POST.get("status") or "").strip()
    reason = (request.POST.get("reason") or "").strip()

    valid = {"pending", "under_review", "approved", "rejected", "paid"}
    if new_status not in valid:
        messages.error(request, "Invalid status.")
        return redirect("sha:claim_detail", claim_id=claim_id)

    # Apply the change with the model's helper methods
    if new_status == "pending":
        claim.status = "pending"
        claim.submitted_at = claim.submitted_at or timezone.now()
        claim.save(update_fields=["status", "submitted_at", "updated_at"])

    elif new_status == "under_review":
        claim.status = "under_review"
        claim.save(update_fields=["status", "updated_at"])

    elif new_status == "approved":
        claim.mark_approved(approved_amount=claim.claim_amount)

    elif new_status == "rejected":
        claim.mark_rejected(reason=reason or "Missing diagnosis code")

    elif new_status == "paid":
        claim.mark_paid(paid_amount=claim.approved_amount or claim.claim_amount)

    AuditLog.objects.create(
        user=request.user, role="accounts",
        action=f"claim.{new_status}",
        detail=f"{claim.claim_reference} for {claim.patient_name}",
        patient_sid=claim.visit.patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(request, f"Claim marked {new_status}.")
    return redirect("sha:claim_detail", claim_id=claim_id)