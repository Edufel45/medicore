"""
MediCore — Billing Models
============================================================
The money layer.

Models:
  - Bill       : the invoice for a Visit (one per visit)
  - LineItem   : one charge (consultation, drug, lab test)
  - Payment    : one payment attempt (M-Pesa STK, cash)
  - Receipt    : official receipt after successful payment

Design:
  - A Bill is 1-to-1 with a Visit.
  - LineItems are FK to the Bill — amount per item.
  - Payment is FK to Bill — multiple payments allow partial
    settlement (e.g. SHA covers 70%, patient pays 30% via M-Pesa).
  - Receipt is FK to Bill — created only when balance hits zero.

Payment gating:
  The `is_settled` property on Bill drives the payment gate.
  A prescription cannot be dispensed, and a visit cannot be
  marked `done`, until the bill is settled.

Proposal reference:
  - §2.3 "automatic invoice generation for consultations,
    procedures, lab tests, and drugs"
  - §2.3 "payment gating where the system does not allow dispensing
    until payment reflects"
  - §2.3 "receipt generation and printing at dispensing points"
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


# ------------------------------------------------------------
# Choices
# ------------------------------------------------------------
BILL_STATUS_CHOICES = [
    ("open",       "Open"),
    ("partial",    "Partially paid"),
    ("settled",    "Settled"),
    ("cancelled",  "Cancelled"),
]

LINE_ITEM_KIND_CHOICES = [
    ("registration", "Registration fee"),
    ("consultation", "Consultation"),
    ("lab",          "Laboratory"),
    ("drug",         "Medication"),
    ("procedure",    "Procedure"),
    ("other",        "Other"),
]

PAYMENT_METHOD_CHOICES = [
    ("mpesa", "M-Pesa"),
    ("cash",  "Cash"),
    ("sha",   "SHA (insurance)"),
    ("other", "Other"),
]

PAYMENT_STATUS_CHOICES = [
    ("initiated", "Initiated"),
    ("pending",   "Pending"),
    ("success",   "Success"),
    ("failed",    "Failed"),
    ("timeout",   "Timeout"),
    ("cancelled", "Cancelled"),
]


# ------------------------------------------------------------
# Bill
# ------------------------------------------------------------
class Bill(models.Model):
    """
    The invoice for a Visit. Created as soon as the visit is
    registered; line items are added as services are ordered.
    """

    visit = models.OneToOneField(
        "clinical.Visit",
        on_delete=models.CASCADE,
        related_name="bill",
    )
    status = models.CharField(
        max_length=10,
        choices=BILL_STATUS_CHOICES,
        default="open",
        db_index=True,
    )

    # ---- Totals (denormalized for speed; computed on every save) ----
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )
    sha_covered_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
        help_text="Amount the SHA claim is expected to cover.",
    )

    # ---- Timestamps ----
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Bill"
        verbose_name_plural = "Bills"
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Bill #{self.pk} · {self.visit.patient.sid} · {self.status}"

    # ---------------- Money math ----------------
    @property
    def balance_due(self):
        return (self.total_amount or Decimal("0")) - (self.paid_amount or Decimal("0"))

    @property
    def is_settled(self):
        return self.balance_due <= 0 and self.status == "settled"

    def recompute_totals(self, save=True):
        """
        Recompute total from line items + paid from successful payments.
        Called whenever a line item or payment is added.
        """
        from django.db.models import Sum

        line_total = (
            self.line_items.aggregate(t=Sum("amount"))["t"]
            or Decimal("0.00")
        )
        paid = (
            self.payments
            .filter(status="success")
            .aggregate(t=Sum("amount"))["t"]
            or Decimal("0.00")
        )
        self.total_amount = line_total
        self.paid_amount = paid

        if paid >= line_total and line_total > 0:
            self.status = "settled"
            if not self.settled_at:
                self.settled_at = timezone.now()
        elif paid > 0:
            self.status = "partial"
        else:
            self.status = "open"

        if save:
            self.save(update_fields=[
                "total_amount", "paid_amount", "status", "settled_at", "updated_at"
            ])

    def add_line_item(self, label, amount, kind="other", **kwargs):
        """
        Convenience: create a LineItem and recompute totals.
        """
        item = LineItem.objects.create(
            bill=self,
            label=label,
            amount=Decimal(amount),
            kind=kind,
            **kwargs,
        )
        self.recompute_totals()
        return item


# ------------------------------------------------------------
# LineItem
# ------------------------------------------------------------
class LineItem(models.Model):
    """
    One charge on a bill.
    """

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    kind = models.CharField(
        max_length=20,
        choices=LINE_ITEM_KIND_CHOICES,
        default="other",
    )
    label = models.CharField(
        max_length=150,
        help_text="Human-readable label, e.g. 'Consultation', 'Drug: paracetamol 500mg'.",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    # ---- Optional links to source objects ----
    lab_test = models.ForeignKey(
        "laboratory.LabTest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="line_items",
    )
    prescription = models.ForeignKey(
        "clinical.Prescription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="line_items",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Line item"
        verbose_name_plural = "Line items"

    def __str__(self):
        return f"{self.label} — Ksh {self.amount}"


# ------------------------------------------------------------
# Payment
# ------------------------------------------------------------
class Payment(models.Model):
    """
    One payment attempt. Most are M-Pesa STK Push, some are cash.

    Partial payments are naturally supported: a Bill can have
    many Payments. `Bill.recompute_totals()` sums only the
    successful ones.
    """

    bill = models.ForeignKey(
        Bill,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    method = models.CharField(
        max_length=10,
        choices=PAYMENT_METHOD_CHOICES,
        default="mpesa",
        db_index=True,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    status = models.CharField(
        max_length=10,
        choices=PAYMENT_STATUS_CHOICES,
        default="initiated",
        db_index=True,
    )

    # ---- M-Pesa linkage (nullable — cash payments leave these blank) ----
    mpesa_transaction = models.ForeignKey(
        "mpesa.MPesaTransaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    mpesa_receipt = models.CharField(
        max_length=30,
        blank=True,
        db_index=True,
        help_text="M-Pesa receipt number, e.g. 'QK12345678'.",
    )

    # ---- SHA linkage ----
    sha_claim = models.ForeignKey(
        "sha.SHAClaim",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )

    # ---- Meta ----
    initiated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments_initiated",
    )

    class Meta:
        ordering = ["-initiated_at"]
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        indexes = [
            models.Index(fields=["bill", "status"]),
            models.Index(fields=["method", "status"]),
        ]

    def __str__(self):
        return f"{self.get_method_display()} Ksh {self.amount} [{self.status}]"

    def mark_success(self, receipt=""):
        self.status = "success"
        self.confirmed_at = timezone.now()
        if receipt:
            self.mpesa_receipt = receipt
        self.save(update_fields=["status", "confirmed_at", "mpesa_receipt"])
        self.bill.recompute_totals()

    def mark_failed(self, reason=""):
        self.status = "failed"
        self.failure_reason = reason[:255]
        self.save(update_fields=["status", "failure_reason"])


# ------------------------------------------------------------
# Receipt
# ------------------------------------------------------------
class Receipt(models.Model):
    """
    Official receipt. Created only when a Bill is fully settled.

    `pdf_url` points to a Supabase Storage URL once the PDF has
    been generated (Phase 6).
    """

    bill = models.OneToOneField(
        Bill,
        on_delete=models.PROTECT,
        related_name="receipt",
    )
    receipt_number = models.CharField(
        max_length=30,
        unique=True,
        db_index=True,
        help_text="Format: RCPT-MCYYYYNNNN-XXXXX.",
    )
    total_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    sha_covered = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )

    # ---- PDF (Supabase Storage) ----
    pdf_url = models.URLField(
        blank=True,
        max_length=500,
        help_text="Supabase Storage URL to the generated PDF.",
    )

    issued_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receipts_issued",
    )

    class Meta:
        ordering = ["-issued_at"]
        verbose_name = "Receipt"
        verbose_name_plural = "Receipts"

    def __str__(self):
        return f"{self.receipt_number} · Ksh {self.total_paid}"

    @staticmethod
    def generate_number(patient_sid):
        """
        Generate a unique receipt number.
        Format: RCPT-MC20240001-XXXXX (5 random digits).
        """
        import random
        digits = "".join(str(random.randint(0, 9)) for _ in range(5))
        base = patient_sid.replace("-", "") if patient_sid else "MC"
        return f"RCPT-{base}-{digits}"