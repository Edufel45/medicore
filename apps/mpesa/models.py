"""
MediCore — M-Pesa Models
============================================================
Safaricom Daraja integration.

Models:
  - MPesaTransaction : one STK Push or C2B transaction attempt
  - CallbackLog      : raw callback payload from Safaricom

Design:
  - Every STK Push creates one MPesaTransaction row.
  - The transaction lifecycle:
      initiated → pending → success | failed | timeout | cancelled
  - Safaricom calls our callback URL when the customer enters
    their PIN. We log the raw payload to CallbackLog, then update
    the MPesaTransaction.
  - If the callback never arrives (network issues, timeouts), a
    Celery beat job queries the Daraja Transaction Status API
    to reconcile.

Proposal reference:
  - §1.3.2 III "M-Pesa Daraja API integration for STK Push payments,
    automatic reconciliation, and receipt generation"
  - §2.3 "STK Push (Lipa Na M-Pesa Online) to initiate payment
    prompt directly on the patient's phone"
  - §2.3 "Transaction Status Query to reconcile payments when
    callback is delayed or missing"
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


# ------------------------------------------------------------
# Choices
# ------------------------------------------------------------
TRANSACTION_TYPE_CHOICES = [
    ("stk_push",   "STK Push"),
    ("c2b",        "C2B (PayBill / Till)"),
    ("b2c",        "B2C (Refund / payout)"),
]

TRANSACTION_STATUS_CHOICES = [
    ("initiated", "Initiated"),
    ("pending",   "Pending (waiting for PIN)"),
    ("success",   "Success"),
    ("failed",    "Failed"),
    ("timeout",   "Timeout"),
    ("cancelled", "Cancelled by user"),
]


# ------------------------------------------------------------
# MPesaTransaction
# ------------------------------------------------------------
class MPesaTransaction(models.Model):
    """
    One M-Pesa transaction attempt.

    For STK Push:
      - Created when the pharmacist clicks "Collect payment".
      - `checkout_request_id` is returned by Daraja immediately.
      - `merchant_request_id` is also returned (Daraja bookkeeping).
      - `mpesa_receipt` (e.g. QK12345678) is set on success.

    For C2B:
      - Created when Safaricom sends a validation/confirmation
        callback. Reference is `trans_id`.

    The link back to billing is `Payment.mpesa_transaction` — a
    Payment row is auto-created / updated when the transaction
    succeeds.
    """

    # ---- Type ----
    transaction_type = models.CharField(
        max_length=10,
        choices=TRANSACTION_TYPE_CHOICES,
        default="stk_push",
        db_index=True,
    )
    status = models.CharField(
        max_length=10,
        choices=TRANSACTION_STATUS_CHOICES,
        default="initiated",
        db_index=True,
    )

    # ---- Amount & account ----
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    phone = models.CharField(
        max_length=15,
        help_text="Customer phone in international format, e.g. 254712345678.",
    )
    account_reference = models.CharField(
        max_length=40,
        blank=True,
        help_text="Invoice / visit reference, e.g. INV-001.",
    )
    description = models.CharField(
        max_length=100,
        blank=True,
        help_text="Shown to the customer on their phone.",
    )

    # ---- Daraja identifiers ----
    merchant_request_id = models.CharField(
        max_length=60,
        blank=True,
        db_index=True,
    )
    checkout_request_id = models.CharField(
        max_length=60,
        blank=True,
        db_index=True,
        help_text="Primary key from Daraja STK Push. Used to match callbacks.",
    )
    mpesa_receipt = models.CharField(
        max_length=30,
        blank=True,
        db_index=True,
        help_text="M-Pesa receipt number, e.g. QK12345678. Set on success.",
    )
    result_code = models.CharField(
        max_length=10,
        blank=True,
        help_text="Daraja result code, e.g. '0' for success, '1032' for cancelled.",
    )
    result_description = models.CharField(
        max_length=255,
        blank=True,
    )

    # ---- Timestamps ----
    initiated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ---- Ownership / linkage ----
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_transactions_initiated",
    )
    visit = models.ForeignKey(
        "clinical.Visit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_transactions",
        help_text="Visit this transaction is paying for.",
    )

    # ---- Raw payloads (for debugging / reconciliation) ----
    request_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Payload we sent to Daraja.",
    )
    response_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw response from Daraja (initiation).",
    )

    class Meta:
        ordering = ["-initiated_at"]
        verbose_name = "M-Pesa transaction"
        verbose_name_plural = "M-Pesa transactions"
        indexes = [
            models.Index(fields=["status", "-initiated_at"]),
            models.Index(fields=["checkout_request_id"]),
            models.Index(fields=["mpesa_receipt"]),
        ]

    def __str__(self):
        return (
            f"{self.get_transaction_type_display()} "
            f"Ksh {self.amount} "
            f"[{self.status}]"
        )

    # ---------------- Lifecycle helpers ----------------
    def mark_pending(self, merchant_request_id="", checkout_request_id="", response=None):
        self.status = "pending"
        self.merchant_request_id = merchant_request_id or self.merchant_request_id
        self.checkout_request_id = checkout_request_id or self.checkout_request_id
        if response is not None:
            self.response_payload = response
        self.save(update_fields=[
            "status", "merchant_request_id", "checkout_request_id",
            "response_payload", "updated_at",
        ])

    def mark_success(self, receipt="", result_code="0", result_description="Success"):
        self.status = "success"
        self.mpesa_receipt = receipt
        self.result_code = result_code
        self.result_description = result_description
        self.completed_at = timezone.now()
        self.save(update_fields=[
            "status", "mpesa_receipt", "result_code",
            "result_description", "completed_at", "updated_at",
        ])

    def mark_failed(self, reason="", result_code=""):
        self.status = "failed"
        self.result_code = result_code
        self.result_description = reason[:255]
        self.completed_at = timezone.now()
        self.save(update_fields=[
            "status", "result_code", "result_description",
            "completed_at", "updated_at",
        ])

    def mark_timeout(self):
        self.status = "timeout"
        self.result_description = "No callback received; reconciled by Celery."
        self.completed_at = timezone.now()
        self.save(update_fields=[
            "status", "result_description", "completed_at", "updated_at",
        ])


# ------------------------------------------------------------
# CallbackLog
# ------------------------------------------------------------
class CallbackLog(models.Model):
    """
    Raw callback payload from Safaricom.

    Every STK Push and C2B callback is logged here verbatim, even
    if the matching MPesaTransaction cannot be found. This is our
    debugging + audit trail for the M-Pesa integration.

    Never delete rows from this table.
    """

    KIND_CHOICES = [
        ("stk_push", "STK Push callback"),
        ("c2b_validation", "C2B validation"),
        ("c2b_confirmation", "C2B confirmation"),
        ("b2c_result", "B2C result"),
        ("b2c_timeout", "B2C timeout"),
        ("unknown", "Unknown"),
    ]

    kind = models.CharField(
        max_length=20,
        choices=KIND_CHOICES,
        default="unknown",
        db_index=True,
    )
    payload = models.JSONField(
        help_text="Raw callback body from Safaricom.",
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Source IP of the callback.",
    )

    # ---- Link to the transaction we matched it to ----
    matched_transaction = models.ForeignKey(
        MPesaTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="callbacks",
    )
    processed = models.BooleanField(
        default=False,
        help_text="True once we've updated the matching transaction.",
    )
    processing_note = models.CharField(
        max_length=255,
        blank=True,
    )

    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "M-Pesa callback"
        verbose_name_plural = "M-Pesa callbacks"
        indexes = [
            models.Index(fields=["kind", "-received_at"]),
            models.Index(fields=["processed", "-received_at"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} @ {self.received_at:%Y-%m-%d %H:%M}"