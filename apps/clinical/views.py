"""
MediCore — Clinical Views
============================================================
The full clinical loop:

  /queue/                        hospital-wide kanban
  /visits/<queue>/               per-visit workbench
  /visits/<queue>/vitals/        nurse saves vitals
  /visits/<queue>/request-vitals/    doctor asks for vitals
  /visits/<queue>/send-to-lab/   doctor routes to lab
  /visits/<queue>/send-to-pharmacy/  doctor routes to pharmacy
  /visits/<queue>/save-diagnosis/    doctor saves dx
  /visits/<queue>/add-rx/        doctor adds prescription
  /visits/<queue>/remove-rx/<id>/    doctor removes prescription
  /visits/<queue>/add-lab/       doctor orders lab test
  /visits/<queue>/release-lab/   lab releases results
  /visits/<queue>/price-rx/<id>/ pharmacy sets price
  /visits/<queue>/collect/       pharmacy collects + dispenses + completes
  /lab/inbox/                    doctor's inbox (patients back from lab)
"""

import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.decorators import role_required
from apps.accounts.models import AuditLog
from apps.billing.models import Payment, Receipt
from apps.billing.services import (
    create_sha_claim_if_needed,
    store_receipt_pdf,
)
from apps.laboratory.models import LabResult, LabTest
from apps.pharmacy.models import Drug

from .models import Diagnosis, Prescription, Visit, Vitals


logger = logging.getLogger("medicore")

STAGE_ORDER = ["reg", "nurse", "doctor", "lab", "pharmacy"]


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _dec(val):
    """Safe Decimal conversion; returns None if empty/invalid."""
    if val in (None, "", "None"):
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def _int(val):
    if val in (None, "", "None"):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------
