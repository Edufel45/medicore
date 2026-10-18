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


# ------------------------------------------------------------
# OTP endpoints for SHA verification
# ------------------------------------------------------------
import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .providers import get_sha_otp_provider


@login_required
@require_POST
def send_otp_view(request):
    """
    POST /sha/send-otp/
    Body: sha_number=..., phone=...

    Returns JSON:
      {"verification_id": "...", "masked_phone": "07XX...5678",
       "dev_otp": "123456", "expires_in": 300}

    In production, remove `dev_otp` — the OTP goes via SMS only.
    """
    sha_number = (request.POST.get("sha_number") or "").strip().upper()
    phone = (request.POST.get("phone") or "").strip()

    try:
        provider = get_sha_otp_provider()
        result = provider.send_otp(sha_number=sha_number, phone=phone)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        import logging
        logging.getLogger("medicore").exception("send_otp failed: %s", exc)
        return JsonResponse({"error": "Could not send code. Try again."}, status=500)

    return JsonResponse(result)


@login_required
@require_POST
def confirm_otp_view(request):
    """
    POST /sha/confirm-otp/
    Body: verification_id=..., otp_code=...

    Returns JSON:
      {"status": "active"|"inactive"|"pending"|"unknown",
       "patient_name": "...", "message": "..."}

    On bad OTP: {"error": "..."} with HTTP 400.
    """
    verification_id = (request.POST.get("verification_id") or "").strip()
    otp_code = (request.POST.get("otp_code") or "").strip()

    if not verification_id or not otp_code:
        return JsonResponse({"error": "Missing verification ID or code."}, status=400)

    try:
        provider = get_sha_otp_provider()
        result = provider.verify_otp(verification_id=verification_id, otp_code=otp_code)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        import logging
        logging.getLogger("medicore").exception("confirm_otp failed: %s", exc)
        return JsonResponse({"error": "Could not verify code. Try again."}, status=500)

    return JsonResponse({
        "status": result.status,
        "patient_name": result.patient_name,
        "message": result.message,
    })