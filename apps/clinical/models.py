"""
MediCore — Clinical Models
============================================================
The clinical loop. Everything the doctor, nurse, and pharmacist
touch during a patient's visit.

Models:
  - Visit        : one visit (arrival → discharge). Owns the queue number.
  - Vitals       : nurse-recorded measurements. Optional.
  - Diagnosis    : doctor's diagnosis. One per visit.
  - Prescription : doctor → pharmacy drug orders. Many per visit.

Proposal reference:
  - §2.3 "consultation notes and diagnosis recording"
  - §2.3 "prescription generation linked to pharmacy inventory"
  - §2.3 "patient queue management for OPD, triage, and doctor
    consultation"
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


# ------------------------------------------------------------
# Choices
# ------------------------------------------------------------
STAGE_CHOICES = [
    ("reg",       "Registration"),
    ("nurse",     "Nurse / Vitals"),
    ("doctor",    "Doctor"),
    ("lab",       "Laboratory"),
    ("pharmacy",  "Pharmacy"),
    ("done",      "Completed"),
]


# ------------------------------------------------------------
# Visit
# ------------------------------------------------------------
class Visit(models.Model):
    """
    One visit. A patient can have many visits over time.
    The Visit is what moves through the queue (reg → nurse →
    doctor → lab → pharmacy → done).

    A Visit is "cleared" when it reaches `stage == "done"` — at
    that point it's no longer in any counter's active list.
    """

    # ---- Identity ----
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.PROTECT,
        related_name="visits",
        help_text="The patient this visit belongs to.",
    )

    # ---- Queue ----
    queue_number = models.CharField(
        max_length=10,
        db_index=True,
        help_text="Daily queue number, e.g. A-015. Resets each day.",
    )
    stage = models.CharField(
        max_length=10,
        choices=STAGE_CHOICES,
        default="reg",
        db_index=True,
        help_text="Current counter. Drives the rail + queue board.",
    )

    # ---- Context ----
    reason_for_visit = models.CharField(
        max_length=255,
        blank=True,
        help_text="Snapshot of the reason at the time of this visit.",
    )

    # ---- Registration fee (paid at reception) ----
    registration_fee_paid = models.BooleanField(
        default=False,
        help_text="True once Ksh 200 has been collected at reception.",
    )
    registration_fee_paid_at = models.DateTimeField(null=True, blank=True)

    # ---- Timestamps ----
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when stage becomes 'done'. Used to archive.",
    )

    # ---- Who created it ----
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visits_created",
    )

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Visit"
        verbose_name_plural = "Visits"
        indexes = [
            models.Index(fields=["stage", "-started_at"]),
            models.Index(fields=["patient", "-started_at"]),
            models.Index(fields=["queue_number"]),
        ]

    def __str__(self):
        return f"{self.queue_number} · {self.patient.sid} ({self.get_stage_display()})"

    # ---------------- Properties ----------------
    @property
    def is_active(self):
        """True while the visit is still in a counter."""
        return self.stage != "done"

    @property
    def is_completed(self):
        return self.stage == "done"

    @property
    def next_queue_number(self):
        """
        Compute the next daily queue number, e.g. A-015 → A-016.
        Resets to A-001 when the day changes.
        """
        today = timezone.localdate()
        last = (
            Visit.objects
            .filter(started_at__date=today)
            .order_by("-id")
            .values_list("queue_number", flat=True)
            .first()
        )
        if not last:
            return "A-001"
        try:
            n = int(last.split("-")[1])
        except (ValueError, IndexError):
            n = 0
        return f"A-{n + 1:03d}"

    @classmethod
    def generate_queue_number(cls):
        """Static helper for the registration view."""
        today = timezone.localdate()
        last = (
            cls.objects
            .filter(started_at__date=today)
            .order_by("-id")
            .values_list("queue_number", flat=True)
            .first()
        )
        if not last:
            return "A-001"
        try:
            n = int(last.split("-")[1])
        except (ValueError, IndexError):
            n = 0
        return f"A-{n + 1:03d}"


# ------------------------------------------------------------
# Vitals
# ------------------------------------------------------------
class Vitals(models.Model):
    """
    Nurse-recorded vitals. Optional per visit — the doctor requests
    them, the nurse fills them, they go back to the doctor.

    One-to-one with Visit. Empty string is allowed for optional
    fields so the nurse can save partial data.
    """

    visit = models.OneToOneField(
        Visit,
        on_delete=models.CASCADE,
        related_name="vitals",
    )

    blood_pressure = models.CharField(
        max_length=15,
        blank=True,
        help_text="e.g. 120/80",
    )
    temperature_c = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Temperature in Celsius, e.g. 36.8",
    )
    weight_kg = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Weight in kilograms.",
    )
    pulse_bpm = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Pulse in beats per minute.",
    )

    # ---- Requested-by-doctor flag ----
    requested_by_doctor = models.BooleanField(
        default=False,
        help_text="True if the doctor explicitly asked for vitals.",
    )

    # ---- Timestamps ----
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vitals_recorded",
    )

    class Meta:
        verbose_name = "Vitals"
        verbose_name_plural = "Vitals"

    def __str__(self):
        return f"Vitals for {self.visit}"


# ------------------------------------------------------------
# Diagnosis
# ------------------------------------------------------------
class Diagnosis(models.Model):
    """
    Doctor's diagnosis for a visit.
    One-to-one with Visit — one diagnosis per visit is the norm,
    but the field is free text and can hold multi-line notes.
    """

    visit = models.OneToOneField(
        Visit,
        on_delete=models.CASCADE,
        related_name="diagnosis",
    )
    text = models.TextField(
        blank=True,
        help_text="Diagnosis or ICD-10 code, free text.",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diagnoses_recorded",
    )

    class Meta:
        verbose_name = "Diagnosis"
        verbose_name_plural = "Diagnoses"

    def __str__(self):
        return f"Diagnosis for {self.visit}"


# ------------------------------------------------------------
# Prescription
# ------------------------------------------------------------
class Prescription(models.Model):
    """
    A drug the doctor has prescribed. Many per visit.

    The doctor types a drug name (free text). The pharmacist's
    system resolves it against the Drug catalogue to get a price.

    Design: we store BOTH the free-text `drug_name_typed` (what the
    doctor actually typed) AND a FK to `pharmacy.Drug` (resolved by
    the pharmacist). This preserves the doctor's intent even when
    the name was misspelled.

    Proposal reference: §2.3 "prescription generation linked to
    pharmacy inventory".
    """

    visit = models.ForeignKey(
        Visit,
        on_delete=models.CASCADE,
        related_name="prescriptions",
    )
    drug_name_typed = models.CharField(
        max_length=120,
        help_text="Exactly what the doctor typed (free text).",
    )
    drug = models.ForeignKey(
        "pharmacy.Drug",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescriptions",
        help_text="Resolved by the pharmacist. Null until resolved.",
    )

    # ---- Dispensing state ----
    is_dispensed = models.BooleanField(default=False)
    dispensed_at = models.DateTimeField(null=True, blank=True)

    # ---- Who / when ----
    prescribed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescriptions_written",
    )
    prescribed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prescribed_at"]
        verbose_name = "Prescription"
        verbose_name_plural = "Prescriptions"
        indexes = [
            models.Index(fields=["visit", "is_dispensed"]),
        ]

    def __str__(self):
        status = "✓" if self.is_dispensed else "•"
        return f"{status} {self.drug_name_typed} (visit {self.visit_id})"