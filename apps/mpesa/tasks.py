"""
MediCore — M-Pesa Celery Tasks
============================================================
Async processing for callbacks and reconciliation.

Tasks:
  - process_stk_callback   : handle an STK Push callback payload
  - reconcile_pending_txns : query Daraja for stuck transactions
  - process_reversal_result: handle a Reversal API callback
"""

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("medicore")


# ------------------------------------------------------------
# STK Push callback processing
# ------------------------------------------------------------
@shared_task(name="mpesa.process_stk_callback", bind=True, max_retries=3)
def process_stk_callback(self, callback_log_id):
    """
    Process an STK Push callback that has already been logged
    in CallbackLog. Called asynchronously from the callback view.
    """
    from apps.billing.models import Payment
    from apps.accounts.models import AuditLog

    from .models import CallbackLog, MPesaTransaction

    try:
        log = CallbackLog.objects.select_related("matched_transaction").get(
            pk=callback_log_id
        )
    except CallbackLog.DoesNotExist:
        logger.warning("CallbackLog %s not found", callback_log_id)
        return

    if log.processed:
        return

    payload = log.payload or {}
    stk = payload.get("Body", {}).get("stkCallback", {})
    checkout_request_id = stk.get("CheckoutRequestID", "")
    result_code = str(stk.get("ResultCode", ""))
    result_desc = stk.get("ResultDesc", "")

    if not checkout_request_id:
        log.processing_note = "No CheckoutRequestID in payload"
        log.processed = True
        log.save(update_fields=["processing_note", "processed"])
        return

    txn = MPesaTransaction.objects.filter(
        checkout_request_id=checkout_request_id
    ).first()

    if not txn:
        log.processing_note = f"No matching transaction for {checkout_request_id}"
        log.processed = True
        log.save(update_fields=["processing_note", "processed"])
        return

    log.matched_transaction = txn

    # Extract the receipt number
    receipt = ""
    if result_code == "0":
        items = stk.get("CallbackMetadata", {}).get("Item", [])
        for item in items:
            if item.get("Name") == "MpesaReceiptNumber":
                receipt = item.get("Value", "")
                break

    # ---- Success path ----
    if result_code == "0" and receipt:
        txn.mark_success(
            receipt=receipt,
            result_code=result_code,
            result_description=result_desc,
        )

        # Attach a Payment
        if txn.visit and not Payment.objects.filter(mpesa_transaction=txn).exists():
            bill = getattr(txn.visit, "bill", None)
            if bill:
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

                # Complete the visit
                visit = txn.visit
                for rx in visit.prescriptions.filter(is_dispensed=False):
                    rx.is_dispensed = True
                    rx.dispensed_at = timezone.now()
                    rx.save(update_fields=["is_dispensed", "dispensed_at"])

                visit.stage = "done"
                visit.completed_at = timezone.now()
                visit.save(update_fields=["stage", "completed_at", "updated_at"])

                # SHA claim if applicable
                try:
                    from apps.billing.services import create_sha_claim_if_needed
                    create_sha_claim_if_needed(visit, user=txn.initiated_by)
                except Exception as exc:
                    logger.warning("SHA claim creation failed: %s", exc)

                # Receipt PDF
                try:
                    from apps.billing.models import Receipt
                    from apps.billing.services import store_receipt_pdf
                    receipt_obj, _ = Receipt.objects.get_or_create(
                        bill=bill,
                        defaults={
                            "total_paid": bill.paid_amount,
                            "sha_covered": bill.sha_covered_amount or 0,
                            "issued_by": txn.initiated_by,
                        },
                    )
                    pdf_url = store_receipt_pdf(receipt_obj, bill, visit, force_local=True)
                    if pdf_url:
                        receipt_obj.pdf_url = pdf_url
                        receipt_obj.save(update_fields=["pdf_url"])
                except Exception as exc:
                    logger.warning("Receipt PDF failed: %s", exc)

                AuditLog.objects.create(
                    user=txn.initiated_by, role="pharmacy",
                    action="payment.pharmacy",
                    detail=f"{visit.patient.sid} · Ksh {txn.amount} · mpesa · {receipt}",
                    patient_sid=visit.patient.sid,
                )

        log.processing_note = f"Success · receipt {receipt}"

    # ---- Failure path ----
    else:
        txn.mark_failed(reason=result_desc, result_code=result_code)
        log.processing_note = f"Failed · {result_desc[:80]}"

    log.processed = True
    log.save(update_fields=[
        "matched_transaction", "processed", "processing_note",
    ])


# ------------------------------------------------------------
# Reconciliation — query Daraja for stuck transactions
# ------------------------------------------------------------
@shared_task(name="mpesa.reconcile_pending_txns")
def reconcile_pending_txns():
    """
    Find transactions stuck in 'pending' for more than 90 seconds
    and query Daraja for their real status.

    Schedule this to run every 60 seconds (see beat schedule).
    """
    from datetime import timedelta
    from .daraja import DarajaError, get_daraja
    from .models import MPesaTransaction

    cutoff = timezone.now() - timedelta(seconds=90)
    pending = MPesaTransaction.objects.filter(
        status__in=["initiated", "pending"],
        initiated_at__lte=cutoff,
    )[:50]  # cap per run

    client = get_daraja()
    reconciled = 0

    for txn in pending:
        if not txn.checkout_request_id:
            continue
        try:
            result = client.stk_query(txn.checkout_request_id)
        except DarajaError as exc:
            logger.warning("Reconcile query failed for %s: %s", txn.id, exc)
            continue

        code = result.get("result_code", "")
        desc = result.get("result_description", "")

        if code == "0":
            # In mock mode, invent a receipt; in real mode, this only
            # tells us the customer paid — the receipt arrives via callback
            receipt = "REC" + str(int(timezone.now().timestamp()))[-6:]
            txn.mark_success(receipt=receipt, result_code=code, result_description=desc)
            reconciled += 1
        elif code in ("1032", "1037", "2001"):
            # User cancelled, timeout, or insufficient funds
            txn.mark_timeout()
            reconciled += 1

    logger.info("Reconciled %s transactions", reconciled)
    return reconciled


