"""
MediCore — SHA Models
============================================================
Social Health Authority integration.

Models:
  - SHAContract : facility's SHA empanelment details
  - SHAClaim    : one insurance claim per visit

Design:
  - SHAClaim is 1-to-1 with Visit — every visit that involves
    SHA gets one claim.
  - The claim lifecycle mirrors the real SHA HMIS flow:
    pending → under_review → approved | rejected → paid
  - All claim submission goes through a provider (mock in dev,
    real SHA HMIS in prod) — see apps/sha/providers.py (Phase 7).

Proposal reference:
  - §1.3.2 II "SHA HMIS integration for real-time patient
    verification and electronic claims submission"
  - §2.3 "electronic claims submission to submit claims digitally
    for reimbursement"
  - §2.3 "claims tracking to track claim status as submitted,
    under review, approved, rejected, or paid"
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


# ------------------------------------------------------------
# Choices
# ------------------------------------------------------------
CLAIM_STATUS_CHOICES = [
    ("draft",         "Draft"),
    ("pending",       "Pending"),
    ("under_review",  "Under review"),
    ("approved",      "Approved"),
    ("rejected",      "Rejected"),
    ("paid",          "Paid"),
]

SHA_VERIFICATION_STATUS_CHOICES = [
    ("active",   "Active"),
    ("inactive", "Inactive"),
    ("pending",  "Pending"),
    ("unknown",  "Unknown"),
]

FUND_TYPE_CHOICES = [
    ("primary",    "Primary Healthcare Fund (L1-3)"),
    ("shif",       "Social Health Insurance Fund (L4-6)"),
    ("emergency",  "Emergency, Chronic & Critical Illness Fund"),
]


# ------------------------------------------------------------
# SHAContract
# ------------------------------------------------------------
class SHAContract(models.Model):
    """
    The facility's SHA empanelment details.

    One row per facility. Stores the facility code, the current
    contract window, and a reference to the benefit package.
    The pricing tier comes from the tariff schedule.
    """

    facility_code = models.CharField(
        max_length=30,
        unique=True,
        help_text="SHA facility code assigned at empanelment.",
    )
    facility_name = models.CharField(max_length=150)
    facility_level = models.CharField(
        max_length=10,
        default="L4",
        help_text="Level 2, 3, 4, 5, or 6.",
    )

    contract_start = models.DateField()
    contract_end = models.DateField()
    is_active = models.BooleanField(default=True)

    # ---- Tariff (contract-level, informs billing) ----
    tariff_consultation_ksh = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    tariff_lab_markup_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Optional markup the contract allows on lab tests.",
    )

    # ---- Meta ----
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SHA contract"
        verbose_name_plural = "SHA contracts"

    def __str__(self):
        return f"{self.facility_name} ({self.facility_code})"


# ------------------------------------------------------------
# SHAClaim
# ------------------------------------------------------------
class SHAClaim(models.Model):
    """
    One SHA claim per visit.

    Created by the pharmacist at the point of payment (or by the
    accountant for back-office claims). The claim lifecycle:

        draft → pending → under_review → approved → paid
                                        ↘ rejected

    Each transition is logged to the AuditLog.
    """

    visit = models.OneToOneField(
        "clinical.Visit",
        on_delete=models.CASCADE,
        related_name="sha_claim",
    )

    # ---- Claim identity ----
    claim_reference = models.CharField(
        max_length=40,
        unique=True,
        db_index=True,
        help_text="Format: SHA-MCYYYYNNNN-XXXX.",
    )

    # ---- SHA number snapshot (in case patient record changes later) ----
    sha_number = models.CharField(max_length=30)
    patient_name = models.CharField(max_length=120)
    patient_national_id = models.CharField(max_length=12, blank=True)

    # ---- Money ----
    claim_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Amount the claim is submitted for.",
    )
    approved_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Amount SHA approved (may differ from claim).",
    )
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )

    # ---- Fund routing (drives which SHA fund pays) ----
    fund_type = models.CharField(
        max_length=12,
        choices=FUND_TYPE_CHOICES,
        default="primary",
        help_text="Which SHA fund covers this claim.",
    )

    # ---- Status ----
    status = models.CharField(
        max_length=15,
        choices=CLAIM_STATUS_CHOICES,
        default="draft",
        db_index=True,
    )
    rejection_reason = models.TextField(
        blank=True,
        help_text="Set by SHA when the claim is rejected.",
    )

    # ---- Timestamps ----
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ---- Ownership ----
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sha_claims_created",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sha_claims_decided",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "SHA claim"
        verbose_name_plural = "SHA claims"
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["claim_reference"]),
        ]

    def __str__(self):
        return f"{self.claim_reference} · {self.patient_name} [{self.status}]"

    # ---------------- Lifecycle helpers ----------------
    @staticmethod
    def generate_reference(patient_sid):
        """Format: SHA-MCYYYYNNNN-XXXX."""
        import random
        suffix = "".join(str(random.randint(0, 9)) for _ in range(4))
        base = patient_sid.replace("-", "") if patient_sid else "MC"
        return f"SHA-{base}-{suffix}"

    def mark_submitted(self):
        self.status = "pending"
        self.submitted_at = timezone.now()
        self.save(update_fields=["status", "submitted_at", "updated_at"])

    def mark_approved(self, approved_amount=None):
        self.status = "approved"
        self.decided_at = timezone.now()
        if approved_amount is not None:
            self.approved_amount = approved_amount
        self.save(update_fields=["status", "decided_at", "approved_amount", "updated_at"])

    def mark_rejected(self, reason=""):
        self.status = "rejected"
        self.decided_at = timezone.now()
        self.rejection_reason = reason[:1000]
        self.save(update_fields=["status", "decided_at", "rejection_reason", "updated_at"])

    def mark_paid(self, paid_amount=None):
        self.status = "paid"
        self.paid_at = timezone.now()
        if paid_amount is not None:
            self.paid_amount = paid_amount
        self.save(update_fields=["status", "paid_at", "paid_amount", "updated_at"])

    # ---------------- UI helpers ----------------
    @property
    def badge_class(self):
        return {
            "draft":        "b-n",
            "pending":      "b-pending",
            "under_review": "b-pending",
            "approved":     "b-active",
            "rejected":     "b-inactive",
            "paid":         "b-active",
        }.get(self.status, "b-n")