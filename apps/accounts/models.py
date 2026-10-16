"""
MediCore — Accounts Models
============================================================
Custom user extension + audit log.

  - UserProfile    : one-to-one with Django User, stores role + theme
  - AuditLog       : append-only record of who did what, when

Proposal reference:
  - §2.3 "role-based access control for Admin, Doctor, Nurse,
    Pharmacist, Cashier, and SHA Clerk"
  - §2.3 "audit trails and restricted reversals"
  - §2.1 SHA HMIS "secure data exchange"
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models


# ------------------------------------------------------------
# Role choices
# ------------------------------------------------------------
ROLE_CHOICES = [
    ("reception",  "Receptionist"),
    ("nurse",      "Nurse"),
    ("doctor",     "Doctor"),
    ("lab",        "Lab Technician"),
    ("pharmacy",   "Pharmacist"),
    ("accounts",   "Accountant"),
]


# ------------------------------------------------------------
# UserProfile
# ------------------------------------------------------------
class UserProfile(models.Model):
    """
    Extends Django's built-in User with MediCore-specific fields.
    Created automatically via signal (see signals.py — added later).
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="reception",
        help_text="Primary role for navigation and permissions.",
    )
    phone = models.CharField(max_length=20, blank=True)
    theme_preference = models.CharField(
        max_length=10,
        choices=[("light", "Light"), ("dark", "Dark")],
        default="light",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User profile"
        verbose_name_plural = "User profiles"

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


# ------------------------------------------------------------
# AuditLog
# ------------------------------------------------------------
class AuditLog(models.Model):
    """
    Append-only audit trail. Written by AuditMiddleware for every
    non-GET request made by an authenticated user. Also written
    explicitly by critical actions (payment, claim, dispense).

    Never update or delete rows from this table.
    """

    ACTION_CHOICES = [
        ("login",             "Login"),
        ("logout",            "Logout"),
        ("patient.view",      "Viewed patient"),
        ("patient.register",  "Registered patient"),
        ("patient.update",    "Updated patient"),
        ("patient.clear",     "Cleared patient from counter"),
        ("stage.change",      "Moved patient to another stage"),
        ("payment.reg",       "Collected registration fee"),
        ("payment.pharmacy",  "Collected pharmacy payment"),
        ("claim.create",      "Created SHA claim"),
        ("claim.approve",     "SHA claim approved"),
        ("claim.reject",      "SHA claim rejected"),
        ("claim.pending",     "SHA claim set to pending"),
        ("receipt.open",      "Printed/viewed receipt"),
        ("lab_report.print",  "Printed lab report"),
        ("summary.print",     "Printed daily summary"),
        ("audit.export",      "Exported audit CSV"),
        ("drug.price.set",    "Set drug price"),
        ("rx.add",            "Added prescription"),
        ("rx.remove",         "Removed prescription"),
        ("other",             "Other action"),
    ]

    # Who
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    role = models.CharField(max_length=20, blank=True)

    # What
    action = models.CharField(
        max_length=40,
        choices=ACTION_CHOICES,
        default="other",
    )
    detail = models.CharField(max_length=500, blank=True)

    # Where
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    path = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=8, blank=True)

    # Optional FK to a patient (System ID), so we can filter by patient
    patient_sid = models.CharField(max_length=20, blank=True, db_index=True)

    # When
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit log entry"
        verbose_name_plural = "Audit log"
        indexes = [
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["patient_sid", "-created_at"]),
        ]

    def __str__(self):
        who = self.user.username if self.user else "system"
        when = self.created_at.strftime("%Y-%m-%d %H:%M")
        return f"[{when}] {who} — {self.get_action_display()}"