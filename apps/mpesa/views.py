"""
MediCore — M-Pesa Views
============================================================
STK Push initiation, callback handling, transaction log.

Routes:
  /mpesa/initiate/<queue_number>/   POST — kick off STK Push
  /mpesa/callback/stk/              POST — Safaricom callback (CSRF exempt)
  /mpesa/simulate/<transaction_id>/ POST — dev-only: fake a success callback
  /mpesa/transactions/              GET  — accountant view of all transactions
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import AuditLog
from apps.billing.models import Bill, Payment
from apps.clinical.models import Visit
from apps.clinical.views import _complete_visit_after_payment

from .daraja import DarajaError, get_daraja
from .models import CallbackLog, MPesaTransaction


logger = logging.getLogger("medicore")


# ------------------------------------------------------------
# Initiate STK Push
# ------------------------------------------------------------
@login_required
@role_required("pharmacy", "admin")
@require_POST
def initiate_stk(request, queue_number):
    """
    POST /mpesa/initiate/<queue_number>/

    Kicks off an STK Push for the visit's outstanding bill.
    Creates an MPesaTransaction in 'pending' state and stores
    the checkout_request_id for callback matching.
    """
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "bill"),
        queue_number=queue_number,
    )

    bill = getattr(visit, "bill", None)
    if not bill:
        messages.error(request, "No bill found.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    bill.recompute_totals()
    if bill.balance_due <= 0:
        messages.info(request, "Bill already settled.")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    patient = visit.patient
    amount = bill.balance_due
    phone = patient.phone

    # ---- 1. Create MPesaTransaction (pending) ----
    txn = MPesaTransaction.objects.create(
        transaction_type="stk_push",
        status="initiated",
        amount=amount,
        phone=phone,
        account_reference=f"INV-{visit.queue_number}",
        description=f"MediCore {visit.queue_number}",
        visit=visit,
        initiated_by=request.user,
    )

    # ---- 2. Call Daraja ----
    client = get_daraja()
    try:
        result = client.stk_push(
            phone=phone,
            amount=amount,
            account_reference=f"INV-{visit.queue_number}",
            description=f"MediCore {visit.queue_number}",
        )
    except DarajaError as exc:
        txn.mark_failed(reason=str(exc)[:255])
        logger.exception("STK Push initiation failed: %s", exc)
        messages.error(request, f"M-Pesa error: {exc}")
        return redirect("clinical:visit_detail", queue_number=queue_number)

    # ---- 3. Update the transaction with Daraja's response ----
    txn.mark_pending(
        merchant_request_id=result.get("merchant_request_id", ""),
        checkout_request_id=result.get("checkout_request_id", ""),
        response=result.get("raw", {}),
    )

    # ---- 4. Audit ----
    AuditLog.objects.create(
        user=request.user, role="pharmacy", action="payment.pharmacy",
        detail=f"{patient.sid} · STK Push Ksh {amount} to {phone}",
        patient_sid=patient.sid,
        path=request.path, method=request.method,
    )

    messages.success(
        request,
        f"STK Push sent to {phone} for Ksh {amount}. "
        f"Waiting for the patient to enter their PIN…"
    )
    return redirect("clinical:visit_detail", queue_number=queue_number)


# ------------------------------------------------------------
# Callback — Safaricom → MediCore
# ------------------------------------------------------------
@csrf_exempt
@require_POST
def stk_callback(request):
    """
    POST /mpesa/callback/stk/

    Safaricom posts the STK Push result here.

    Strategy:
      1. Log the raw payload (always).
      2. Dispatch an async Celery task to process the callback.
      3. Return 200 immediately (Daraja requires response < 5s).

    In DEBUG, CELERY_TASK_ALWAYS_EAGER=True means the task runs
    synchronously inline — no worker needed for dev.
    """
    raw_ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
    )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        logger.warning("STK callback — bad JSON: %s", exc)
        return JsonResponse(
            {"ResultCode": 1, "ResultDesc": "Bad request"}, status=400
        )

    # ---- 1. Log the raw payload ----
    log = CallbackLog.objects.create(
        kind="stk_push",
        payload=payload,
        ip_address=raw_ip,
    )

    # ---- 2. Dispatch async processing ----
    try:
        from .tasks import process_stk_callback
        process_stk_callback.delay(log.id)
    except Exception as exc:
        # Never let a task dispatch failure stop the 200 response.
        # Daraja will retry if we return non-200.
        logger.exception("Failed to dispatch process_stk_callback: %s", exc)

    # ---- 3. Return 200 ----
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


# ------------------------------------------------------------
# Simulate callback (dev only)
# ------------------------------------------------------------
@login_required
@role_required("pharmacy", "admin")
@require_POST
def simulate_callback(request, transaction_id):
    """
    POST /mpesa/simulate/<transaction_id>/

    Dev-only helper: acts as if Safaricom sent a successful callback.
    Only usable when Daraja is in mock mode.
    """
    client = get_daraja()
    if not client.is_mock:
        messages.error(request, "Simulation only available in mock mode.")
        return redirect("mpesa:transactions")

    txn = get_object_or_404(MPesaTransaction, pk=transaction_id)

    if txn.status not in ("initiated", "pending"):
        messages.info(request, f"Transaction already {txn.status}.")
        return redirect("clinical:visit_detail", queue_number=txn.visit.queue_number)

    # Simulate a successful callback
    receipt = "SIM" + str(int(timezone.now().timestamp()))[-6:]
    txn.mark_success(
        receipt=receipt,
        result_code="0",
        result_description="Simulated",
    )

    _attach_payment_for_transaction(txn, receipt=receipt)

    CallbackLog.objects.create(
        kind="stk_push",
        payload={"simulated": True, "receipt": receipt},
        matched_transaction=txn,
        processed=True,
        processing_note=f"Simulated success · {receipt}",
    )

    messages.success(
        request,
        f"✅ M-Pesa payment confirmed · Receipt {receipt}. "
        f"{txn.visit.queue_number} complete."
    )
    # Land on the printable receipt — pharmacist prints, then closes back to queue.
    return redirect("billing:receipt", queue_number=txn.visit.queue_number)


# ------------------------------------------------------------
# Reversal (refund) — accountant only
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
@require_POST
def reversal_initiate(request, transaction_id):
    """
    POST /mpesa/reversal/<transaction_id>/

    Reverses a successful M-Pesa transaction via Daraja.

    Side effects:
      - Calls DarajaClient.reversal()
      - Marks the MPesaTransaction as reversed
      - Marks the linked Payment as refunded
      - Writes an audit entry
    """
    txn = get_object_or_404(MPesaTransaction, pk=transaction_id)

    # ---- Guards ----
    if txn.status != "success":
        messages.error(request, "Only successful transactions can be reversed.")
        return redirect("mpesa:transactions")

    if txn.reversed_at:
        messages.info(request, "Transaction already reversed.")
        return redirect("mpesa:transactions")

    if not txn.mpesa_receipt:
        messages.error(request, "Transaction has no M-Pesa receipt to reverse.")
        return redirect("mpesa:transactions")

    reason = (request.POST.get("reason") or "").strip() or "Refund requested"

    # ---- Call Daraja ----
    client = get_daraja()
    try:
        result = client.reversal(
            transaction_id=txn.mpesa_receipt,
            amount=txn.amount,
            remarks=reason,
        )
    except DarajaError as exc:
        logger.exception("Reversal API failed: %s", exc)
        messages.error(request, f"Reversal failed: {exc}")
        return redirect("mpesa:transactions")

    # ---- Update the transaction ----
    txn.reversed_at = timezone.now()
    txn.reversed_by = request.user
    txn.reversal_reason = reason[:255]
    txn.save(update_fields=["reversed_at", "reversed_by", "reversal_reason"])

    # ---- Refund the linked Payment ----
    linked = Payment.objects.filter(mpesa_transaction=txn)
    for p in linked:
        p.status = "refunded"
        p.save(update_fields=["status"])
    # Recompute bill totals so the "balance due" reflects the refund
    for p in linked:
        if p.bill:
            p.bill.recompute_totals()

    # ---- Audit ----
    AuditLog.objects.create(
        user=request.user, role="accounts", action="payment.pharmacy",
        detail=(
            f"{txn.visit.patient.sid if txn.visit else '—'} · "
            f"REVERSAL Ksh {txn.amount} · {txn.mpesa_receipt} · {reason[:60]}"
        ),
        patient_sid=txn.visit.patient.sid if txn.visit else "",
        path=request.path, method=request.method,
    )

    messages.success(
        request,
        f"Refund initiated for {txn.mpesa_receipt}. "
        f"Daraja will process it shortly."
    )
    return redirect("mpesa:transactions")


# ------------------------------------------------------------
# C2B — Validation (Safaricom asks us to accept or reject)
# ------------------------------------------------------------
@csrf_exempt
@require_POST
def c2b_validation(request):
    """
    POST /mpesa/callback/c2b/validate/

    Safaricom asks us whether to accept a PayBill payment.
    We accept everything (ResponseType=Completed in registration),
    but this endpoint must exist or the payment fails.

    Returns:
      {"ResultCode": 0, "ResultDesc": "Accepted"}
    """
    raw_ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
    )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        logger.warning("C2B validation — bad JSON: %s", exc)
        return JsonResponse(
            {"ResultCode": 1, "ResultDesc": "Bad request"}, status=400
        )

    CallbackLog.objects.create(
        kind="c2b_validation",
        payload=payload,
        ip_address=raw_ip,
        processed=True,
        processing_note="Validation accepted",
    )

    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


# ------------------------------------------------------------
# C2B — Confirmation (payment actually happened)
# ------------------------------------------------------------
@csrf_exempt
@require_POST
def c2b_confirmation(request):
    """
    POST /mpesa/callback/c2b/confirm/

    Safaricom sends the final payment details. We:
      1. Log the raw payload.
      2. Dispatch an async task to match it to a Visit + Payment.
      3. Return 200 immediately.
    """
    raw_ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
    )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        logger.warning("C2B confirmation — bad JSON: %s", exc)
        return JsonResponse(
            {"ResultCode": 1, "ResultDesc": "Bad request"}, status=400
        )

    log = CallbackLog.objects.create(
        kind="c2b_confirmation",
        payload=payload,
        ip_address=raw_ip,
    )

    try:
        from .tasks import process_c2b_confirmation
        process_c2b_confirmation.delay(log.id)
    except Exception as exc:
        logger.exception("Failed to dispatch process_c2b_confirmation: %s", exc)

    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


# ------------------------------------------------------------
# C2B — Register URLs (dev helper)
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
@require_POST
def c2b_register(request):
    """
    POST /mpesa/c2b/register/

    One-time registration of C2B URLs with Safaricom.
    In mock mode this is a no-op that returns a fake success.
    """
    client = get_daraja()

    try:
        confirmation_url = request.build_absolute_uri("/mpesa/callback/c2b/confirm/")
        validation_url = request.build_absolute_uri("/mpesa/callback/c2b/validate/")
        result = client.c2b_register_urls(
            confirmation_url=confirmation_url,
            validation_url=validation_url,
        )
    except DarajaError as exc:
        messages.error(request, f"C2B registration failed: {exc}")
        return redirect("mpesa:transactions")

    messages.success(
        request,
        f"C2B URLs registered. Response: {result.get('response_description', 'OK')}"
    )
    return redirect("mpesa:transactions")


# ------------------------------------------------------------
# Transaction log (accountant view)
# ------------------------------------------------------------
@login_required
@role_required("accounts", "admin")
def transactions(request):
    """
    GET /mpesa/transactions/ — all M-Pesa transactions.
    """
    qs = MPesaTransaction.objects.select_related(
        "visit__patient", "initiated_by"
    ).order_by("-initiated_at")

    status_filter = (request.GET.get("status") or "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)

    return render(request, "mpesa/transactions.html", {
        "transactions": list(qs[:200]),
        "total": qs.count(),
        "status_filter": status_filter,
        "status_choices": [
            ("", "All statuses"),
            ("initiated", "Initiated"),
            ("pending", "Pending"),
            ("success", "Success"),
            ("failed", "Failed"),
            ("timeout", "Timeout"),
            ("cancelled", "Cancelled"),
        ],
    })


# ------------------------------------------------------------
# Internal — attach a Payment row for a successful transaction
# ------------------------------------------------------------
def _attach_payment_for_transaction(txn, receipt=""):
    """
    Called when a transaction is marked success.
    Creates a Payment on the visit's bill, recomputes totals,
    then completes the visit (dispense, receipt, SHA claim, done).
    """
    if not txn.visit:
        return
    bill = getattr(txn.visit, "bill", None)
    if not bill:
        return

    # Avoid duplicates
    if Payment.objects.filter(mpesa_transaction=txn).exists():
        return

    from django.db import transaction as _tx

    with _tx.atomic():
        Payment.objects.create(
            bill=bill,
            method="mpesa",
            amount=txn.amount,
            status="success",
            confirmed_at=timezone.now(),
            mpesa_transaction=txn,
            mpesa_receipt=receipt,
            initiated_by=txn.initiated_by,
        )
        bill.recompute_totals()

        visit = txn.visit
        if visit.stage != "done":
            _complete_visit_after_payment(
                visit, bill, "mpesa",
                txn.initiated_by,
                request=None,
            )