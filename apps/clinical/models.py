"""
MediCore — Clinical Models
============================================================
The clinical loop. Everything the doctor, nurse, and pharmacist
touch during a patient's visit.
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
    """

    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.PROTECT,
        related_name="visits",
    )
    queue_number = models.CharField(
        max_length=10,
        unique=True,
        db_index=True,
        help_text="Sequential queue number, e.g. A-015.",
    )
    stage = models.CharField(
        max_length=10,
        choices=STAGE_CHOICES,
        default="reg",
        db_index=True,
    )
    reason_for_visit = models.CharField(max_length=255, blank=True)

    registration_fee_paid = models.BooleanField(default=False)
    registration_fee_paid_at = models.DateTimeField(null=True, blank=True)

    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

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
        ]

    def __str__(self):
        return f"{self.queue_number} · {self.patient.sid} ({self.get_stage_display()})"

    @property
    def is_active(self):
        return self.stage != "done"

    @property
    def is_completed(self):
        return self.stage == "done"

    @classmethod
    def generate_queue_number(cls):
        """
        Generate the next queue number GLOBALLY (across all time).
        Format: A-001, A-002, ..., A-999, A-1000, A-1001, ...
        """
        last = (
            cls.objects
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
        # Skip any existing numbers to avoid duplicates
        while cls.objects.filter(queue_number=f"A-{n + 1:03d}").exists():
            n += 1
        return f"A-{n + 1:03d}"


# ------------------------------------------------------------
# Vitals
# ------------------------------------------------------------
class Vitals(models.Model):
    visit = models.OneToOneField(
        Visit,
        on_delete=models.CASCADE,
        related_name="vitals",
    )
    blood_pressure = models.CharField(max_length=15, blank=True)
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    pulse_bpm = models.PositiveSmallIntegerField(null=True, blank=True)
    requested_by_doctor = models.BooleanField(default=False)

    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vitals_recorded",
    )

    def __str__(self):
        return f"Vitals for {self.visit}"


# ------------------------------------------------------------
# Diagnosis
# ------------------------------------------------------------
class Diagnosis(models.Model):
    visit = models.OneToOneField(
        Visit,
        on_delete=models.CASCADE,
        related_name="diagnosis",
    )
    text = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diagnoses_recorded",
    )

    def __str__(self):
        return f"Diagnosis for {self.visit}"


# ------------------------------------------------------------
# Prescription
# ------------------------------------------------------------
class Prescription(models.Model):
    visit = models.ForeignKey(
        Visit,
        on_delete=models.CASCADE,
        related_name="prescriptions",
    )
    drug_name_typed = models.CharField(max_length=120)
    drug = models.ForeignKey(
        "pharmacy.Drug",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescriptions",
    )
    is_dispensed = models.BooleanField(default=False)
    dispensed_at = models.DateTimeField(null=True, blank=True)
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

    def __str__(self):
        return f"{self.drug_name_typed} (visit {self.visit_id})"