# Queue board
# ------------------------------------------------------------
@login_required
@role_required("reception", "nurse", "doctor", "lab", "pharmacy", "accounts", "admin")
def queue_board(request):
    visits = (
        Visit.objects
        .exclude(stage="done")
        .select_related("patient", "bill")
        .prefetch_related("lab_tests", "prescriptions", "vitals")
        .order_by("stage", "-started_at")
    )

    columns = {stage: [] for stage in STAGE_ORDER}
    now = timezone.now()

    for v in visits:
        wait_min = int((now - v.started_at).total_seconds() // 60)
        has_lab_ready = any(t.status == "released" for t in v.lab_tests.all())

        v.card = {
            "sid": v.patient.sid,
            "queue": v.queue_number,
            "name": v.patient.full_name,
            "sha_status": v.patient.sha_status,
            "sha_badge": v.patient.sha_badge_class,
            "wait_min": wait_min,
            "lab_ready": has_lab_ready,
            "vitals_requested": (v.stage == "nurse"),
            "reason": (v.reason_for_visit or "")[:40],
        }
        if v.stage in columns:
            columns[v.stage].append(v)

    return render(request, "clinical/queue.html", {
        "columns": columns,
        "stage_order": STAGE_ORDER,
    })


# ------------------------------------------------------------
# Workbench
# ------------------------------------------------------------
@login_required
@role_required("reception", "nurse", "doctor", "lab", "pharmacy", "accounts", "admin")
def visit_detail(request, queue_number):
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "bill"),
        queue_number=queue_number,
    )
    try:
        user_role = request.user.profile.role
    except Exception:
        user_role = "admin"

    wait_min = int((timezone.now() - visit.started_at).total_seconds() // 60)
    vitals = getattr(visit, "vitals", None)
    diagnosis = getattr(visit, "diagnosis", None)
    prescriptions = visit.prescriptions.all()
    lab_tests = visit.lab_tests.all()
    bill = getattr(visit, "bill", None)
    drugs = Drug.objects.filter(is_active=True).order_by("name")[:200]

    return render(request, "clinical/visit_detail.html", {
        "visit": visit,
        "patient": visit.patient,
        "vitals": vitals,
        "diagnosis": diagnosis,
        "prescriptions": prescriptions,
        "lab_tests": lab_tests,
        "bill": bill,
        "drugs": drugs,
        "user_role": user_role,
        "wait_min": wait_min,
    })


# ------------------------------------------------------------
# Nurse — save vitals
# ------------------------------------------------------------
@login_required
@role_required("nurse", "admin")
def save_vitals(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    with transaction.atomic():
        v, _ = Vitals.objects.get_or_create(
            visit=visit,
            defaults={"recorded_by": request.user},
        )
        v.blood_pressure = (request.POST.get("blood_pressure") or "").strip()[:15]
        v.temperature_c = _dec(request.POST.get("temperature_c"))
        v.weight_kg = _dec(request.POST.get("weight_kg"))
        v.pulse_bpm = _int(request.POST.get("pulse_bpm"))
        v.recorded_by = request.user
        v.requested_by_doctor = False
        v.save()

        visit.stage = "doctor"
        visit.save(update_fields=["stage", "updated_at"])

        AuditLog.objects.create(
            user=request.user, role="nurse", action="stage.change",
            detail=f"{visit.patient.sid} vitals → Doctor · BP {v.blood_pressure or '—'}",
            patient_sid=visit.patient.sid,
            path=request.path, method=request.method,
        )

    messages.success(request, f"Vitals saved for {visit.queue_number}. Sent back to doctor.")
    return redirect("clinical:queue")


# ------------------------------------------------------------
# Doctor — request vitals
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def request_vitals(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    visit.stage = "nurse"
    visit.save(update_fields=["stage", "updated_at"])

    AuditLog.objects.create(
        user=request.user, role="doctor", action="stage.change",
        detail=f"{visit.patient.sid} → Nurse (vitals requested)",
        patient_sid=visit.patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(request, f"{visit.queue_number} sent to nurse for vitals.")
    return redirect("clinical:queue")


# ------------------------------------------------------------
# Doctor — send to lab
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def send_to_lab(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    visit.stage = "lab"
    visit.save(update_fields=["stage", "updated_at"])

    AuditLog.objects.create(
        user=request.user, role="doctor", action="stage.change",
        detail=f"{visit.patient.sid} → Laboratory",
        patient_sid=visit.patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(request, f"{visit.queue_number} sent to Laboratory.")
    return redirect("clinical:queue")


# ------------------------------------------------------------
# Doctor — send to pharmacy
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def send_to_pharmacy(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    visit.stage = "pharmacy"
    visit.save(update_fields=["stage", "updated_at"])

    AuditLog.objects.create(
        user=request.user, role="doctor", action="stage.change",
        detail=f"{visit.patient.sid} → Pharmacy",
        patient_sid=visit.patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(request, f"{visit.queue_number} sent to Pharmacy.")
    return redirect("clinical:queue")


# ------------------------------------------------------------
# Doctor — save diagnosis
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def save_diagnosis(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    text = (request.POST.get("diagnosis") or "").strip()

    with transaction.atomic():
        dx, _ = Diagnosis.objects.get_or_create(
            visit=visit,
            defaults={"recorded_by": request.user},
        )
        dx.text = text
        dx.recorded_by = request.user
        dx.save()

        AuditLog.objects.create(
            user=request.user, role="doctor", action="other",
            detail=f"{visit.patient.sid} diagnosis saved: {text[:60]}",
            patient_sid=visit.patient.sid,
            path=request.path, method=request.method,
        )

    messages.success(request, "Diagnosis saved.")
    return redirect("clinical:visit_detail", queue_number=queue_number)


# ------------------------------------------------------------
# Doctor — add prescription
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def add_prescription(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    drug_name = (request.POST.get("drug_name") or "").strip()
    if not drug_name:
        messages.error(request, "Enter a drug name.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    resolved = (
        Drug.objects
        .filter(is_active=True)
        .filter(Q(name__iexact=drug_name) | Q(aliases__icontains=drug_name))
        .first()
    )

    Prescription.objects.create(
        visit=visit,
        drug_name_typed=drug_name,
        drug=resolved,
        prescribed_by=request.user,
    )

    AuditLog.objects.create(
        user=request.user, role="doctor", action="rx.add",
        detail=f"{visit.patient.sid} · {drug_name}",
        patient_sid=visit.patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(request, f"Prescription added: {drug_name}")
    return redirect("clinical:visit_detail", queue_number=queue_number)


# ------------------------------------------------------------
# Doctor — remove prescription
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def remove_prescription(request, queue_number, prescription_id):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    rx = get_object_or_404(Prescription, pk=prescription_id, visit=visit)

    if rx.is_dispensed:
        messages.error(request, "Cannot remove — already dispensed.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    name = rx.drug_name_typed
    rx.delete()

    AuditLog.objects.create(
        user=request.user, role="doctor", action="rx.remove",
        detail=f"{visit.patient.sid} · {name}",
        patient_sid=visit.patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(request, f"Removed {name}.")
    return redirect("clinical:visit_detail", queue_number=queue_number)


# ------------------------------------------------------------
# Doctor — add lab order
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def add_lab_order(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    test_name = (request.POST.get("test_name") or "").strip()
    if not test_name:
        messages.error(request, "Enter a test name.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    try:
        price = Decimal(str(request.POST.get("test_price") or "0"))
    except (InvalidOperation, ValueError):
        price = Decimal("0")

    lt = LabTest.objects.create(
        visit=visit,
        name=test_name,
        price_ksh=price,
        status="ordered",
        ordered_by=request.user,
    )

    if visit.bill:
        visit.bill.add_line_item(
            label=f"Lab: {test_name}",
            amount=price,
            kind="lab",
            lab_test=lt,
        )

    AuditLog.objects.create(
        user=request.user, role="doctor", action="other",
        detail=f"{visit.patient.sid} lab ordered: {test_name}",
        patient_sid=visit.patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(request, f"Lab ordered: {test_name}")
    return redirect("clinical:visit_detail", queue_number=queue_number)


# ------------------------------------------------------------
# Lab — release results
# ------------------------------------------------------------
@login_required
@role_required("lab", "admin")
def release_lab_results(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    raw = (request.POST.get("lab_results") or "").strip()

    with transaction.atomic():
        parsed = {}
        for line in raw.split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, _, value = line.partition(":")
            parsed[name.strip().lower()] = value.strip()

        for lt in visit.lab_tests.all():
            key = lt.name.strip().lower()
            value = parsed.get(key)
            if value is None:
                continue
            LabResult.objects.update_or_create(
                lab_test=lt,
                defaults={"value": value, "entered_by": request.user},
            )
            lt.status = "released"
            lt.released_at = timezone.now()
            lt.released_by = request.user
            lt.save(update_fields=["status", "released_at", "released_by"])

        visit.stage = "doctor"
        visit.save(update_fields=["stage", "updated_at"])

        AuditLog.objects.create(
            user=request.user, role="lab", action="stage.change",
            detail=f"{visit.patient.sid} lab results → Doctor",
            patient_sid=visit.patient.sid,
            path=request.path, method=request.method,
        )

    messages.success(request, f"{visit.queue_number} results released to doctor.")
    return redirect("clinical:queue")


# ------------------------------------------------------------
# Doctor — lab inbox
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def lab_inbox(request):
    visits = (
        Visit.objects
        .filter(stage="doctor")
        .select_related("patient")
        .prefetch_related("lab_tests__result")
        .order_by("-updated_at")
    )

    inbox = []
    for v in visits:
        released = [t for t in v.lab_tests.all() if t.status == "released"]
        if released:
            inbox.append({"visit": v, "released": released})

    return render(request, "clinical/lab_inbox.html", {"inbox": inbox})


# ------------------------------------------------------------
# Pharmacy — set price for an unpriced prescription
# ------------------------------------------------------------
@login_required
@role_required("pharmacy", "admin")
def price_prescription(request, queue_number, prescription_id):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    rx = get_object_or_404(Prescription, pk=prescription_id, visit=visit)

    try:
        price = Decimal(str(request.POST.get("price") or "0"))
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid price.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    if price <= 0:
        messages.error(request, "Price must be greater than zero.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    drug, created = Drug.objects.get_or_create(
        name=rx.drug_name_typed.strip().lower(),
        defaults={
            "price_ksh": price,
            "unit": "unit",
            "is_active": True,
        },
    )
    if not created and drug.price_ksh != price:
        drug.price_ksh = price
        drug.save(update_fields=["price_ksh"])

    rx.drug = drug
    rx.save(update_fields=["drug"])

    if visit.bill:
        visit.bill.add_line_item(
            label=f"Drug: {drug.name}",
            amount=price,
            kind="drug",
            prescription=rx,
        )

    AuditLog.objects.create(
        user=request.user, role="pharmacy", action="drug.price.set",
        detail=f"{rx.drug_name_typed} → Ksh {price}",
        patient_sid=visit.patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(request, f"Price set: {drug.name} → Ksh {price}")
    return redirect("clinical:visit_detail", queue_number=queue_number)


# ------------------------------------------------------------
# Pharmacy — collect payment + dispense + complete + receipt + SHA claim
# ------------------------------------------------------------
@login_required
@role_required("pharmacy", "admin")
def collect_payment(request, queue_number):
    visit = get_object_or_404(Visit, queue_number=queue_number)
    if request.method != "POST":
        return redirect("clinical:visit_detail", queue_number=queue_number)

    bill = getattr(visit, "bill", None)
    if not bill:
        messages.error(request, "No bill found.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    bill.recompute_totals()
    if bill.balance_due <= 0:
        messages.info(request, "Bill already settled.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    method = (request.POST.get("method") or "mpesa").strip()

    with transaction.atomic():
        # ---- 1. Record payment ----
        Payment.objects.create(
            bill=bill,
            method=method,
            amount=bill.balance_due,
            status="success",
            confirmed_at=timezone.now(),
            initiated_by=request.user,
            mpesa_receipt=(
                "SIM" + str(int(timezone.now().timestamp()))[-6:]
                if method == "mpesa" else ""
            ),
        )
        bill.recompute_totals()

        # ---- 2. Dispense all prescriptions ----
        for rx in visit.prescriptions.filter(is_dispensed=False):
            rx.is_dispensed = True
            rx.dispensed_at = timezone.now()
            rx.save(update_fields=["is_dispensed", "dispensed_at"])

        # ---- 3. Complete the visit ----
        visit.stage = "done"
        visit.completed_at = timezone.now()
        visit.save(update_fields=["stage", "completed_at", "updated_at"])

        # ---- 4. Audit ----
        AuditLog.objects.create(
            user=request.user, role="pharmacy", action="payment.pharmacy",
            detail=f"{visit.patient.sid} · Ksh {bill.total_amount} · {method}",
            patient_sid=visit.patient.sid,
            path=request.path, method=request.method,
        )

        # ---- 5. Auto-create SHA claim if applicable ----
        try:
            create_sha_claim_if_needed(visit, user=request.user)
        except Exception as exc:
            logger.warning("SHA claim creation failed (ignored): %s", exc)

        # ---- 6. Auto-generate receipt + PDF ----
        receipt, _ = Receipt.objects.get_or_create(
            bill=bill,
            defaults={
                "total_paid": bill.paid_amount,
                "sha_covered": bill.sha_covered_amount or 0,
                "issued_by": request.user,
            },
        )
        try:
            pdf_url = store_receipt_pdf(receipt, bill, visit, force_local=True)
            if pdf_url:
                receipt.pdf_url = pdf_url
                receipt.save(update_fields=["pdf_url"])
        except Exception as exc:
            logger.warning("Receipt PDF failed: %s", exc)

    messages.success(
        request,
        f"Payment collected · {visit.queue_number} complete. Counter cleared."
    )
    return redirect("clinical:queue")