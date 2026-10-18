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
  /doctor/lookup/                doctor's key-in lookup
  /nurse/lookup/                 nurse's key-in lookup
  /api/drugs/                    drug autocomplete for prescription
  /api/labs/                     lab test autocomplete for orders
"""

import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.decorators import role_required
from apps.accounts.models import AuditLog
from apps.billing.models import Bill, Payment, Receipt
from apps.billing.services import (
    create_sha_claim_if_needed,
    store_receipt_pdf,
)
from apps.laboratory.models import LabResult, LabTest
from apps.pharmacy.models import Drug
from apps.patients.models import Patient

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


def _get_bill_or_none(visit):
    """Safely return a visit's bill, or None if it has none."""
    try:
        return visit.bill
    except Visit.bill.RelatedObjectDoesNotExist:
        return None


# ------------------------------------------------------------
# Queue board
# ------------------------------------------------------------
@login_required
@role_required("reception", "nurse", "doctor", "lab", "pharmacy", "accounts", "admin")
def queue_board(request):
    """
    Role-aware queue board.

    Each role sees ONLY their own stage — no counter overlap.
    The accountant sees everything (read-only oversight).
    Receptionists see `reg` (unpaid registrations awaiting fee).
    """
    try:
        user_role = request.user.profile.role
    except Exception:
        user_role = "admin"

    role_to_stage = {
        "reception": "reg",
        "nurse":     "nurse",
        "doctor":    "doctor",
        "lab":       "lab",
        "pharmacy":  "pharmacy",
    }

    if user_role == "admin" or user_role == "accounts":
        visible_stages = STAGE_ORDER
        own_stage = None
    else:
        own_stage = role_to_stage.get(user_role)
        visible_stages = [own_stage] if own_stage else STAGE_ORDER

    visits = (
        Visit.objects
        .filter(stage__in=visible_stages)
        .exclude(stage="done")
        .select_related("patient", "bill")
        .prefetch_related("lab_tests", "prescriptions", "vitals")
        .order_by("-started_at")
    )

    columns = {stage: [] for stage in visible_stages}
    now = timezone.now()

    for v in visits:
        wait_min = int((now - v.started_at).total_seconds() // 60)
        has_lab_ready = any(t.status == "released" for t in v.lab_tests.all())

        v.card = {
            "sid": v.patient.sid,
            "queue": v.queue_number,
            "name": v.patient.full_name,
            "age": v.patient.age,
            "sex": v.patient.sex,
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
        "stage_order": visible_stages,
        "own_stage": own_stage,
        "user_role": user_role,
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
    bill = _get_bill_or_none(visit)
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

    with transaction.atomic():
        visit.lab_tests.filter(status="released").update(
            status="ordered",
            released_at=None,
            released_by=None,
        )
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

    bill = _get_bill_or_none(visit)
    if bill:
        bill.add_line_item(
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

    with transaction.atomic():
        released_count = 0
        for lt in visit.lab_tests.all():
            field_name = f"result_{lt.id}"
            value = (request.POST.get(field_name) or "").strip()
            if not value:
                continue

            LabResult.objects.update_or_create(
                lab_test=lt,
                defaults={"value": value, "entered_by": request.user},
            )
            lt.status = "released"
            lt.released_at = timezone.now()
            lt.released_by = request.user
            lt.save(update_fields=["status", "released_at", "released_by"])
            released_count += 1

        if released_count > 0:
            visit.stage = "doctor"
            visit.save(update_fields=["stage", "updated_at"])

        AuditLog.objects.create(
            user=request.user, role="lab", action="stage.change",
            detail=f"{visit.patient.sid} lab results → Doctor ({released_count} test{'s' if released_count != 1 else ''} released)",
            patient_sid=visit.patient.sid,
            path=request.path, method=request.method,
        )

    if released_count > 0:
        messages.success(
            request,
            f"{released_count} result{'s' if released_count != 1 else ''} released to doctor."
        )
    else:
        messages.warning(request, "No results were entered — patient stays in lab.")

    return redirect("clinical:queue")


# ------------------------------------------------------------
# Doctor — lab inbox
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def lab_inbox(request):
    visits = (
        Visit.objects
        .filter(stage="doctor", lab_tests__status="released")
        .select_related("patient")
        .prefetch_related("lab_tests__result")
        .distinct()
        .order_by("-updated_at")
    )

    inbox = []
    for v in visits:
        released = [t for t in v.lab_tests.all() if t.status == "released"]
        if released:
            inbox.append({"visit": v, "released": released})

    return render(request, "clinical/lab_inbox.html", {
        "inbox": inbox,
        "pending_count": len(inbox),
    })


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

    bill = _get_bill_or_none(visit)
    if bill:
        bill.add_line_item(
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

    bill = _get_bill_or_none(visit)
    if not bill:
        messages.error(request, "No bill found.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    bill.recompute_totals()
    if bill.balance_due <= 0:
        messages.info(request, "Bill already settled.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    method = (request.POST.get("method") or "mpesa").strip()

    if method == "mpesa":
        from apps.mpesa.daraja import get_daraja
        client = get_daraja()

        if not client.is_mock:
            return redirect("mpesa:initiate", queue_number=visit.queue_number)

    with transaction.atomic():
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

        for rx in visit.prescriptions.filter(is_dispensed=False):
            rx.is_dispensed = True
            rx.dispensed_at = timezone.now()
            rx.save(update_fields=["is_dispensed", "dispensed_at"])

        visit.stage = "done"
        visit.completed_at = timezone.now()
        visit.save(update_fields=["stage", "completed_at", "updated_at"])

        AuditLog.objects.create(
            user=request.user, role="pharmacy", action="payment.pharmacy",
            detail=f"{visit.patient.sid} · Ksh {bill.total_amount} · {method}",
            patient_sid=visit.patient.sid,
            path=request.path, method=request.method,
        )

        try:
            create_sha_claim_if_needed(visit, user=request.user)
        except Exception as exc:
            logger.warning("SHA claim creation failed (ignored): %s", exc)

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


# ------------------------------------------------------------
# Doctor — key-in lookup
# ------------------------------------------------------------
@login_required
@role_required("doctor", "admin")
def doctor_lookup(request):
    sid = (request.GET.get("sid") or "").strip().upper()
    visit = None
    lookup_error = ""
    lookup_status = ""

    if sid:
        patient = Patient.objects.filter(sid__iexact=sid).first()
        if patient:
            visit = (
                Visit.objects
                .filter(patient=patient, stage="doctor")
                .select_related("patient")
                .order_by("-started_at")
                .first()
            )
            if not visit:
                other = (
                    Visit.objects
                    .filter(patient=patient)
                    .exclude(stage="done")
                    .order_by("-started_at")
                    .first()
                )
                if other:
                    lookup_status = (
                        f"Patient {patient.sid} is currently at "
                        f"{other.get_stage_display()} — not yet at your counter."
                    )
                else:
                    last_done = (
                        Visit.objects
                        .filter(patient=patient, stage="done")
                        .order_by("-completed_at")
                        .first()
                    )
                    if last_done:
                        lookup_status = (
                            f"Patient {patient.sid} was last seen on "
                            f"{last_done.completed_at:%d %b %Y} — visit complete."
                        )
                    else:
                        lookup_status = f"Patient {patient.sid} found but has no active visit."
        else:
            visit = (
                Visit.objects
                .filter(queue_number__iexact=sid, stage="doctor")
                .select_related("patient")
                .first()
            )
            if not visit:
                patient = Patient.objects.filter(national_id=sid).first()
                if patient:
                    visit = (
                        Visit.objects
                        .filter(patient=patient, stage="doctor")
                        .select_related("patient")
                        .order_by("-started_at")
                        .first()
                    )
                    if not visit:
                        lookup_status = f"Patient (NID {sid}) is not currently at your counter."
                else:
                    lookup_error = f'No patient found for "{sid}".'

        if visit:
            return redirect("clinical:visit_detail", queue_number=visit.queue_number)

    recent = (
        Visit.objects
        .filter(stage="doctor")
        .select_related("patient")
        .order_by("-updated_at")[:5]
    )

    return render(request, "clinical/doctor_lookup.html", {
        "sid": sid,
        "lookup_error": lookup_error,
        "lookup_status": lookup_status,
        "recent": recent,
        "pending_count": Visit.objects.filter(stage="doctor").count(),
    })


# ------------------------------------------------------------
# Nurse — key-in lookup
# ------------------------------------------------------------
@login_required
@role_required("nurse", "admin")
def nurse_lookup(request):
    """
    /nurse/lookup/ — the nurse's main workflow.

    Accepts: System ID, queue number, or NID.
    Only opens visits at the nurse stage (vitals awaiting).
    """
    sid = (request.GET.get("sid") or "").strip().upper()
    visit = None
    lookup_error = ""
    lookup_status = ""

    if sid:
        # 1. Try System ID
        patient = Patient.objects.filter(sid__iexact=sid).first()
        if patient:
            visit = (
                Visit.objects
                .filter(patient=patient, stage="nurse")
                .select_related("patient")
                .order_by("-started_at")
                .first()
            )
            if not visit:
                other = (
                    Visit.objects
                    .filter(patient=patient)
                    .exclude(stage="done")
                    .order_by("-started_at")
                    .first()
                )
                if other:
                    lookup_status = (
                        f"Patient {patient.sid} is currently at "
                        f"{other.get_stage_display()} — not yet at your counter."
                    )
                else:
                    lookup_status = f"Patient {patient.sid} found but has no active visit."
        else:
            # 2. Try queue number
            visit = (
                Visit.objects
                .filter(queue_number__iexact=sid, stage="nurse")
                .select_related("patient")
                .first()
            )
            if not visit:
                # 3. Try NID
                patient = Patient.objects.filter(national_id=sid).first()
                if patient:
                    visit = (
                        Visit.objects
                        .filter(patient=patient, stage="nurse")
                        .select_related("patient")
                        .order_by("-started_at")
                        .first()
                    )
                    if not visit:
                        lookup_status = f"Patient (NID {sid}) is not currently at your counter."
                else:
                    lookup_error = f'No patient found for "{sid}".'

        if visit:
            return redirect("clinical:visit_detail", queue_number=visit.queue_number)

    recent = (
        Visit.objects
        .filter(stage="nurse")
        .select_related("patient")
        .order_by("-updated_at")[:5]
    )

    return render(request, "clinical/nurse_lookup.html", {
        "sid": sid,
        "lookup_error": lookup_error,
        "lookup_status": lookup_status,
        "recent": recent,
        "pending_count": Visit.objects.filter(stage="nurse").count(),
    })


# ------------------------------------------------------------
# Nurse — stat counts for home dashboard
# ------------------------------------------------------------
@login_required
@role_required("nurse", "admin")
def nurse_stats(request):
    """
    /nurse/stats/ — JSON with nurse dashboard counts.
    Used by home_nurse.html to show live numbers.
    """
    from datetime import timedelta

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    awaiting = Visit.objects.filter(stage="nurse", req_vitals=True).count() if _has_req_vitals() else Visit.objects.filter(stage="nurse").count()
    requested = Visit.objects.filter(stage="nurse").count()
    completed_today = (
        Vitals.objects
        .filter(recorded_at__gte=today_start)
        .count()
    )

    # Average time spent in nurse stage today (rough)
    avg_min = 0
    completed_visits = (
        Visit.objects
        .filter(
            updated_at__gte=today_start,
            stage="doctor",
            vitals__recorded_at__gte=today_start,
        )
        .select_related("vitals")
    )
    durations = []
    for v in completed_visits:
        try:
            durations.append(
                int((v.updated_at - v.vitals.recorded_at).total_seconds() // 60)
            )
        except Exception:
            pass
    if durations:
        avg_min = round(sum(durations) / len(durations))

    return JsonResponse({
        "awaiting": awaiting,
        "requested": requested,
        "completed_today": completed_today,
        "avg_min": avg_min,
    })


def _has_req_vitals():
    """Check if Vitals model has req_vitals field (defensive)."""
    try:
        Vitals.objects.filter(req_vitals=True).exists()
        return True
    except Exception:
        return False


# ------------------------------------------------------------
# Autocomplete APIs
# ------------------------------------------------------------
@login_required
@role_required("doctor", "pharmacy", "admin")
def api_drug_search(request):
    q = (request.GET.get("q") or "").strip().lower()

    if not q:
        return JsonResponse({"results": []})

    drugs = (
        Drug.objects
        .filter(is_active=True)
        .filter(Q(name__icontains=q) | Q(aliases__icontains=q))
        .order_by("name")[:10]
    )

    return JsonResponse({
        "results": [
            {
                "id": d.id,
                "name": d.name,
                "price": float(d.price_ksh) if d.price_ksh else 0,
                "stock": d.total_stock,
            }
            for d in drugs
        ]
    })


@login_required
@role_required("doctor", "lab", "admin")
def api_lab_search(request):
    from django.conf import settings

    q = (request.GET.get("q") or "").strip().lower()

    catalogue = getattr(settings, "LAB_TEST_CATALOGUE", [
        {"name": "Malaria test",          "price": 300},
        {"name": "Urinalysis",            "price": 250},
        {"name": "Blood sugar",           "price": 200},
        {"name": "Full haemogram",        "price": 600},
        {"name": "HIV test",              "price": 0},
        {"name": "Pregnancy test",        "price": 250},
        {"name": "Stool analysis",        "price": 200},
        {"name": "Liver function test",   "price": 1200},
        {"name": "Renal function test",   "price": 1200},
        {"name": "Lipid profile",         "price": 900},
        {"name": "Typhoid test",          "price": 350},
        {"name": "Brucella test",         "price": 400},
        {"name": "Blood group",           "price": 200},
        {"name": "Widal test",            "price": 300},
        {"name": "Sputum AFB",            "price": 500},
        {"name": "HbA1c",                 "price": 800},
        {"name": "PT/INR",                "price": 600},
        {"name": "ESR",                   "price": 200},
        {"name": "Urea & electrolytes",   "price": 800},
    ])

    if q:
        results = [t for t in catalogue if q in t["name"].lower()]
    else:
        results = catalogue[:10]

    return JsonResponse({"results": results[:10]})