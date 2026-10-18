"""
MediCore — Patients Views
============================================================
Registration, list, HTMX SHA verify, patient slip.
"""

from decimal import Decimal

from django.conf import settings as dj_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import AuditLog
from apps.billing.models import Bill, Payment
from apps.clinical.models import Visit
from apps.sha.providers import get_sha_provider

from .forms import PatientForm, VisitForm
from .models import Patient


# ------------------------------------------------------------
# Register
# ------------------------------------------------------------
@login_required
@role_required("reception", "admin")
def register(request):
    """Register a new patient + open their first visit."""
    if request.method == "POST":
        form = PatientForm(request.POST)
        visit_form = VisitForm(request.POST)

        if form.is_valid() and visit_form.is_valid():
            with transaction.atomic():
                patient = form.save(commit=False)
                patient.created_by = request.user
                patient.save()

                provider = get_sha_provider()
                if patient.sha_number:
                    result = provider.verify_eligibility(patient.sha_number)
                    patient.sha_status = result.status
                    patient.sha_verified_at = timezone.now()
                    patient.save(update_fields=["sha_status", "sha_verified_at"])

                visit = Visit.objects.create(
                    patient=patient,
                    queue_number=Visit.generate_queue_number(),
                    stage="doctor",
                    reason_for_visit=visit_form.cleaned_data.get("reason_for_visit")
                        or patient.reason_for_visit,
                    created_by=request.user,
                )

                bill = Bill.objects.create(visit=visit)
                bill.add_line_item(
                    label="Registration fee",
                    amount=Decimal(str(dj_settings.REGISTRATION_FEE)),
                    kind="registration",
                )

                if visit_form.cleaned_data.get("collect_registration_fee"):
                    Payment.objects.create(
                        bill=bill,
                        method="mpesa",
                        amount=Decimal(str(dj_settings.REGISTRATION_FEE)),
                        status="success",
                        confirmed_at=timezone.now(),
                        initiated_by=request.user,
                        mpesa_receipt="SIM" + str(int(timezone.now().timestamp()))[-6:],
                    )
                    bill.recompute_totals()
                    visit.registration_fee_paid = True
                    visit.registration_fee_paid_at = timezone.now()
                    visit.save(update_fields=[
                        "registration_fee_paid", "registration_fee_paid_at",
                    ])

                AuditLog.objects.create(
                    user=request.user, role="reception",
                    action="patient.register",
                    detail=f"{patient.sid} · {patient.full_name}",
                    patient_sid=patient.sid,
                    path=request.path, method=request.method,
                )

            sha_msg = {
                "active":   "SHA active — billable to SHA.",
                "inactive": "SHA inactive — patient pays by M-Pesa.",
                "pending":  "SHA pending — re-verify before billing.",
                "unknown":  "SHA status unknown.",
            }.get(patient.sha_status, "")

            messages.success(
                request,
                f"Registered {patient.sid} · {patient.full_name}. "
                f"Queue {visit.queue_number}. {sha_msg}"
            )
            # Redirect to the printable patient slip
            return redirect("patients:slip", queue_number=visit.queue_number)

        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = PatientForm()
        visit_form = VisitForm()

    return render(request, "patients/register.html", {
        "form": form,
        "visit_form": visit_form,
    })


# ------------------------------------------------------------
# List / search
# ------------------------------------------------------------
@login_required
@role_required("reception", "nurse", "doctor", "lab", "pharmacy", "accounts", "admin")
def patient_list(request):
    """Searchable list of patients."""
    q = (request.GET.get("q") or "").strip()
    scope = (request.GET.get("scope") or "active").strip()

    qs = (
        Patient.objects
        .prefetch_related("visits")
        .order_by("-created_at")
    )

    if scope == "active":
        qs = qs.filter(is_active=True)

    if q:
        qs = qs.filter(
            Q(sid__icontains=q)
            | Q(full_name__icontains=q)
            | Q(national_id__icontains=q)
            | Q(phone__icontains=q)
            | Q(sha_number__icontains=q)
        )

    patients = list(qs[:200])

    return render(request, "patients/list.html", {
        "patients": patients,
        "q": q,
        "scope": scope,
        "total": qs.count(),
    })


# ------------------------------------------------------------
# HTMX — live SHA verify
# ------------------------------------------------------------
@login_required
@require_POST
def sha_verify_ajax(request):
    """POST /patients/sha-verify/ — returns a small HTML fragment."""
    sha_number = (request.POST.get("sha_number") or "").strip().upper()

    if not sha_number:
        return render(request, "patients/partials/sha_result.html", {
            "empty": True,
        })

    provider = get_sha_provider()
    result = provider.verify_eligibility(sha_number)

    return render(request, "patients/partials/sha_result.html", {
        "result": result,
    })


# ------------------------------------------------------------
# Patient slip — printable A5 receipt for the doctor
# ------------------------------------------------------------
@login_required
@role_required("reception", "admin", "doctor", "nurse")
def patient_slip(request, queue_number):
    """
    /patients/slip/<queue_number>/ — printable A5 slip.

    The receptionist prints this after registration and gives it
    to the patient. They carry it to the doctor, who uses the
    System ID to look them up.
    """
    visit = get_object_or_404(
        Visit.objects.select_related("patient"),
        queue_number=queue_number,
    )
    return render(request, "patients/slip.html", {
        "visit": visit,
        "patient": visit.patient,
        "now": timezone.now(),
    })