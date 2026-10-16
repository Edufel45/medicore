"""
MediCore — Patients Models
============================================================
The Patient is the core entity. Every Visit, Bill, LabTest,
Prescription, and MPesaTransaction links back to a Patient.

Key fields:
  - sid          : System ID (MC-2024-NNNN) — auto-generated
  - nid          : National ID (Kenya)
  - phone        : M-Pesa phone number
  - sha_number   : SHA membership number
  - sha_status   : Active / Inactive / Pending / Unknown
  - date_of_birth + age_years (age is computed if DOB is set)

Proposal reference:
  - §2.3 "patient management requires registration and demographic
    capture including SHA membership number"
  - §2.3 "electronic medical records with visit history"
  - §2.3 "patient queue management for OPD, triage, and doctor
    consultation"
"""

from datetime import date

from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


# ------------------------------------------------------------
# Validators
# ------------------------------------------------------------
nid_validator = RegexValidator(
    regex=r"^\d{6,12}$",
    message="National ID must be 6 to 12 digits.",
)

phone_validator = RegexValidator(
    regex=r"^(?:\+?254|0)?7\d{8}$|^(?:\+?254|0)?1\d{8}$",
    message="Enter a valid Kenyan phone (e.g. 0712345678 or +254712345678).",
)

sha_number_validator = RegexValidator(
    regex=r"^[A-Z0-9\-]{4,30}$",
    message="SHA number must be 4-30 characters (A-Z, 0-9, dash).",
)


# ------------------------------------------------------------
# Choices
# ------------------------------------------------------------
SEX_CHOICES = [
    ("F", "Female"),
    ("M", "Male"),
    ("O", "Other"),
]

SHA_STATUS_CHOICES = [
    ("active",   "Active"),
    ("inactive", "Inactive"),
    ("pending",  "Pending"),
    ("unknown",  "Unknown"),
]


# ------------------------------------------------------------
# Patient
# ------------------------------------------------------------
class Patient(models.Model):
    """
    A patient. Every interaction with the hospital starts here.

    Never delete a Patient — set `is_active=False` for soft-delete.
    Hard-delete would orphan visits and bills.
    """

    # ---- System ID (MC-YYYY-NNNN) ----
    sid = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        editable=False,
        help_text="System-generated ID. Format: MC-YYYY-NNNN.",
    )

    # ---- Identity ----
    full_name = models.CharField(max_length=120)
    national_id = models.CharField(
        max_length=12,
        validators=[nid_validator],
        db_index=True,
        help_text="Kenyan National ID (6-12 digits).",
    )
    phone = models.CharField(
        max_length=15,
        validators=[phone_validator],
        db_index=True,
        help_text="M-Pesa phone number.",
    )
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, default="F")

    # ---- Age — either DOB (preferred) or a manually entered age ----
    date_of_birth = models.DateField(
        null=True,
        blank=True,
        help_text="Preferred. If set, age is computed automatically.",
    )
    age_years = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Fallback when date_of_birth is unknown.",
    )

    # ---- SHA (Social Health Authority) ----
    sha_number = models.CharField(
        max_length=30,
        blank=True,
        validators=[sha_number_validator],
        db_index=True,
        help_text="SHA membership number (e.g. DEMO-1001).",
    )
    sha_status = models.CharField(
        max_length=10,
        choices=SHA_STATUS_CHOICES,
        default="unknown",
        help_text="Last known SHA eligibility status.",
    )
    sha_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent SHA verification.",
    )

    # ---- Visit context ----
    reason_for_visit = models.CharField(
        max_length=255,
        blank=True,
        help_text="Why the patient came in (free text).",
    )

    # ---- Meta ----
    is_active = models.BooleanField(
        default=True,
        help_text="Soft-delete flag. Inactive patients are hidden from the queue.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patients_created",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Patient"
        verbose_name_plural = "Patients"
        indexes = [
            models.Index(fields=["national_id", "phone"]),
            models.Index(fields=["sha_number"]),
            models.Index(fields=["is_active", "-created_at"]),
        ]
        constraints = [
            # Two ACTIVE patients cannot share the same national ID.
            # (Cleared patients can be re-registered with the same NID.)
            models.UniqueConstraint(
                fields=["national_id"],
                condition=models.Q(is_active=True),
                name="uniq_active_patient_national_id",
            ),
            models.UniqueConstraint(
                fields=["phone"],
                condition=models.Q(is_active=True),
                name="uniq_active_patient_phone",
            ),
        ]

    # ---------------- Methods ----------------
    def __str__(self):
        return f"{self.sid} · {self.full_name}"

    def save(self, *args, **kwargs):
        if not self.sid:
            self.sid = self._generate_sid()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_sid():
        """
        Generate a System ID in the format MC-YYYY-NNNN.
        NNNN resets per year.
        """
        year = timezone.now().year
        prefix = f"MC-{year}-"
        last = (
            Patient.objects
            .filter(sid__startswith=prefix)
            .order_by("-sid")
            .values_list("sid", flat=True)
            .first()
        )
        if last:
            try:
                next_num = int(last.split("-")[-1]) + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1
        return f"{prefix}{next_num:04d}"

    @property
    def age(self):
        """
        Return age in years. Prefers DOB over manual age_years.
        """
        if self.date_of_birth:
            today = date.today()
            years = today.year - self.date_of_birth.year
            if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
                years -= 1
            return years
        return self.age_years

    @property
    def sha_is_active(self):
        return self.sha_status == "active"

    # Convenience for the frontend badge (matches the HTML prototype)
    @property
    def sha_badge_class(self):
        return {
            "active":   "b-active",
            "inactive": "b-inactive",
            "pending":  "b-pending",
            "unknown":  "b-n",
        }.get(self.sha_status, "b-n")