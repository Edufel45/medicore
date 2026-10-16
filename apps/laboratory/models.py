"""
MediCore — Laboratory Models
============================================================
Lab test orders and their results.

Models:
  - LabTest    : a test the doctor ordered (name, price, status)
  - LabResult  : the actual result entered by the lab technician

Design:
  - LabTest carries the *order* (what the doctor asked for).
  - LabResult is one-to-one with LabTest — one result per test.
  - The lab releases results by setting LabTest.released_at. When
    that's set, the visit moves back to the doctor.

Proposal reference:
  - §2.3 "consultation notes and diagnosis recording" (order flow)
  - §2.3 "pharmacy and inventory" (cost flows to the bill)
  - The pricing here feeds the pharmacy bill at checkout.
"""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


# ------------------------------------------------------------
# Choices
# ------------------------------------------------------------
LAB_STATUS_CHOICES = [
    ("ordered",   "Ordered"),
    ("collected", "Sample collected"),
    ("running",   "Running"),
    ("ready",     "Result ready"),
    ("released",  "Released to doctor"),
    ("cancelled", "Cancelled"),
]


# ------------------------------------------------------------
# LabTest
# ------------------------------------------------------------
class LabTest(models.Model):
    """
    A single lab test ordered by the doctor for a visit.

    Many LabTests can exist per visit (e.g. Malaria + Urinalysis).
    When the lab technician releases results, `status` becomes
    'released' and `released_at` is stamped — the doctor's inbox
    picks the visit up again.
    """

    visit = models.ForeignKey(
        "clinical.Visit",
        on_delete=models.CASCADE,
        related_name="lab_tests",
    )
    name = models.CharField(
        max_length=80,
        help_text="Test name, e.g. 'Malaria test', 'Urinalysis'.",
    )
    price_ksh = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Charged when the test is ordered. Added to the bill.",
    )

    # ---- Status ----
    status = models.CharField(
        max_length=12,
        choices=LAB_STATUS_CHOICES,
        default="ordered",
        db_index=True,
    )
    ordered_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lab_tests_ordered",
    )

    # ---- Release ----
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the lab tech releases results to the doctor.",
    )
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lab_tests_released",
    )

    class Meta:
        ordering = ["ordered_at"]
        verbose_name = "Lab test"
        verbose_name_plural = "Lab tests"
        indexes = [
            models.Index(fields=["visit", "status"]),
            models.Index(fields=["status", "ordered_at"]),
        ]

    def __str__(self):
        return f"{self.name} · {self.visit} [{self.get_status_display()}]"

    @property
    def is_released(self):
        return self.status == "released" and self.released_at is not None


# ------------------------------------------------------------
# LabResult
# ------------------------------------------------------------
class LabResult(models.Model):
    """
    The result of a LabTest. One-to-one with LabTest.

    `value` is the raw text entered by the lab technician, e.g.
    "Negative", "Positive", "5.4 mmol/L", "Normal".

    In a more advanced system we'd have structured fields per test
    type (analyte, unit, reference range). For this system free
    text is enough and matches the prototype.
    """

    lab_test = models.OneToOneField(
        LabTest,
        on_delete=models.CASCADE,
        related_name="result",
    )
    value = models.TextField(
        help_text="Free-text result, e.g. 'Negative', '5.4 mmol/L'.",
    )
    notes = models.TextField(
        blank=True,
        help_text="Optional comments from the lab technician.",
    )

    # ---- Entry metadata ----
    entered_at = models.DateTimeField(auto_now_add=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lab_results_entered",
    )

    class Meta:
        verbose_name = "Lab result"
        verbose_name_plural = "Lab results"

    def __str__(self):
        return f"Result for {self.lab_test.name}: {self.value[:30]}"