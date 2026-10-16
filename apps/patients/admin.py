"""
MediCore — Patients Admin
============================================================
"""

from django.contrib import admin
from .models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("sid", "full_name", "national_id", "phone", "sha_status", "is_active", "created_at")
    list_filter = ("sha_status", "is_active", "sex", "created_at")
    search_fields = ("sid", "full_name", "national_id", "phone", "sha_number")
    readonly_fields = ("sid", "created_at", "updated_at", "sha_verified_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    fieldsets = (
        ("Identity", {
            "fields": ("sid", "full_name", "national_id", "phone", "sex")
        }),
        ("Age", {
            "fields": ("date_of_birth", "age_years")
        }),
        ("SHA", {
            "fields": ("sha_number", "sha_status", "sha_verified_at")
        }),
        ("Visit context", {
            "fields": ("reason_for_visit",)
        }),
        ("Meta", {
            "fields": ("is_active", "created_at", "updated_at", "created_by")
        }),
    )