# ------------------------------------------------------------
# Reversal callback processing
# ------------------------------------------------------------
@shared_task(name="mpesa.process_reversal_result")
def process_reversal_result(payload):
    """
    Handle the async Reversal API result callback from Safaricom.
    """
    logger.info("Reversal result: %s", payload)
    # Full implementation could flag the transaction as reversed



# ------------------------------------------------------------
# C2B confirmation processing
# ------------------------------------------------------------
@shared_task(name="mpesa.process_c2b_confirmation", bind=True, max_retries=3)
def process_c2b_confirmation(self, callback_log_id):
    """
    Handle a C2B confirmation callback.

    Safaricom sends:
      {
        "TransactionType": "Pay Bill",
        "TransID": "QK...",
        "TransTime": "20261016123456",
        "TransAmount": "660.00",
        "BusinessShortCode": "174379",
        "BillRefNumber": "INV-A-001",
        "MSISDN": "254712345678",
        "FirstName": "...",
        ...
      }

    We match `BillRefNumber` (e.g. "INV-A-001") to a Visit's queue number.
    If found and the visit has an outstanding bill, we record the
    payment and complete the visit.
    """
    from apps.billing.models import Payment
    from apps.accounts.models import AuditLog
    from apps.clinical.models import Visit

    from .models import CallbackLog, MPesaTransaction

    try:
        log = CallbackLog.objects.get(pk=callback_log_id)
    except CallbackLog.DoesNotExist:
        logger.warning("CallbackLog %s not found", callback_log_id)
        return

    if log.processed:
        return

    payload = log.payload or {}
    trans_id = payload.get("TransID", "")
    trans_amount = payload.get("TransAmount", "0")
    bill_ref = payload.get("BillRefNumber", "") or ""
    msisdn = payload.get("MSISDN", "")

    if not trans_id:
        log.processing_note = "No TransID"
        log.processed = True
        log.save(update_fields=["processing_note", "processed"])
        return

    # Parse the visit queue number from BillRefNumber (e.g. "INV-A-001")
    queue_number = ""
    if bill_ref.upper().startswith("INV-"):
        queue_number = bill_ref[4:].strip()
    elif bill_ref:
        queue_number = bill_ref.strip()

    visit = None
    if queue_number:
        visit = Visit.objects.filter(queue_number=queue_number).first()

    # ---- Create the MPesaTransaction ----
    from decimal import Decimal
    try:
        amount = Decimal(str(trans_amount))
    except Exception:
        amount = Decimal("0")

    txn = MPesaTransaction.objects.create(
        transaction_type="c2b",
        status="success",
        amount=amount,
        phone=msisdn,
        account_reference=bill_ref[:12],
        description=f"C2B {trans_id}"[:100],
        visit=visit,
        mpesa_receipt=trans_id,
        result_code="0",
        result_description="C2B success",
        completed_at=timezone.now(),
        response_payload=payload,
    )

    log.matched_transaction = txn

    # ---- If we matched a visit, record a Payment and complete ----
    if visit:
        bill = getattr(visit, "bill", None)
        if bill and amount > 0:
            Payment.objects.create(
                bill=bill,
                method="mpesa",
                amount=amount,
                status="success",
                confirmed_at=timezone.now(),
                mpesa_transaction=txn,
                mpesa_receipt=trans_id,
                initiated_by=None,  # C2B — customer initiated
            )
            bill.recompute_totals()

            # If fully settled, complete the visit
            if bill.balance_due <= 0:
                for rx in visit.prescriptions.filter(is_dispensed=False):
                    rx.is_dispensed = True
                    rx.dispensed_at = timezone.now()
                    rx.save(update_fields=["is_dispensed", "dispensed_at"])

                visit.stage = "done"
                visit.completed_at = timezone.now()
                visit.save(update_fields=["stage", "completed_at", "updated_at"])

                # SHA claim
                try:
                    from apps.billing.services import create_sha_claim_if_needed
                    create_sha_claim_if_needed(visit, user=None)
                except Exception as exc:
                    logger.warning("SHA claim creation failed: %s", exc)

                # Receipt PDF
                try:
                    from apps.billing.models import Receipt
                    from apps.billing.services import store_receipt_pdf
                    receipt_obj, _ = Receipt.objects.get_or_create(
                        bill=bill,
                        defaults={
                            "total_paid": bill.paid_amount,
                            "sha_covered": bill.sha_covered_amount or 0,
                        },
                    )
                    pdf_url = store_receipt_pdf(receipt_obj, bill, visit, force_local=True)
                    if pdf_url:
                        receipt_obj.pdf_url = pdf_url
                        receipt_obj.save(update_fields=["pdf_url"])
                except Exception as exc:
                    logger.warning("Receipt PDF failed: %s", exc)

                AuditLog.objects.create(
                    user=None, role="", action="payment.pharmacy",
                    detail=f"{visit.patient.sid} · C2B Ksh {amount} · {trans_id}",
                    patient_sid=visit.patient.sid,
                )

        log.processing_note = f"C2B matched visit {queue_number} · {trans_id}"
    else:
        log.processing_note = f"C2B {trans_id} — no visit for ref {bill_ref!r}"

    log.processed = True
    log.save(update_fields=["matched_transaction", "processed", "processing_note"])